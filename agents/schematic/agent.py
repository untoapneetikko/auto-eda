"""
agent.py — Schematic Agent (tool layer — no LLM calls)

Reads:
  data/outputs/datasheet.json
  data/outputs/component.json
  data/outputs/example_schematic.json

Exposes:
  get_design_context()  — compact summary for the orchestrator to read before designing
  apply_schematic(schematic_dict, output_dir=None)  — validate + write schematic.json

The orchestrator (Claude Code) does the reasoning.  Python is pure tools.
"""

from __future__ import annotations

import json
import os
import re
import sys
import subprocess
from pathlib import Path
from typing import Any

# Local helpers
from net_namer import suggest_net_names  # noqa: E402

# ---------------------------------------------------------------------------
# Environment / paths
# ---------------------------------------------------------------------------

_ROOT = Path(os.getenv("DATA_DIR", Path(__file__).parent.parent.parent / "data"))
_OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", _ROOT / "outputs"))
_SCHEMA_DIR = Path(__file__).parent.parent.parent / "shared" / "schemas"
_VALIDATOR = Path(__file__).parent.parent.parent / "backend" / "tools" / "schema_validator.py"

DATASHEET_PATH = _OUTPUT_DIR / "datasheet.json"
COMPONENT_PATH = _OUTPUT_DIR / "component.json"
EXAMPLE_SCH_PATH = _OUTPUT_DIR / "example_schematic.json"
SCHEMATIC_OUT_PATH = _OUTPUT_DIR / "schematic.json"
SCHEMA_PATH = _SCHEMA_DIR / "schematic_output.json"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_json(path: Path, label: str) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"{label} not found at {path}")
    with open(path) as fh:
        return json.load(fh)


def _extract_json_block(text: str) -> str:
    """Extract the first ```json ... ``` code fence, or the whole text if none."""
    fence_match = re.search(r"```(?:json)?\s*([\s\S]+?)```", text)
    if fence_match:
        return fence_match.group(1).strip()
    # Try to find a bare { ... } block
    brace_match = re.search(r"(\{[\s\S]+\})", text)
    if brace_match:
        return brace_match.group(1).strip()
    return text.strip()


def _validate(output_path: Path) -> tuple[bool, list[str]]:
    """
    Run schema_validator.py against output_path.

    Returns (valid: bool, errors: list[str]).
    """
    if not _VALIDATOR.exists():
        # No external validator — skip silently
        return True, []

    result = subprocess.run(
        [sys.executable, str(_VALIDATOR), str(output_path), str(SCHEMA_PATH)],
        capture_output=True,
        text=True,
    )
    errors: list[str] = []
    if result.returncode != 0:
        errors = [line for line in (result.stderr + result.stdout).splitlines() if line.strip()]
    return result.returncode == 0, errors


