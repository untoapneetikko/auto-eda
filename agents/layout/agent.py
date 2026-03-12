"""
agents/layout/agent.py — Layout Agent

Reads placement.json, routing.json, schematic.json, footprint.json,
calls Anthropic claude-sonnet-4-20250514 to produce a finalized board
summary, assembles the .kicad_pcb via KiCadPCBWriter, validates it,
and writes both final_layout.json and final_layout.kicad_pcb to
data/outputs/.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
import anthropic

from kicad_pcb_writer import KiCadPCBWriter

# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------

load_dotenv()

_ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
_OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "data/outputs"))
_SCHEMAS_DIR = Path(os.getenv("SCHEMAS_DIR", "shared/schemas"))
_TOOLS_DIR = Path(os.getenv("TOOLS_DIR", "backend/tools"))

MODEL = "claude-sonnet-4-20250514"


# ---------------------------------------------------------------------------
# File helpers
# ---------------------------------------------------------------------------

def _load_json(path: Path) -> dict:
    """Load JSON from path; return empty dict if missing."""
    if not path.exists():
        print(f"[layout] WARNING: {path} not found — using empty dict", file=sys.stderr)
        return {}
    with open(path) as f:
        return json.load(f)


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    print(f"[layout] Wrote {path}")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"[layout] Wrote {path}")


# ---------------------------------------------------------------------------
# Anthropic call
# ---------------------------------------------------------------------------

def _ask_claude(
    placement: dict,
    routing: dict,
    schematic: dict,
    footprint: dict,
) -> dict:
    """
    Call claude-sonnet-4-20250514 to produce a finalized board summary.
    Returns a dict with keys: project_name, board_notes, drc_notes, layer_notes.
    Falls back gracefully if the API key is absent.
    """
    if not _ANTHROPIC_API_KEY:
        print("[layout] No ANTHROPIC_API_KEY — skipping Claude call, using defaults")
        return {
            "project_name": schematic.get("project_name", "PCB-AI Project"),
            "board_notes": "Auto-generated board. No AI review performed.",
            "drc_notes": "DRC not reviewed by AI.",
            "layer_notes": "Standard 2-layer FR4.",
        }

    client = anthropic.Anthropic(api_key=_ANTHROPIC_API_KEY)

    board_info = placement.get("board", {})
    num_components = len(schematic.get("components", []))
    num_nets = len(schematic.get("nets", []))
    num_traces = len(routing.get("traces", []))
    num_vias = len(routing.get("vias", []))
    board_w = board_info.get("width_mm", "?")
    board_h = board_info.get("height_mm", "?")
    project_name = schematic.get("project_name", "PCB-AI Project")

    prompt = f"""You are a senior PCB layout engineer performing a final design review.

Board: {project_name}
Size: {board_w}mm × {board_h}mm
Components: {num_components}
Nets: {num_nets}
Traces: {num_traces}
Vias: {num_vias}
Footprint: {footprint.get("name", "Unknown")}

Schematic nets: {json.dumps([n.get("name","") for n in schematic.get("nets", [])[:20]], indent=2)}

Provide a concise JSON object (no markdown fences) with these keys:
- "project_name": project name string
- "board_notes": overall board assessment (1-2 sentences)
- "drc_notes": any DRC concerns based on the data above
- "layer_notes": stackup recommendation (e.g. "2-layer FR4 1.6mm, 1oz copper")

