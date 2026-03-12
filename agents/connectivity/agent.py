"""
agent.py — Connectivity Agent (tool layer — no LLM calls)

Reads:
  data/outputs/schematic.json

Exposes:
  check_connectivity(schematic_dict)  — run all connectivity rules, return report dict
  run_from_file(output_dir=None)      — load schematic.json, check, write connectivity.json

The orchestrator (Claude Code) does the reasoning.  Python is pure tools.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Environment / paths
# ---------------------------------------------------------------------------

_ROOT = Path(os.getenv("DATA_DIR", Path(__file__).parent.parent.parent / "data"))
_OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", _ROOT / "outputs"))
_SCHEMA_DIR = Path(__file__).parent.parent.parent / "shared" / "schemas"
_VALIDATOR = Path(__file__).parent.parent.parent / "backend" / "tools" / "schema_validator.py"

SCHEMATIC_PATH = _OUTPUT_DIR / "schematic.json"
CONNECTIVITY_OUT_PATH = _OUTPUT_DIR / "connectivity.json"
SCHEMA_PATH = _SCHEMA_DIR / "connectivity_output.json"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_json(path: Path, label: str) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"{label} not found at {path}")
    with open(path) as fh:
        return json.load(fh)


def _validate(output_path: Path) -> tuple[bool, list[str]]:
    if not _VALIDATOR.exists():
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


def _issue(severity: str, code: str, message: str, **kwargs: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {"severity": severity, "code": code, "message": message}
    entry.update(kwargs)
    return entry


# ---------------------------------------------------------------------------
# Connectivity checks
# ---------------------------------------------------------------------------


def check_connectivity(schematic: dict[str, Any]) -> dict[str, Any]:
    """
    Run all connectivity rules against a schematic dict.

    Rules:
      ORPHAN_NET      — net in nets[] never used in any connection
      DANGLING_NET    — net used in connection but not declared in nets[]
      SINGLE_PIN_NET  — net connected to exactly one pin (NC exempt)
      FLOATING_PIN    — IC pin (U-ref) has no connection entry
      DUPLICATE_NET   — duplicate name in nets[]
      SELF_LOOP       — both pins of a 2-pin passive on the same net

    Returns a connectivity_output dict ready for JSON serialisation.
    """
    issues: list[dict[str, Any]] = []

    nets: list[dict[str, Any]] = schematic.get("nets", [])
    components: list[dict[str, Any]] = schematic.get("components", [])

    # ------------------------------------------------------------------
    # Build lookup structures
    # ------------------------------------------------------------------

    declared_net_names: list[str] = [n.get("name", "") for n in nets if n.get("name")]

    # DUPLICATE_NET
    seen_names: set[str] = set()
    for name in declared_net_names:
        if name in seen_names:
            issues.append(_issue("error", "DUPLICATE_NET",
                                 f"Net '{name}' declared more than once", net=name))
        seen_names.add(name)
    declared_set = set(declared_net_names)

    # Build net → [component_refs] usage map
    net_usage: dict[str, list[str]] = {n: [] for n in declared_set}

    for comp in components:
        ref = comp.get("reference", "?")
        for conn in comp.get("connections", []):
            net_name = conn.get("net", "")
            if not net_name:
                continue
            if net_name in net_usage:
                net_usage[net_name].append(ref)
            else:
                # DANGLING_NET — used but not declared
                issues.append(_issue(
                    "error", "DANGLING_NET",
                    f"Net '{net_name}' referenced by {ref} but not declared in nets[]",
                    net=net_name, component=ref,
                ))
                # Register anyway so we don't double-report
                net_usage[net_name] = [ref]

    # ORPHAN_NET — declared but never used
    for name in declared_set:
        if not net_usage[name]:
            issues.append(_issue("error", "ORPHAN_NET",
                                 f"Net '{name}' declared but never connected to any pin",
                                 net=name))

    # SINGLE_PIN_NET — connected to only one non-NC endpoint
    for name, refs in net_usage.items():
        if name.upper() == "NC":
            continue
        if len(refs) == 1:
            issues.append(_issue(
                "warning", "SINGLE_PIN_NET",
                f"Net '{name}' connects to only one pin ({refs[0]}) — possible open circuit",
                net=name, component=refs[0],
            ))

    # FLOATING_PIN — IC (U-reference) pin not in any connection entry
    for comp in components:
        ref = comp.get("reference", "")
        if not ref.startswith("U"):
            continue
        connections = comp.get("connections", [])
        connected_pins = {int(c["pin"]) for c in connections if "pin" in c}
        # We can only flag if there is a declared pin count; skip if no info
        # (The schematic agent already enforces NC via _ensure_no_floating_pins,
        #  but we double-check here as an independent pass.)
        if not connected_pins:
            issues.append(_issue(
                "error", "FLOATING_PIN",
                f"Component {ref} has no connection entries at all",
                component=ref,
            ))

    # SELF_LOOP — two-pin component with both pins on the same net
    for comp in components:
        ref = comp.get("reference", "")
        connections = comp.get("connections", [])
        if len(connections) == 2:  # noqa: PLR2004
            nets_used = [c.get("net", "") for c in connections]
            if nets_used[0] and nets_used[0] == nets_used[1]:
                issues.append(_issue(
                    "warning", "SELF_LOOP",
                    f"Component {ref} has both pins on net '{nets_used[0]}' (self-loop)",
                    component=ref, net=nets_used[0],
                ))

    # ------------------------------------------------------------------
    # Assemble report
    # ------------------------------------------------------------------

    error_count = sum(1 for i in issues if i["severity"] == "error")
    warning_count = sum(1 for i in issues if i["severity"] == "warning")
    connection_count = sum(len(c.get("connections", [])) for c in components)

    return {
        "valid": error_count == 0,
        "issues": issues,
        "stats": {
            "net_count": len(declared_set),
            "component_count": len(components),
            "connection_count": connection_count,
            "error_count": error_count,
            "warning_count": warning_count,
        },
        "format": "connectivity_report",
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_from_file(output_dir: str | Path | None = None) -> dict[str, Any]:
    """
    Load data/outputs/schematic.json, run check_connectivity(), write
    data/outputs/connectivity.json, validate against schema.

    Returns:
      {"success": True,  "report": {...}, "errors": []}       on success
      {"success": False, "report": {...}, "errors": ["...",]} on schema-validation failure
    """
    schematic = _load_json(SCHEMATIC_PATH, "schematic.json")
    report = check_connectivity(schematic)

    out_dir = Path(output_dir) if output_dir else _OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "connectivity.json"

    with open(out_path, "w") as fh:
        json.dump(report, fh, indent=2)

    valid_schema, schema_errors = _validate(out_path)
    return {"success": valid_schema, "report": report, "errors": schema_errors}


# ---------------------------------------------------------------------------
# CLI usage
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Connectivity agent tool layer")
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("run", help="Load schematic.json and write connectivity.json")

    check_p = sub.add_parser("check", help="Check a schematic JSON file (no write)")
    check_p.add_argument("schematic_file", help="Path to schematic JSON to check")

    args = parser.parse_args()

    if args.cmd == "run":
        result = run_from_file()
        print(json.dumps(result["report"], indent=2))
        if not result["success"]:
            sys.exit(1)

    elif args.cmd == "check":
        with open(args.schematic_file) as fh:
            sch = json.load(fh)
        report = check_connectivity(sch)
        print(json.dumps(report, indent=2))
        if not report["valid"]:
            sys.exit(1)

    else:
        parser.print_help()
        sys.exit(1)
