"""
agent.py — Component Agent (tools-only layer)

Python is a pure tools layer. All reasoning is handled by the orchestrator (Claude Code).
This module provides:
  - read_datasheet(path)       — load datasheet.json from disk
  - validate_output(data, schema_path) — validate a dict against a JSON schema file
  - get_datasheet_summary()    — compact summary the orchestrator needs before designing a symbol
  - apply_symbol(symbol_dict)  — validate + write component.json and component.kicad_sym

run() raises NotImplementedError; the orchestrator designs the symbol, not this module.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

# Allow running from any working directory
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from agents.component.kicad_sym_writer import write_kicad_sym
from backend.tools.schema_validator import validate as validate_schema
from backend.tools.kicad_validator import validate_sexpr

# ─── Path constants (overridable via env) ───────────────────────────────────
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", str(REPO_ROOT / "data" / "outputs")))
DATASHEET_JSON = OUTPUT_DIR / "datasheet.json"
COMPONENT_JSON = OUTPUT_DIR / "component.json"
COMPONENT_KICAD_SYM = OUTPUT_DIR / "component.kicad_sym"
COMPONENT_SCHEMA = REPO_ROOT / "shared" / "schemas" / "component_output.json"


# ─── Public tools ────────────────────────────────────────────────────────────

def read_datasheet(path: Path = DATASHEET_JSON) -> dict:
    """Read and return the datasheet.json produced by the datasheet-parser agent."""
    if not path.exists():
        raise FileNotFoundError(
            f"datasheet.json not found at {path}. "
            "Run the datasheet-parser agent first."
        )
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def validate_output(data: dict, schema_path: str | Path) -> bool:
    """
    Validate *data* (a dict) against the JSON schema at *schema_path*.

    Writes a temporary file so the underlying file-based schema_validator can
    read it, then cleans up.  Returns True if valid, False otherwise.
    """
    schema_path = Path(schema_path)
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    ) as tmp:
        json.dump(data, tmp, indent=2)
        tmp_path = tmp.name
    try:
        return validate_schema(tmp_path, str(schema_path))
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def get_datasheet_summary() -> dict:
    """
    Read datasheet.json and return a compact summary suitable for the orchestrator.

    Returns a dict with:
      - component_name  (str)
      - package         (str)
      - pin_count       (int)
      - pin_list        (list of {number, name, type} dicts)
    """
    data = read_datasheet()
    pins = data.get("pins", [])
    return {
        "component_name": data.get("component_name", ""),
        "package": data.get("package", ""),
        "pin_count": len(pins),
        "pin_list": [
            {
                "number": p.get("number"),
                "name": p.get("name", ""),
                "type": p.get("type", ""),
            }
            for p in pins
        ],
    }


def apply_symbol(symbol_dict: dict, output_dir: Path | None = None) -> dict:
    """
    Validate *symbol_dict* (already designed by the orchestrator), generate the
    KiCad symbol file, write both outputs, and validate them.

    Args:
        symbol_dict: A dict matching the component_output.json schema,
                     i.e. {"symbol": {"name": ..., "pins": [...], ...}}.
        output_dir:  Optional override for the output directory.
                     Defaults to the module-level OUTPUT_DIR.

    Returns:
        {
            "success": bool,
            "files": {
                "component_json": str | None,
                "component_kicad_sym": str | None,
            },
            "errors": [str, ...],  # empty list on success
        }
    """
    out_dir = Path(output_dir) if output_dir is not None else OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    comp_json_path = out_dir / "component.json"
    comp_sym_path = out_dir / "component.kicad_sym"

    errors: list[str] = []

    # 1. Validate the symbol dict against the schema
    if not validate_output(symbol_dict, COMPONENT_SCHEMA):
        errors.append(
            f"symbol_dict does not match schema at {COMPONENT_SCHEMA}"
        )
        return {"success": False, "files": {"component_json": None, "component_kicad_sym": None}, "errors": errors}

    # 2. Write component.json
    try:
        with open(comp_json_path, "w", encoding="utf-8") as f:
            json.dump(symbol_dict, f, indent=2)
    except OSError as exc:
        errors.append(f"Failed to write component.json: {exc}")
        return {"success": False, "files": {"component_json": None, "component_kicad_sym": None}, "errors": errors}

    # 3. Generate and write component.kicad_sym
    try:
        kicad_content = write_kicad_sym(symbol_dict)
        with open(comp_sym_path, "w", encoding="utf-8") as f:
            f.write(kicad_content)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"Failed to write component.kicad_sym: {exc}")
        return {
            "success": False,
            "files": {"component_json": str(comp_json_path), "component_kicad_sym": None},
            "errors": errors,
        }

    # 4. Validate both written files
    json_valid = validate_schema(str(comp_json_path), str(COMPONENT_SCHEMA))
    if not json_valid:
        errors.append(f"component.json failed schema validation: {comp_json_path}")

    kicad_valid = validate_sexpr(str(comp_sym_path))
    if not kicad_valid:
        errors.append(f"component.kicad_sym failed KiCad validation: {comp_sym_path}")

    success = json_valid and kicad_valid
    return {
        "success": success,
        "files": {
            "component_json": str(comp_json_path),
            "component_kicad_sym": str(comp_sym_path),
        },
        "errors": errors,
    }


# ─── Disabled entry point ────────────────────────────────────────────────────

def run() -> None:
    """Disabled. Reasoning is handled by the orchestrator."""
    raise NotImplementedError(
        "Use apply_symbol() — reasoning is handled by the orchestrator"
    )


if __name__ == "__main__":
    run()