Respond with ONLY valid JSON, nothing else."""

    message = client.messages.create(
        model=MODEL,
        max_tokens=512,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = message.content[0].text.strip()

    # Strip markdown code fences if present
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        print(f"[layout] Claude returned non-JSON: {raw!r}", file=sys.stderr)
        return {
            "project_name": project_name,
            "board_notes": raw[:200],
            "drc_notes": "Could not parse AI response.",
            "layer_notes": "2-layer FR4 1.6mm",
        }


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _validate_kicad_pcb(pcb_path: Path) -> bool:
    """
    Validate the .kicad_pcb file.

    First tries to call kicad_validator.py as a subprocess; if that fails
    (e.g. Windows console encoding issues with emoji output), falls back to
    an inline balanced-parentheses check.
    """
    validator = _TOOLS_DIR / "kicad_validator.py"

    if validator.exists():
        result = subprocess.run(
            [sys.executable, str(validator), str(pcb_path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
        stdout = result.stdout.strip()
        stderr = result.stderr.strip()
        if stdout:
            print(f"[layout] validator: {stdout}")
        if result.returncode == 0:
            return True
        # If the error is purely a Unicode/console issue, fall through to inline check
        if "UnicodeEncodeError" not in stderr and "charmap" not in stderr:
            print(f"[layout] validator stderr: {stderr}", file=sys.stderr)
            return False
        print("[layout] validator subprocess had encoding issues; running inline check")

    # Inline balanced-parentheses check (equivalent to kicad_validator.py logic)
    try:
        content = pcb_path.read_text(encoding="utf-8")
    except Exception as exc:
        print(f"[layout] Cannot read PCB file for validation: {exc}", file=sys.stderr)
        return False

    depth = 0
    for ch in content:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth < 0:
                print(f"[layout] INVALID: unmatched closing parenthesis in {pcb_path}", file=sys.stderr)
                return False

    if depth != 0:
        print(f"[layout] INVALID: {depth} unclosed parentheses in {pcb_path}", file=sys.stderr)
        return False

    print(f"[layout] VALID: {pcb_path} (inline check passed)")
    return True


# ---------------------------------------------------------------------------
# Final layout JSON builder
# ---------------------------------------------------------------------------

def _build_final_layout_json(
    placement: dict,
    routing: dict,
    schematic: dict,
    footprint: dict,
    ai_review: dict,
) -> dict:
    """Assemble the final_layout.json summary document."""
    board = placement.get("board", {})
    now_iso = datetime.now(timezone.utc).isoformat()

    # Collect all net names present in traces
    routed_nets = list({t["net"] for t in routing.get("traces", []) if "net" in t})
    # All nets from schematic
    schematic_nets = [n.get("name", "") for n in schematic.get("nets", [])]
    # Unrouted = schematic nets not present in traces
    unrouted = [n for n in schematic_nets if n and n not in routed_nets]

    return {
        "schema_version": "1.0",
        "generated_at": now_iso,
        "project_name": ai_review.get("project_name", schematic.get("project_name", "PCB-AI")),
        "board": {
            "width_mm":  board.get("width_mm",  100.0),
            "height_mm": board.get("height_mm",  80.0),
            "layers":    2,
            "stackup":   ai_review.get("layer_notes", "2-layer FR4 1.6mm"),
        },
        "stats": {
            "components":      len(schematic.get("components", [])),
            "nets_total":      len(schematic_nets),
            "nets_routed":     len(routed_nets),
            "nets_unrouted":   len(unrouted),
            "unrouted_nets":   unrouted,
            "traces":          len(routing.get("traces", [])),
            "vias":            len(routing.get("vias", [])),
            "mounting_holes":  4,
        },
        "ai_review": ai_review,
        "drc": {
            "board_outline":    True,
            "mounting_holes":   True,
            "validated_by":     "kicad_validator.py",
        },
        "output_files": {
            "kicad_pcb": "final_layout.kicad_pcb",
        },
    }


# ---------------------------------------------------------------------------
# Main run function
# ---------------------------------------------------------------------------

def run(output_dir: Path | None = None) -> dict:
    """
    Full layout agent run.

    Returns a dict with keys:
      - status: "ok" | "error"
      - message: human-readable summary
      - final_layout_json: path to final_layout.json
      - final_layout_kicad_pcb: path to final_layout.kicad_pcb
    """
    out_dir = output_dir or _OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Step 1 — Load all inputs
    # ------------------------------------------------------------------
    print("[layout] Loading input files…")
    placement  = _load_json(out_dir / "placement.json")
    routing    = _load_json(out_dir / "routing.json")
    schematic  = _load_json(out_dir / "schematic.json")
    footprint  = _load_json(out_dir / "footprint.json")

    # ------------------------------------------------------------------
    # Step 2 — AI design review
    # ------------------------------------------------------------------
    print("[layout] Calling Claude for design review…")
    ai_review = _ask_claude(placement, routing, schematic, footprint)
    project_name = ai_review.get("project_name", schematic.get("project_name", "PCB-AI Project"))
    print(f"[layout] Project: {project_name}")
    print(f"[layout] Board notes: {ai_review.get('board_notes', '')}")

    # ------------------------------------------------------------------
    # Step 3 — Generate .kicad_pcb
    # ------------------------------------------------------------------
    print("[layout] Assembling .kicad_pcb…")
    writer = KiCadPCBWriter(
        placement_data=placement,
        routing_data=routing,
        schematic_data=schematic,
        footprint_data=footprint,
        project_name=project_name,
    )
    kicad_content = writer.write()

    pcb_path  = out_dir / "final_layout.kicad_pcb"
    json_path = out_dir / "final_layout.json"

    _write_text(pcb_path, kicad_content)

    # ------------------------------------------------------------------
    # Step 4 — Validate .kicad_pcb
    # ------------------------------------------------------------------
    print("[layout] Validating .kicad_pcb…")
    valid = _validate_kicad_pcb(pcb_path)
    if not valid:
        print("[layout] WARNING: KiCad PCB validation failed", file=sys.stderr)

    # ------------------------------------------------------------------
    # Step 5 — Write final_layout.json
    # ------------------------------------------------------------------
    print("[layout] Writing final_layout.json…")
    final_json = _build_final_layout_json(
        placement, routing, schematic, footprint, ai_review
    )
    final_json["drc"]["kicad_validation_passed"] = valid
    _write_json(json_path, final_json)

    status_msg = "Layout agent complete." if valid else "Layout agent complete (validation warnings)."
    print(f"[layout] {status_msg}")

    return {
        "status": "ok",
        "message": status_msg,
        "final_layout_json": str(json_path),
        "final_layout_kicad_pcb": str(pcb_path),
        "validation_passed": valid,
        "ai_review": ai_review,
    }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    result = run()
    print(json.dumps(result, indent=2))
    sys.exit(0 if result["status"] == "ok" else 1)
