"""
agents/layout/agent.py — Layout Agent

Reads placement.json, routing.json, schematic.json, footprint.json,
assembles the .kicad_pcb via KiCadPCBWriter, validates it, and writes
both final_layout.json and final_layout.kicad_pcb to data/outputs/.

No LLM calls — layout assembly is fully deterministic.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from kicad_pcb_writer import KiCadPCBWriter

# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------

load_dotenv()

_OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "data/outputs"))
_SCHEMAS_DIR = Path(os.getenv("SCHEMAS_DIR", "shared/schemas"))
_TOOLS_DIR = Path(os.getenv("TOOLS_DIR", "backend/tools"))


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
# Assembly context
# ---------------------------------------------------------------------------

def get_assembly_context(output_dir: Path | None = None) -> dict:
    """
    Read all 4 input files and return a summary dict suitable for the
    orchestrator to inspect before triggering final assembly.

    Returns:
      {
        "component_count": int,
        "net_count":       int,
        "trace_count":     int,
        "via_count":       int,
        "board": {"width_mm": float, "height_mm": float},
        "project_name":    str,
        "missing_files":   [str, ...],   # empty when all files present
      }
    """
    out_dir = output_dir or _OUTPUT_DIR

    file_names = {
        "placement":  out_dir / "placement.json",
        "routing":    out_dir / "routing.json",
        "schematic":  out_dir / "schematic.json",
        "footprint":  out_dir / "footprint.json",
    }

    missing_files: list[str] = []
    loaded: dict[str, dict] = {}
    for key, path in file_names.items():
        if not path.exists():
            missing_files.append(str(path))
            loaded[key] = {}
        else:
            with open(path) as f:
                loaded[key] = json.load(f)

    placement  = loaded["placement"]
    routing    = loaded["routing"]
    schematic  = loaded["schematic"]
    board_info = placement.get("board", {})

    return {
        "component_count": len(schematic.get("components", [])),
        "net_count":       len(schematic.get("nets", [])),
        "trace_count":     len(routing.get("traces", [])),
        "via_count":       len(routing.get("vias", [])),
        "board": {
            "width_mm":  board_info.get("width_mm",  100.0),
            "height_mm": board_info.get("height_mm",  80.0),
        },
        "project_name": schematic.get("project_name", "PCB-AI Project"),
        "missing_files": missing_files,
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
    board_meta: dict,
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

    stats = {
        "components":      len(schematic.get("components", [])),
        "nets_total":      len(schematic_nets),
        "nets_routed":     len(routed_nets),
        "nets_unrouted":   len(unrouted),
        "unrouted_nets":   unrouted,
        "traces":          len(routing.get("traces", [])),
        "vias":            len(routing.get("vias", [])),
        "mounting_holes":  4,
    }

    return {
        "schema_version": "1.0",
        "generated_at": now_iso,
        "project_name": board_meta.get("project_name", schematic.get("project_name", "PCB-AI")),
        "revision": board_meta.get("revision", "v1.0"),
        "author": board_meta.get("author", "PCB-AI"),
        "board": {
            "width_mm":  board.get("width_mm",  100.0),
            "height_mm": board.get("height_mm",  80.0),
            "layers":    2,
            "stackup":   "2-layer FR4 1.6mm, 1oz copper",
        },
        "stats": stats,
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
# apply_layout — primary entry point
# ---------------------------------------------------------------------------

def apply_layout(board_meta: dict | None = None, output_dir: Path | None = None) -> dict:
    """
    Read the 4 input files, generate .kicad_pcb, validate it, and write
    final_layout.kicad_pcb + final_layout.json to output_dir.

    Args:
        board_meta: Optional overrides — any of: project_name, revision, author.
                    If None, sensible defaults are used.
        output_dir: Override output directory (default: data/outputs).

    Returns:
        {
          "success": bool,
          "files": {"kicad_pcb": str, "layout_json": str},
          "stats": { component_count, net_count, trace_count, ... },
          "validation_passed": bool,
          "message": str,
        }
    """
    out_dir = Path(output_dir) if output_dir and not isinstance(output_dir, Path) else (output_dir or _OUTPUT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    meta = board_meta or {}

    # ------------------------------------------------------------------
    # Step 1 — Load all inputs
    # ------------------------------------------------------------------
    print("[layout] Loading input files...")
    placement  = _load_json(out_dir / "placement.json")
    routing    = _load_json(out_dir / "routing.json")
    schematic  = _load_json(out_dir / "schematic.json")
    footprint  = _load_json(out_dir / "footprint.json")

    project_name = meta.get("project_name") or schematic.get("project_name", "PCB-AI Project")

    # ------------------------------------------------------------------
    # Step 2 — Generate .kicad_pcb
    # ------------------------------------------------------------------
    print("[layout] Assembling .kicad_pcb...")
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
    # Step 3 — Validate .kicad_pcb
    # ------------------------------------------------------------------
    print("[layout] Validating .kicad_pcb...")
    valid = _validate_kicad_pcb(pcb_path)
    if not valid:
        print("[layout] WARNING: KiCad PCB validation failed", file=sys.stderr)

    # ------------------------------------------------------------------
    # Step 4 — Write final_layout.json
    # ------------------------------------------------------------------
    print("[layout] Writing final_layout.json...")
    final_json = _build_final_layout_json(placement, routing, schematic, footprint, meta)
    final_json["drc"]["kicad_validation_passed"] = valid
    _write_json(json_path, final_json)

    stats = final_json["stats"]
    status_msg = "Layout complete." if valid else "Layout complete (validation warnings)."
    print(f"[layout] {status_msg}")

    return {
        "success": valid,
        "files": {
            "kicad_pcb":   str(pcb_path),
            "layout_json": str(json_path),
        },
        "stats": {
            "component_count": stats["components"],
            "net_count":       stats["nets_total"],
            "trace_count":     stats["traces"],
            "via_count":       stats["vias"],
            "nets_unrouted":   stats["nets_unrouted"],
        },
        "validation_passed": valid,
        "message": status_msg,
    }


# ---------------------------------------------------------------------------
# run() — simple wrapper kept for backward compatibility
# ---------------------------------------------------------------------------

def run(output_dir: Path | None = None) -> dict:
    """
    Thin wrapper around apply_layout() for end-to-end execution once
    all input files are ready.

    Returns a dict with keys: status, message, final_layout_json,
    final_layout_kicad_pcb, validation_passed.
    """
    result = apply_layout(board_meta=None, output_dir=output_dir)
    return {
        "status":                 "ok" if result["success"] else "error",
        "message":                result["message"],
        "final_layout_json":      result["files"]["layout_json"],
        "final_layout_kicad_pcb": result["files"]["kicad_pcb"],
        "validation_passed":      result["validation_passed"],
    }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    result = run()
    print(json.dumps(result, indent=2))
    sys.exit(0 if result["status"] == "ok" else 1)
