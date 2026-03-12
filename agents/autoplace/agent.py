"""
Auto-Place Agent
================
Reads data/outputs/schematic.json, calls Claude to determine optimal PCB
component placement, validates the result with the placement optimizer, and
writes data/outputs/placement.json.

Environment variables (loaded from .env):
    ANTHROPIC_API_KEY   — required
    OUTPUT_DIR          — directory for placement.json (default: data/outputs)
    SCHEMATIC_FILE      — path to schematic.json (default: <OUTPUT_DIR>/schematic.json)
    BOARD_WIDTH_MM      — PCB board width in mm  (default: 100.0)
    BOARD_HEIGHT_MM     — PCB board height in mm (default: 80.0)
    MAX_RETRIES         — LLM retry loops on DRC failure (default: 3)
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import anthropic
from dotenv import load_dotenv

# Import from sibling module — works whether run as a module or as a script.
try:
    from agents.autoplace.placement_optimizer import (
        check_courtyard_clearances,
        summarize_violations,
    )
except ModuleNotFoundError:
    # Allow running directly from the agents/autoplace/ directory.
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    from agents.autoplace.placement_optimizer import (
        check_courtyard_clearances,
        summarize_violations,
    )

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

load_dotenv()

log = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [autoplace] %(levelname)s %(message)s",
)

_ROOT = Path(__file__).parent.parent.parent  # pcb-agent-autoplace/

OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", str(_ROOT / "data" / "outputs")))
SCHEMATIC_FILE = Path(os.getenv("SCHEMATIC_FILE", str(OUTPUT_DIR / "schematic.json")))
PLACEMENT_FILE = OUTPUT_DIR / "placement.json"
SCHEMA_FILE = _ROOT / "shared" / "schemas" / "placement_output.json"
DRC_TOOL = _ROOT / "backend" / "tools" / "drc_checker.py"

BOARD_WIDTH_MM: float = float(os.getenv("BOARD_WIDTH_MM", "100.0"))
BOARD_HEIGHT_MM: float = float(os.getenv("BOARD_HEIGHT_MM", "80.0"))
MAX_RETRIES: int = int(os.getenv("MAX_RETRIES", "3"))

MODEL = "claude-sonnet-4-20250514"

# ---------------------------------------------------------------------------
# Prompt helpers
# ---------------------------------------------------------------------------

PLACEMENT_STRATEGY = """\
Placement Strategy (follow in priority order):
1. Connectors first — always on board edge (x near 0 or near board_width, or y near 0 or near board_height)
2. Power components — near power entry point, away from sensitive analog signals
3. Main ICs — center of their functional block
4. Decoupling caps — within 0.5 mm of their IC's power pin
5. Signal passives — adjacent to the pin they serve
6. Mechanical components — per mounting hole constraints

PCB Placement Rules:
- Keep analog and digital grounds separate until single-point join
- High-frequency components: shorter traces = better, cluster tightly
- Thermal relief: power components need 2 mm clearance for airflow
- Test points: accessible from top side, not under components
- Component orientation: all polarized components same orientation where possible
- Courtyard clearance: minimum 0.25 mm between component courtyards
- Every placement MUST include a non-trivial rationale string explaining WHY

Rationale examples:
  BAD:  "placed here"
  GOOD: "Decoupling cap C1 placed within 0.3 mm of U1 pin 4 (VCC) to minimize inductance on power rail"
"""


def _build_system_prompt() -> str:
    return (
        "You are an expert PCB layout engineer. "
        "You place components on a PCB to minimize trace lengths, improve signal integrity, "
        "ensure thermal management, and maximise manufacturability.\n\n"
        + PLACEMENT_STRATEGY
    )


def _build_user_prompt(
    schematic: dict[str, Any],
    board_width: float,
    board_height: float,
    feedback: str | None = None,
) -> str:
    components = schematic.get("components", [])
    nets = schematic.get("nets", [])

    comp_lines = []
    for c in components:
        conns = ", ".join(
            f"pin{conn['pin']}→{conn['net']}" for conn in c.get("connections", [])
        )
        comp_lines.append(
            f"  - {c['reference']} ({c['value']}, footprint: {c.get('footprint','unknown')})"
            f"  connections: [{conns}]"
        )

    net_lines = [
        f"  - {n['name']} ({n.get('type','signal')})"
        for n in nets
    ]

    feedback_section = ""
    if feedback:
        feedback_section = (
            f"\n\n## DRC / Courtyard Feedback (MUST be fixed)\n{feedback}\n"
            "Adjust placements to resolve every violation listed above."
        )

    return f"""\
