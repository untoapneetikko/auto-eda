"""
test_agent.py — Tests for the Component Agent.

Tests:
1. kicad_sym_writer produces valid (balanced-parenthesis) output for a synthetic symbol
2. Full agent run with a sample datasheet.json produces component.json matching the schema
3. component.kicad_sym has balanced parentheses
4. Agent handles missing datasheet.json gracefully

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

# A minimal but realistic 8-pin IC (e.g. NE555 timer-like)
SAMPLE_DATASHEET = {
    "component_name": "NE555",
    "manufacturer": "Texas Instruments",
    "package": "DIP-8",
    "pins": [
        {"number": 1, "name": "GND",     "type": "power",         "function": "Ground"},
        {"number": 2, "name": "TRIG",    "type": "input",         "function": "Trigger"},
        {"number": 3, "name": "OUT",     "type": "output",        "function": "Output"},
        {"number": 4, "name": "RESET",   "type": "input",         "function": "Reset (active low)"},
        {"number": 5, "name": "CV",      "type": "bidirectional", "function": "Control Voltage"},
        {"number": 6, "name": "THRESH",  "type": "input",         "function": "Threshold"},
        {"number": 7, "name": "DIS",     "type": "output",        "function": "Discharge"},
        {"number": 8, "name": "VCC",     "type": "power",         "function": "Supply Voltage"},
    ],
    "footprint": {
        "standard": "DIP-8",
        "pad_count": 8,
        "pitch_mm": 2.54,
        "courtyard_mm": {"x": 9.0, "y": 7.0},
    },
    "electrical": {
        "vcc_min": 4.5,
        "vcc_max": 16.0,
        "i_max_ma": 200.0,
    },
    "example_application": {
        "description": "Astable multivibrator oscillator",
        "required_passives": ["R1", "R2", "C1", "C2"],
        "typical_schematic_notes": "C2 = 0.01uF on CV pin for noise immunity",
    },
    "raw_text": "NE555 Precision Timer datasheet excerpt for testing.",
    "source_pdf": "ne555.pdf",
}

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
        # Pin name should appear in output
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
    """Agent must raise FileNotFoundError when datasheet.json does not exist."""
    from agents.component.agent import read_datasheet
    import tempfile

    nonexistent = Path(tempfile.mkdtemp()) / "datasheet.json"
    try:
        read_datasheet(nonexistent)
        assert False, "Expected FileNotFoundError was not raised"
    except FileNotFoundError:
        pass
    print("PASS test_missing_datasheet_raises")


def test_agent_run_with_sample_datasheet(tmp_path: Path | None = None):
    """
    Full agent run using SAMPLE_DATASHEET as input.

    Writes datasheet.json to a temp directory, patches OUTPUT_DIR, calls agent.run(),
    then checks:
    - component.json exists and matches the schema
    - component.kicad_sym exists and has balanced parentheses
    """
    import importlib
    import agents.component.agent as agent_module

    # Create isolated temp output directory
    if tmp_path is None:
        tmp_path = Path(tempfile.mkdtemp())

    # Write sample datasheet.json
    ds_path = tmp_path / "datasheet.json"
    with open(ds_path, "w", encoding="utf-8") as f:
        json.dump(SAMPLE_DATASHEET, f, indent=2)

    # Monkey-patch the paths in the agent module
    orig_datasheet = agent_module.DATASHEET_JSON
    orig_component = agent_module.COMPONENT_JSON
    orig_kicad = agent_module.COMPONENT_KICAD_SYM
    orig_output_dir = agent_module.OUTPUT_DIR

    agent_module.DATASHEET_JSON = ds_path
    agent_module.COMPONENT_JSON = tmp_path / "component.json"
    agent_module.COMPONENT_KICAD_SYM = tmp_path / "component.kicad_sym"
    agent_module.OUTPUT_DIR = tmp_path

    try:
        result = agent_module.run()
    finally:
        # Restore originals
        agent_module.DATASHEET_JSON = orig_datasheet
        agent_module.COMPONENT_JSON = orig_component
        agent_module.COMPONENT_KICAD_SYM = orig_kicad
        agent_module.OUTPUT_DIR = orig_output_dir

    # Verify component.json
    comp_json_path = tmp_path / "component.json"
    assert comp_json_path.exists(), "component.json was not written"
    json_valid = validate_schema(str(comp_json_path), str(COMPONENT_SCHEMA))
    assert json_valid, "component.json does not match the schema"

    # Verify component.kicad_sym
    kicad_path = tmp_path / "component.kicad_sym"
    assert kicad_path.exists(), "component.kicad_sym was not written"
    kicad_valid = validate_sexpr(str(kicad_path))
    assert kicad_valid, "component.kicad_sym has unbalanced parentheses"

    print("PASS test_agent_run_with_sample_datasheet")
    return result


# ─── Direct run ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Running component agent tests...\n")

    test_kicad_sym_writer_balanced_parens()
    test_kicad_sym_writer_contains_all_pins()
    test_kicad_sym_writer_validate_with_tool()
    test_component_json_schema_validation()
    test_missing_datasheet_raises()

    # Only run the full Anthropic API test if ANTHROPIC_API_KEY is set
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if api_key:
        print("\nANTHROPIC_API_KEY found — running full agent test...")
        test_agent_run_with_sample_datasheet()
    else:
        print(
            "\nSkipping test_agent_run_with_sample_datasheet: "
            "ANTHROPIC_API_KEY not set."
        )

    print("\nAll tests passed.")
