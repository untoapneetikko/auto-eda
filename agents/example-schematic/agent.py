"""
Example Schematic Agent — pure tools layer.

Claude Code handles all reasoning. This module provides:
  - get_application_context()  — extract datasheet context for the orchestrator
  - apply_schematic()          — validate and persist a schematic dict produced by
                                 the orchestrator
  - run()                      — stub; raises NotImplementedError
"""

import json
import os
import re
import logging
import subprocess
import sys
from pathlib import Path
from typing import Any

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths (resolved relative to project root via env or defaults)
# ---------------------------------------------------------------------------
_ROOT = Path(os.getenv("PROJECT_ROOT", Path(__file__).resolve().parents[2]))
INPUT_PATH = Path(os.getenv("DATASHEET_JSON", _ROOT / "data" / "outputs" / "datasheet.json"))
OUTPUT_PATH = Path(os.getenv("SCHEMATIC_JSON", _ROOT / "data" / "outputs" / "example_schematic.json"))
SCHEMA_PATH = Path(os.getenv("SCHEMATIC_SCHEMA", _ROOT / "shared" / "schemas" / "schematic_output.json"))
VALIDATOR_PATH = _ROOT / "backend" / "tools" / "schema_validator.py"


# ---------------------------------------------------------------------------
# Internal helpers (kept for validator reuse in tests)
# ---------------------------------------------------------------------------

def _validate_net_names(schematic: dict) -> list[str]:
    """Return a list of violation strings for bad net names."""
    bad_pattern = re.compile(r"^N[ET]*\d+$", re.IGNORECASE)
    violations = []
    for net in schematic.get("nets", []):
        name = net.get("name", "")
        if bad_pattern.match(name):
            violations.append(f"Net '{name}' uses a generic name (NET001-style) — must be descriptive")
    return violations


def _validate_passive_values(schematic: dict) -> list[str]:
    """Return violations where passives have missing/empty values."""
    passive_refs = re.compile(r"^[RCLD]\d", re.IGNORECASE)
    violations = []
    for comp in schematic.get("components", []):
        ref = comp.get("reference", "")
        if passive_refs.match(ref):
            value = comp.get("value", "").strip()
            if not value or value.lower() in ("", "?", "unknown", "tbd"):
                violations.append(f"Passive {ref} has no value — must specify a value (set assumed:true if guessed)")
    return violations


def _schema_validate(output_path: Path) -> None:
    """Run schema_validator.py as a subprocess for authoritative validation."""
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    result = subprocess.run(
        [sys.executable, str(VALIDATOR_PATH), str(output_path), str(SCHEMA_PATH)],
        capture_output=True,
        text=True,
        env=env,
    )
    if result.returncode != 0:
        log.error("Schema validation FAILED:\n%s\n%s", result.stdout, result.stderr)
        raise ValueError(f"Schema validation failed for {output_path}")
    log.info("Schema validation passed: %s", result.stdout.strip())


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_application_context(datasheet_path: Path = INPUT_PATH) -> dict:
    """
    Read datasheet.json and extract the context an orchestrator needs to build
    an example schematic.

    Returns a dict with:
      - component_name  (str)
      - package         (str)
      - example_application  (dict — the full example_application section)

    Raises FileNotFoundError if datasheet_path does not exist.
    """
    if not datasheet_path.exists():
        raise FileNotFoundError(f"Datasheet not found: {datasheet_path}")

    with open(datasheet_path) as f:
        datasheet = json.load(f)

    return {
        "component_name": datasheet.get("component_name", ""),
        "package": datasheet.get("package", ""),
        "example_application": datasheet.get("example_application", {}),
    }


def apply_schematic(schematic_dict: dict, output_dir: Path | None = None) -> dict:
    """
    Validate a schematic dict produced by the orchestrator and write it to disk.

    Steps:
      1. Run _validate_net_names() and _validate_passive_values().
      2. Enforce format == 'kicad_sch'.
      3. Write to output_dir/example_schematic.json (default: OUTPUT_PATH).
      4. Run authoritative schema_validator.py subprocess.
      5. Return {success, errors, netlist_summary}.

    Parameters
    ----------
    schematic_dict : dict
        The schematic produced by the orchestrator.
    output_dir : Path or None
        Directory to write example_schematic.json into.
        Defaults to OUTPUT_PATH.parent.

    Returns
    -------
    dict with keys:
      success        (bool)
      errors         (list[str])   — empty on success
      netlist_summary (str)        — human-readable netlist from netlist_builder
    """
    from netlist_builder import build_netlist_summary  # local import avoids circular issues

    errors: list[str] = []

    # 1. Custom validation checks
    net_violations = _validate_net_names(schematic_dict)
    value_violations = _validate_passive_values(schematic_dict)
    errors.extend(net_violations)
    errors.extend(value_violations)

    # 2. Enforce format field
    schematic_dict = dict(schematic_dict)
    schematic_dict["format"] = "kicad_sch"

    # 3. Write to disk
    out_path = (Path(output_dir) / "example_schematic.json") if output_dir else OUTPUT_PATH
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(schematic_dict, f, indent=2)
    log.info("Wrote schematic to %s", out_path)

    # 4. Schema validation
    try:
        _schema_validate(out_path)
    except ValueError as exc:
        errors.append(str(exc))
        return {
            "success": False,
            "errors": errors,
            "netlist_summary": "",
        }

    # 5. Build netlist summary
    netlist_summary = build_netlist_summary(schematic_dict)

    if errors:
        log.warning("apply_schematic completed with %d warning(s): %s", len(errors), errors)

    return {
        "success": True,
        "errors": errors,
        "netlist_summary": netlist_summary,
    }


def run(*args: Any, **kwargs: Any) -> None:
    """Deprecated. Claude Code handles reasoning — use get_application_context() + apply_schematic()."""
    raise NotImplementedError(
        "run() is no longer implemented. "
        "Use get_application_context() to read context and apply_schematic() to persist results."
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Example Schematic Agent — tools layer")
    subparsers = parser.add_subparsers(dest="command")

    ctx_parser = subparsers.add_parser("context", help="Print application context from datasheet.json")
    ctx_parser.add_argument("--datasheet", default=str(INPUT_PATH), help="Path to datasheet.json")

    args = parser.parse_args()

    if args.command == "context":
        ctx = get_application_context(Path(args.datasheet))
        print(json.dumps(ctx, indent=2))
    else:
        parser.print_help()