## Schematic: {schematic.get('project_name', 'PCB Project')}

Board dimensions: {board_width} mm × {board_height} mm  (origin at top-left corner, x right, y down)

### Components to place
{chr(10).join(comp_lines)}

### Nets
{chr(10).join(net_lines)}
{feedback_section}

## Task
Return a JSON object with this exact structure — no markdown fences, no extra keys:

{{
  "board": {{"width_mm": {board_width}, "height_mm": {board_height}}},
  "placements": [
    {{
      "reference": "<REF>",
      "x": <float mm from left edge>,
      "y": <float mm from top edge>,
      "rotation": <0|90|180|270>,
      "layer": "F.Cu",
      "rationale": "<detailed reason for this position>"
    }}
  ]
}}

Rules:
- Every component in the schematic must appear exactly once in "placements".
- x must be in [0, {board_width}], y must be in [0, {board_height}].
- Connectors (reference starting with J or P, or footprint containing CONN/USB/HDR) must have x ≤ 3 or x ≥ {board_width - 3} or y ≤ 3 or y ≥ {board_height - 3}.
- All rationale strings must be specific (mention net names, pin numbers, distances, or design rules).
- Output raw JSON only.
"""


# ---------------------------------------------------------------------------
# JSON extraction
# ---------------------------------------------------------------------------

def _extract_json(text: str) -> dict[str, Any]:
    """Extract the first JSON object from a potentially prose-wrapped response."""
    # Try direct parse first.
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Strip markdown fences.
    fenced = re.sub(r"```(?:json)?", "", text).strip()
    try:
        return json.loads(fenced)
    except json.JSONDecodeError:
        pass

    # Find the outermost { ... } block.
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1:
        return json.loads(text[start : end + 1])

    raise ValueError("No valid JSON object found in LLM response")


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def _validate_structure(data: dict[str, Any], schematic: dict[str, Any]) -> list[str]:
    """Return a list of structural errors (empty list = OK)."""
    errors: list[str] = []

    if "board" not in data:
        errors.append("Missing top-level 'board' key")
    if "placements" not in data:
        errors.append("Missing top-level 'placements' key")
        return errors

    expected_refs = {c["reference"] for c in schematic.get("components", [])}
    placed_refs = {p.get("reference") for p in data["placements"]}

    for ref in expected_refs - placed_refs:
        errors.append(f"Component {ref} missing from placements")

    for ref in placed_refs - expected_refs:
        errors.append(f"Unexpected reference {ref} in placements (not in schematic)")

    board_w = data.get("board", {}).get("width_mm", BOARD_WIDTH_MM)
    board_h = data.get("board", {}).get("height_mm", BOARD_HEIGHT_MM)

    for p in data["placements"]:
        ref = p.get("reference", "?")
        x, y = p.get("x", 0), p.get("y", 0)
        if not (0 <= x <= board_w):
            errors.append(f"{ref}: x={x} out of board bounds [0, {board_w}]")
        if not (0 <= y <= board_h):
            errors.append(f"{ref}: y={y} out of board bounds [0, {board_h}]")
        rationale = p.get("rationale", "")
        if not rationale or rationale.lower() in {"placed here", "here", ""}:
            errors.append(f"{ref}: rationale is trivial or missing")

    return errors


def _run_drc(placement_path: Path) -> tuple[bool, str]:
    """Run the external DRC checker script. Returns (passed, output_text)."""
    result = subprocess.run(
        [sys.executable, str(DRC_TOOL), str(placement_path), "--check", "clearance,courtyard"],
        capture_output=True,
        text=True,
    )
    output = result.stdout + result.stderr
    return result.returncode == 0, output


# ---------------------------------------------------------------------------
# Core placement logic
# ---------------------------------------------------------------------------

def _enrich_placements_with_footprints(
    placements: list[dict[str, Any]],
    schematic: dict[str, Any],
) -> list[dict[str, Any]]:
    """Inject footprint into each placement from the schematic so the optimizer
    can estimate bounding boxes."""
    fp_map = {c["reference"]: c.get("footprint", "") for c in schematic.get("components", [])}
    enriched = []
    for p in placements:
        ep = dict(p)
        ep.setdefault("footprint", fp_map.get(p.get("reference", ""), ""))
        enriched.append(ep)
    return enriched


def run_placement(
    schematic: dict[str, Any] | None = None,
    board_width: float = BOARD_WIDTH_MM,
    board_height: float = BOARD_HEIGHT_MM,
) -> dict[str, Any]:
    """
    Main entry point.  Reads schematic (from file if not provided), calls
    Claude to place components, validates, and writes placement.json.

    Returns the final placement dict.
    """
    # 1. Load schematic
    if schematic is None:
        log.info("Reading schematic from %s", SCHEMATIC_FILE)
        with open(SCHEMATIC_FILE) as f:
            schematic = json.load(f)

    if not schematic.get("components"):
        raise ValueError("Schematic has no components to place")

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    feedback: str | None = None
    last_placement: dict[str, Any] | None = None

    for attempt in range(1, MAX_RETRIES + 1):
        log.info("Placement attempt %d/%d", attempt, MAX_RETRIES)

        # 2. Build prompt
        system_prompt = _build_system_prompt()
        user_prompt = _build_user_prompt(schematic, board_width, board_height, feedback)

        # 3. Call Claude
        log.info("Calling %s …", MODEL)
        message = client.messages.create(
            model=MODEL,
            max_tokens=4096,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        raw_text = message.content[0].text
        log.debug("LLM response (first 500 chars): %s", raw_text[:500])

        # 4. Parse JSON
        try:
            placement_data = _extract_json(raw_text)
        except (ValueError, json.JSONDecodeError) as exc:
            log.warning("JSON parse error on attempt %d: %s", attempt, exc)
            feedback = f"Your previous response could not be parsed as JSON: {exc}. Return raw JSON only."
            continue

        # 5. Structural validation
        struct_errors = _validate_structure(placement_data, schematic)
        if struct_errors:
            feedback = "Structural errors:\n" + "\n".join(f"  - {e}" for e in struct_errors)
            log.warning("Structural validation failed: %s", feedback)
            last_placement = placement_data
            continue

        # 6. Courtyard check via Polars optimizer
        enriched = _enrich_placements_with_footprints(
            placement_data["placements"], schematic
        )
        opt_result = check_courtyard_clearances(enriched)
        if not opt_result["is_valid"]:
            feedback = summarize_violations(opt_result)
            log.warning("Courtyard violations on attempt %d:\n%s", attempt, feedback)
            last_placement = placement_data
            continue

        # All in-process checks passed — write to disk and run external DRC
        last_placement = placement_data
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        with open(PLACEMENT_FILE, "w") as f:
            json.dump(placement_data, f, indent=2)
        log.info("Wrote placement to %s", PLACEMENT_FILE)

        drc_passed, drc_output = _run_drc(PLACEMENT_FILE)
        log.info("DRC output:\n%s", drc_output)
        if drc_passed:
            log.info("DRC PASSED — placement complete.")
            return placement_data
        else:
            feedback = f"External DRC failed:\n{drc_output}"
            log.warning("DRC failed on attempt %d", attempt)

    # Retries exhausted — write best attempt and warn
    if last_placement:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        with open(PLACEMENT_FILE, "w") as f:
            json.dump(last_placement, f, indent=2)
        log.warning(
            "MAX_RETRIES (%d) reached — wrote best-effort placement to %s",
            MAX_RETRIES, PLACEMENT_FILE,
        )
        return last_placement

    raise RuntimeError("Placement failed: no valid response produced by LLM")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Auto-place PCB components using Claude")
    parser.add_argument("--schematic", help="Path to schematic.json (overrides env var)")
    parser.add_argument("--board-width", type=float, default=BOARD_WIDTH_MM)
    parser.add_argument("--board-height", type=float, default=BOARD_HEIGHT_MM)
    args = parser.parse_args()

    sch: dict[str, Any] | None = None
    if args.schematic:
        with open(args.schematic) as f:
            sch = json.load(f)

    result = run_placement(schematic=sch, board_width=args.board_width, board_height=args.board_height)
    print(f"\nPlacement written to {PLACEMENT_FILE}")
    print(f"Components placed: {len(result['placements'])}")