def _ensure_no_floating_pins(
    schematic: dict[str, Any], component: dict[str, Any]
) -> dict[str, Any]:
    """
    For every pin declared in component.json, ensure that the main IC component
    (U1) has a connection entry.  Missing pins are added with net="NC".
    This enforces the no-floating-pins rule even if the orchestrator missed some.
    """
    all_pins: list[dict[str, Any]] = (
        component.get("symbol", {}).get("pins", [])
        or component.get("pins", [])
    )
    if not all_pins:
        return schematic

    all_pin_numbers: set[int] = {int(p["number"]) for p in all_pins}

    for comp in schematic.get("components", []):
        if comp.get("reference", "").startswith("U"):
            existing = {int(c["pin"]) for c in comp.get("connections", [])}
            missing = all_pin_numbers - existing
            for pin_num in sorted(missing):
                comp.setdefault("connections", []).append(
                    {"pin": pin_num, "net": "NC"}
                )
    return schematic


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_design_context() -> dict[str, Any]:
    """
    Read datasheet.json + component.json + example_schematic.json from the
    standard output directory and return a compact combined summary.

    The summary contains:
      - component_name   — human-readable name from component.json
      - pins             — list of {number, name, type, function} from datasheet
      - example_nets     — net names present in example_schematic.json (if available)
      - suggested_nets   — output of net_namer.suggest_net_names(datasheet)

    Raises FileNotFoundError if datasheet.json or component.json are missing.
    example_schematic.json is optional — missing file yields an empty list.
    """
    datasheet = _load_json(DATASHEET_PATH, "datasheet.json")
    component = _load_json(COMPONENT_PATH, "component.json")

    # example_schematic is optional
    example_nets: list[str] = []
    if EXAMPLE_SCH_PATH.exists():
        try:
            example_sch = _load_json(EXAMPLE_SCH_PATH, "example_schematic.json")
            example_nets = [
                n.get("name", "")
                for n in example_sch.get("nets", [])
                if n.get("name")
            ]
        except Exception:  # noqa: BLE001
            example_nets = []

    suggested_nets = suggest_net_names(datasheet)

    # Compact pin list from datasheet
    pins = [
        {
            "number": p.get("number"),
            "name": p.get("name", ""),
            "type": p.get("type", ""),
            "function": p.get("function", ""),
        }
        for p in datasheet.get("pins", [])
    ]

    # Component name: try several common locations
    component_name = (
        component.get("name")
        or component.get("part_number")
        or component.get("symbol", {}).get("name")
        or "Unknown"
    )

    return {
        "component_name": component_name,
        "pins": pins,
        "example_nets": example_nets,
        "suggested_nets": suggested_nets,
    }


def apply_schematic(
    schematic_dict: dict[str, Any],
    output_dir: str | Path | None = None,
) -> dict[str, Any]:
    """
    Accept a schematic dict from the orchestrator, post-process it, validate it
    against the schema, and write data/outputs/schematic.json.

    Steps:
      1. Load component.json to get pin list (needed for floating-pin check).
      2. Run _ensure_no_floating_pins() on the schematic.
      3. Write to disk.
      4. Run schema validation.

    Returns:
      {"success": True, "errors": []}                on success
      {"success": False, "errors": ["...", ...]}     on validation failure
    """
    out_dir = Path(output_dir) if output_dir else _OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "schematic.json"

    # Load component for floating-pin enforcement
    component: dict[str, Any] = {}
    if COMPONENT_PATH.exists():
        try:
            component = _load_json(COMPONENT_PATH, "component.json")
        except Exception:  # noqa: BLE001
            component = {}

    # Post-process
    schematic_dict = _ensure_no_floating_pins(schematic_dict, component)

    # Write
    with open(out_path, "w") as fh:
        json.dump(schematic_dict, fh, indent=2)

    # Validate
    valid, errors = _validate(out_path)
    return {"success": valid, "errors": errors}


# ---------------------------------------------------------------------------
# Legacy entry point — replaced by orchestrator; do not call
# ---------------------------------------------------------------------------


def run(project_brief: str) -> dict[str, Any]:  # noqa: ARG001
    raise NotImplementedError(
        "run() has been removed. The orchestrator (Claude Code) now designs the "
        "schematic. Use get_design_context() to read inputs and apply_schematic() "
        "to write the result."
    )


# ---------------------------------------------------------------------------
# CLI usage
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Schematic agent tool layer")
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("context", help="Print design context as JSON")

    apply_p = sub.add_parser("apply", help="Apply a schematic JSON file")
    apply_p.add_argument("schematic_file", help="Path to schematic JSON to apply")

    args = parser.parse_args()

    if args.cmd == "context":
        ctx = get_design_context()
        print(json.dumps(ctx, indent=2))

    elif args.cmd == "apply":
        with open(args.schematic_file) as fh:
            sch = json.load(fh)
        result = apply_schematic(sch)
        print(json.dumps(result, indent=2))
        if not result["success"]:
            sys.exit(1)

    else:
        parser.print_help()
        sys.exit(1)
