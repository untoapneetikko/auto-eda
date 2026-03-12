"""
test_agent.py — Tests for the Component Agent (tools-only layer).

Tests:
1. kicad_sym_writer produces balanced-parenthesis output for a synthetic symbol
2. kicad_sym_writer contains all expected pin entries
3. kicad_sym_writer output passes kicad_validator
4. SAMPLE_COMPONENT passes component_output.json schema validation
5. read_datasheet raises FileNotFoundError for a missing file
6. apply_symbol() with a known-good symbol dict succeeds end-to-end
7. apply_symbol() with a bad symbol dict returns success=False with errors

Run from the repo root:
    python -m pytest agents/component/test_agent.py -v
or:
    python agents/component/test_agent.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

# Ensure repo root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from agents.component.kicad_sym_writer import write_kicad_sym
from backend.tools.kicad_validator import validate_sexpr
from backend.tools.schema_validator import validate as validate_schema

# ─── Sample data ────────────────────────────────────────────────────────────

# A hand-crafted component.json that correctly follows the design rules
SAMPLE_COMPONENT = {
    "symbol": {
        "name": "NE555",
        "reference": "U",
        "pins": [
            # Power pins — top/bottom
            {"number": 8, "name": "VCC",    "direction": "top",    "type": "power",         "x": 0.0,     "y": 12.7},
            {"number": 1, "name": "GND",    "direction": "bottom", "type": "power",         "x": 0.0,     "y": -12.7},
            # Input pins — left side
            {"number": 2, "name": "TRIG",   "direction": "left",   "type": "input",         "x": -10.16,  "y": 5.08},
            {"number": 4, "name": "RESET",  "direction": "left",   "type": "input",         "x": -10.16,  "y": 2.54},
            {"number": 6, "name": "THRESH", "direction": "left",   "type": "input",         "x": -10.16,  "y": 0.0},
            # Output / bidirectional pins — right side
            {"number": 3, "name": "OUT",    "direction": "right",  "type": "output",        "x": 10.16,   "y": 5.08},
            {"number": 7, "name": "DIS",    "direction": "right",  "type": "output",        "x": 10.16,   "y": 2.54},
            {"number": 5, "name": "CV",     "direction": "right",  "type": "bidirectional", "x": 10.16,   "y": 0.0},
        ],
        "body": {"width": 15.24, "height": 17.78},
        "format": "kicad_sym",
    }
}

COMPONENT_SCHEMA = REPO_ROOT / "shared" / "schemas" / "component_output.json"


# ─── Helper ──────────────────────────────────────────────────────────────────

def _count_parens(text: str) -> tuple[int, int]:
    """Return (open_count, close_count) of parentheses in text."""
    return text.count('('), text.count(')')


# ─── Tests ───────────────────────────────────────────────────────────────────

def test_kicad_sym_writer_balanced_parens():
    """kicad_sym_writer must produce a file with perfectly balanced parentheses."""
    content = write_kicad_sym(SAMPLE_COMPONENT)
    opens, closes = _count_parens(content)
    assert opens == closes, (
        f"Unbalanced parentheses: {opens} open vs {closes} close\n\n{content}"
    )
    print("PASS test_kicad_sym_writer_balanced_parens")


def test_kicad_sym_writer_contains_all_pins():
    """kicad_sym_writer must emit an entry for every pin."""
    content = write_kicad_sym(SAMPLE_COMPONENT)
    pins = SAMPLE_COMPONENT["symbol"]["pins"]
    for pin in pins:
        assert f'"{pin["number"]}"' in content or str(pin["number"]) in content, (
            f"Pin {pin['number']} not found in .kicad_sym output"
        )
        name = pin["name"]
        assert name in content, f"Pin name '{name}' not found in .kicad_sym output"
    print("PASS test_kicad_sym_writer_contains_all_pins")


def test_kicad_sym_writer_validate_with_tool():
    """Validate the kicad_sym_writer output using the official kicad_validator tool."""
    content = write_kicad_sym(SAMPLE_COMPONENT)
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".kicad_sym", delete=False, encoding="utf-8"
    ) as f:
        f.write(content)
        tmp_path = f.name
    try:
        ok = validate_sexpr(tmp_path)
        assert ok, f"kicad_validator reported invalid file at {tmp_path}"
    finally:
        os.unlink(tmp_path)
    print("PASS test_kicad_sym_writer_validate_with_tool")


def test_component_json_schema_validation():
    """SAMPLE_COMPONENT must pass schema validation against component_output.json."""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    ) as f:
        json.dump(SAMPLE_COMPONENT, f, indent=2)
        tmp_path = f.name
    try:
        ok = validate_schema(tmp_path, str(COMPONENT_SCHEMA))
        assert ok, "SAMPLE_COMPONENT failed component_output.json schema validation"
    finally:
        os.unlink(tmp_path)
    print("PASS test_component_json_schema_validation")


def test_missing_datasheet_raises():
    """read_datasheet must raise FileNotFoundError when datasheet.json does not exist."""
    from agents.component.agent import read_datasheet

    nonexistent = Path(tempfile.mkdtemp()) / "datasheet.json"
    try:
        read_datasheet(nonexistent)
        assert False, "Expected FileNotFoundError was not raised"
    except FileNotFoundError:
        pass
    print("PASS test_missing_datasheet_raises")


def test_apply_symbol_success():
    """apply_symbol() with a known-good symbol dict must write both files and return success=True."""
    from agents.component.agent import apply_symbol

    with tempfile.TemporaryDirectory() as tmp_dir:
        result = apply_symbol(SAMPLE_COMPONENT, output_dir=tmp_dir)

    assert result["success"], (
        f"apply_symbol() reported failure. errors={result['errors']}"
    )
    assert not result["errors"], f"Unexpected errors: {result['errors']}"
    assert result["files"]["component_json"] is not None, "component_json path is None"
    assert result["files"]["component_kicad_sym"] is not None, "component_kicad_sym path is None"
    print("PASS test_apply_symbol_success")


def test_apply_symbol_invalid_dict():
    """apply_symbol() with a bad symbol dict must return success=False with a non-empty errors list."""
    from agents.component.agent import apply_symbol

    bad_symbol = {"symbol": {"name": "BAD"}}  # missing required fields

    with tempfile.TemporaryDirectory() as tmp_dir:
        result = apply_symbol(bad_symbol, output_dir=tmp_dir)

    assert not result["success"], "apply_symbol() should have failed on a bad symbol dict"
    assert result["errors"], "errors list should be non-empty on failure"
    print("PASS test_apply_symbol_invalid_dict")


def test_run_raises_not_implemented():
    """run() must raise NotImplementedError."""
    from agents.component.agent import run

    try:
        run()
        assert False, "Expected NotImplementedError was not raised"
    except NotImplementedError as exc:
        assert "apply_symbol" in str(exc), f"Unexpected message: {exc}"
    print("PASS test_run_raises_not_implemented")


# ─── Direct run ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Running component agent tests...\n")

    test_kicad_sym_writer_balanced_parens()
    test_kicad_sym_writer_contains_all_pins()
    test_kicad_sym_writer_validate_with_tool()
    test_component_json_schema_validation()
    test_missing_datasheet_raises()
    test_apply_symbol_success()
    test_apply_symbol_invalid_dict()
    test_run_raises_not_implemented()

    print("\nAll tests passed.")
