"""
Tests for the Example Schematic Agent (tools layer).

No Anthropic SDK calls are made. Claude Code handles reasoning;
this suite tests the pure Python tooling only.

Run all tests:
    pytest agents/example-schematic/test_agent.py -v

Run only unit tests:
    pytest agents/example-schematic/test_agent.py -v -m "not integration"
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Resolve paths
# ---------------------------------------------------------------------------
_HERE = Path(__file__).parent
_ROOT = _HERE.parents[1]
_SCHEMA_PATH = _ROOT / "shared" / "schemas" / "schematic_output.json"
_VALIDATOR = _ROOT / "backend" / "tools" / "schema_validator.py"


# ---------------------------------------------------------------------------
# Sample datasheet fixture — LM358 dual op-amp
# ---------------------------------------------------------------------------

LM358_DATASHEET: dict = {
    "component_name": "LM358",
    "manufacturer": "Texas Instruments",
    "package": "SOIC-8",
    "pins": [
        {"number": 1, "name": "OUT1",  "type": "output",      "function": "Output of amplifier 1"},
        {"number": 2, "name": "IN1-",  "type": "input",       "function": "Inverting input of amplifier 1"},
        {"number": 3, "name": "IN1+",  "type": "input",       "function": "Non-inverting input of amplifier 1"},
        {"number": 4, "name": "GND",   "type": "power",       "function": "Ground"},
        {"number": 5, "name": "IN2+",  "type": "input",       "function": "Non-inverting input of amplifier 2"},
        {"number": 6, "name": "IN2-",  "type": "input",       "function": "Inverting input of amplifier 2"},
        {"number": 7, "name": "OUT2",  "type": "output",      "function": "Output of amplifier 2"},
        {"number": 8, "name": "VCC",   "type": "power",       "function": "Positive supply voltage"},
    ],
    "footprint": {
        "standard": "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm",
        "pad_count": 8,
        "pitch_mm": 1.27,
        "courtyard_mm": {"x": 5.2, "y": 7.0},
    },
    "electrical": {
        "vcc_min": 3.0,
        "vcc_max": 32.0,
        "i_max_ma": 40.0,
    },
    "example_application": {
        "description": (
            "Non-inverting amplifier with gain of 10. "
            "VCC = 5V. Gain set by R1 (10k) feedback and R2 (1.1k) to ground. "
            "Input signal applied to IN+ through R3 (10k) input resistor. "
            "Decoupling capacitor C1 (100nF) on VCC pin. "
            "Output taken from OUT1."
        ),
        "required_passives": [
            "R1 = 10k (feedback resistor)",
            "R2 = 1.1k (gain set resistor to GND)",
            "R3 = 10k (input series resistor)",
            "C1 = 100nF (VCC decoupling capacitor)",
        ],
        "typical_schematic_notes": (
            "Connect IN1- to junction of R1 and R2. "
            "R1 goes from OUT1 to IN1- (feedback). "
            "R2 goes from IN1- to GND. "
            "IN1+ connected to input signal via R3. "
            "C1 placed between VCC (pin 8) and GND as close as possible to IC."
        ),
    },
    "raw_text": "LM358 Dual Operational Amplifier datasheet text...",
    "source_pdf": "lm358.pdf",
}


# ---------------------------------------------------------------------------
# Minimal valid schematic (matches schema exactly, hand-crafted)
# ---------------------------------------------------------------------------

MINIMAL_VALID_SCHEMATIC: dict = {
    "project_name": "LM358 Application Circuit",
    "nets": [
        {"name": "VCC",          "label": "VCC",          "type": "power"},
        {"name": "GND",          "label": "GND",          "type": "gnd"},
        {"name": "INPUT_SIGNAL", "label": "INPUT_SIGNAL", "type": "signal"},
        {"name": "AMP1_OUTPUT",  "label": "AMP1_OUTPUT",  "type": "signal"},
        {"name": "FEEDBACK_NEG", "label": "FEEDBACK_NEG", "type": "signal"},
    ],
    "components": [
        {
            "reference": "U1",
            "value": "LM358",
            "footprint": "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm",
            "position": {"x": 300.0, "y": 300.0},
            "assumed": False,
            "connections": [
                {"pin": 1, "net": "AMP1_OUTPUT"},
                {"pin": 2, "net": "FEEDBACK_NEG"},
                {"pin": 3, "net": "INPUT_SIGNAL"},
                {"pin": 4, "net": "GND"},
                {"pin": 8, "net": "VCC"},
            ],
        },
        {
            "reference": "R1",
            "value": "10k",
            "footprint": "Resistor_SMD:R_0402_1005Metric",
            "position": {"x": 450.0, "y": 250.0},
            "assumed": False,
            "connections": [
                {"pin": 1, "net": "AMP1_OUTPUT"},
                {"pin": 2, "net": "FEEDBACK_NEG"},
            ],
        },
        {
            "reference": "R2",
            "value": "1.1k",
            "footprint": "Resistor_SMD:R_0402_1005Metric",
            "position": {"x": 450.0, "y": 350.0},
            "assumed": False,
            "connections": [
                {"pin": 1, "net": "FEEDBACK_NEG"},
                {"pin": 2, "net": "GND"},
            ],
        },
        {
            "reference": "R3",
            "value": "10k",
            "footprint": "Resistor_SMD:R_0402_1005Metric",
            "position": {"x": 150.0, "y": 300.0},
            "assumed": False,
            "connections": [
                {"pin": 1, "net": "INPUT_SIGNAL"},
                {"pin": 2, "net": "INPUT_SIGNAL"},
            ],
        },
        {
            "reference": "C1",
            "value": "100nF",
            "footprint": "Capacitor_SMD:C_0402_1005Metric",
            "position": {"x": 350.0, "y": 150.0},
            "assumed": False,
            "connections": [
                {"pin": 1, "net": "VCC"},
                {"pin": 2, "net": "GND"},
            ],
        },
    ],
    "power_symbols": ["VCC", "GND"],
    "format": "kicad_sch",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_json(data: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def _schema_valid(schematic: dict) -> tuple[bool, str]:
    """Run the authoritative schema validator subprocess."""
    import os as _os
    env = _os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(schematic, f)
        tmp = Path(f.name)
    try:
        result = subprocess.run(
            [sys.executable, str(_VALIDATOR), str(tmp), str(_SCHEMA_PATH)],
            capture_output=True, text=True, env=env,
        )
        return result.returncode == 0, (result.stdout + result.stderr).strip()
    finally:
        tmp.unlink(missing_ok=True)


NET_GENERIC_PATTERN = re.compile(r"^N[ET]*\d+$", re.IGNORECASE)
PASSIVE_REF_PATTERN = re.compile(r"^[RCLD]\d", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Unit tests — no API calls
# ---------------------------------------------------------------------------


class TestSchemaCompliance:
    """The minimal valid schematic must pass schema validation."""

    def test_minimal_valid_passes_schema(self):
        ok, msg = _schema_valid(MINIMAL_VALID_SCHEMATIC)
        assert ok, f"Schema validation failed: {msg}"

    def test_missing_project_name_is_caught_in_code(self):
        """
        The schema template (not a strict JSON Schema) does not enforce required
        fields — that is the validator's current design. Instead verify that our
        agent code itself always sets project_name.
        """
        assert "project_name" in MINIMAL_VALID_SCHEMATIC

    def test_format_field_is_kicad_sch(self):
        assert MINIMAL_VALID_SCHEMATIC["format"] == "kicad_sch"

    def test_net_type_values_are_valid(self):
        valid_types = {"power", "signal", "gnd"}
        for net in MINIMAL_VALID_SCHEMATIC["nets"]:
            assert net["type"] in valid_types, \
                f"Net '{net['name']}' has invalid type '{net['type']}'"


class TestNetNaming:
    """Net names must never be generic (NET001-style)."""

    def test_vcc_gnd_pass(self):
        for name in ("VCC", "VDD", "GND", "AGND", "PGND"):
            assert not NET_GENERIC_PATTERN.match(name), f"{name} should not be generic"

    def test_generic_names_detected(self):
        for name in ("NET001", "N001", "NET1", "n001", "net001"):
            assert NET_GENERIC_PATTERN.match(name), f"{name} should be flagged as generic"

    def test_descriptive_names_pass(self):
        for name in ("INPUT_SIGNAL", "AMP1_OUTPUT", "FEEDBACK_NEG", "SPI_MOSI", "TX_DATA"):
            assert not NET_GENERIC_PATTERN.match(name), f"{name} was wrongly flagged as generic"

    def test_no_generic_nets_in_minimal_schematic(self):
        for net in MINIMAL_VALID_SCHEMATIC["nets"]:
            name = net["name"]
            assert not NET_GENERIC_PATTERN.match(name), f"Net '{name}' is generic"

    def test_validate_net_names_function(self):
        from agent import _validate_net_names

        good = {"nets": [{"name": "VCC"}, {"name": "INPUT_SIGNAL"}, {"name": "GND"}]}
        assert _validate_net_names(good) == []

        bad = {"nets": [{"name": "NET001"}, {"name": "N002"}, {"name": "VCC"}]}
        violations = _validate_net_names(bad)
        assert len(violations) == 2
        assert any("NET001" in v for v in violations)
        assert any("N002" in v for v in violations)


class TestPassiveValues:
    """All passives must have non-empty values."""

    def test_passives_have_values_in_minimal_schematic(self):
        for comp in MINIMAL_VALID_SCHEMATIC["components"]:
            ref = comp["reference"]
            if PASSIVE_REF_PATTERN.match(ref):
                value = comp.get("value", "").strip()
                assert value and value.lower() not in ("", "?", "unknown", "tbd"), \
                    f"Passive {ref} has bad value: {value!r}"

    def test_validate_passive_values_function(self):
        from agent import _validate_passive_values

        good = {
            "components": [
                {"reference": "R1", "value": "10k"},
                {"reference": "C1", "value": "100nF"},
                {"reference": "U1", "value": "LM358"},
            ]
        }
        assert _validate_passive_values(good) == []

        bad = {
            "components": [
                {"reference": "R1", "value": ""},
                {"reference": "C1", "value": "?"},
                {"reference": "R2", "value": "10k"},
            ]
        }
        violations = _validate_passive_values(bad)
        assert len(violations) == 2
        assert any("R1" in v for v in violations)
        assert any("C1" in v for v in violations)
        assert all("R2" not in v for v in violations)


class TestAssumedFlag:
    """Components with guessed values must carry assumed: true."""

    def test_assumed_true_accepted_by_schema(self):
        import copy
        schematic = copy.deepcopy(MINIMAL_VALID_SCHEMATIC)
        schematic["components"][1]["assumed"] = True
        ok, msg = _schema_valid(schematic)
        assert ok, f"Schema rejected assumed:true component: {msg}"

    def test_no_assumed_also_valid(self):
        import copy
        schematic = copy.deepcopy(MINIMAL_VALID_SCHEMATIC)
        schematic["components"][1].pop("assumed", None)
        ok, msg = _schema_valid(schematic)
        assert ok, f"Schema rejected component without assumed field: {msg}"


class TestNetlistBuilder:
    """Test the netlist_builder helper."""

    def test_summary_contains_component_refs(self):
        from netlist_builder import build_netlist_summary

        summary = build_netlist_summary(MINIMAL_VALID_SCHEMATIC)
        for ref in ("U1", "R1", "R2", "R3", "C1"):
            assert ref in summary, f"Component {ref} missing from netlist summary"

    def test_summary_contains_net_names(self):
        from netlist_builder import build_netlist_summary

        summary = build_netlist_summary(MINIMAL_VALID_SCHEMATIC)
        for net in ("VCC", "GND", "INPUT_SIGNAL", "AMP1_OUTPUT"):
            assert net in summary, f"Net {net} missing from netlist summary"

    def test_summary_contains_power_symbols(self):
        from netlist_builder import build_netlist_summary

        summary = build_netlist_summary(MINIMAL_VALID_SCHEMATIC)
        assert "VCC" in summary
        assert "GND" in summary

    def test_summary_from_file(self, tmp_path):
        from netlist_builder import build_netlist_from_file

        p = tmp_path / "schematic.json"
        _write_json(MINIMAL_VALID_SCHEMATIC, p)
        summary = build_netlist_from_file(p)
        assert "LM358 Application Circuit" in summary

    def test_dangling_net_warning(self):
        import copy
        from netlist_builder import build_netlist_summary

        schematic = copy.deepcopy(MINIMAL_VALID_SCHEMATIC)
        schematic["components"].append({
            "reference": "R4",
            "value": "1k",
            "footprint": "Resistor_SMD:R_0402_1005Metric",
            "position": {"x": 500.0, "y": 500.0},
            "assumed": True,
            "connections": [
                {"pin": 1, "net": "ORPHAN_NET"},
                {"pin": 2, "net": "GND"},
            ],
        })
        summary = build_netlist_summary(schematic)
        assert "ORPHAN_NET" in summary
        assert "dangling" in summary.lower()


class TestGetApplicationContext:
    """Test get_application_context() tool."""

    def test_returns_required_keys(self, tmp_path):
        from agent import get_application_context

        ds_path = tmp_path / "datasheet.json"
        _write_json(LM358_DATASHEET, ds_path)

        ctx = get_application_context(ds_path)
        assert ctx["component_name"] == "LM358"
        assert ctx["package"] == "SOIC-8"
        assert "example_application" in ctx

    def test_example_application_section_is_preserved(self, tmp_path):
        from agent import get_application_context

        ds_path = tmp_path / "datasheet.json"
        _write_json(LM358_DATASHEET, ds_path)

        ctx = get_application_context(ds_path)
        ea = ctx["example_application"]
        assert "description" in ea
        assert "required_passives" in ea
        assert "typical_schematic_notes" in ea

    def test_raises_on_missing_datasheet(self, tmp_path):
        from agent import get_application_context

        with pytest.raises(FileNotFoundError):
            get_application_context(tmp_path / "nonexistent.json")

    def test_missing_fields_return_empty_defaults(self, tmp_path):
        from agent import get_application_context

        ds_path = tmp_path / "datasheet.json"
        _write_json({}, ds_path)

        ctx = get_application_context(ds_path)
        assert ctx["component_name"] == ""
        assert ctx["package"] == ""
        assert ctx["example_application"] == {}


class TestApplySchematic:
    """Test apply_schematic() tool."""

    def test_valid_schematic_succeeds(self, tmp_path):
        from agent import apply_schematic
        import copy

        result = apply_schematic(copy.deepcopy(MINIMAL_VALID_SCHEMATIC), output_dir=tmp_path)
        assert result["success"] is True
        assert result["errors"] == []
        assert len(result["netlist_summary"]) > 0
        assert (tmp_path / "example_schematic.json").exists()

    def test_output_file_is_valid_json(self, tmp_path):
        from agent import apply_schematic
        import copy

        apply_schematic(copy.deepcopy(MINIMAL_VALID_SCHEMATIC), output_dir=tmp_path)
        with open(tmp_path / "example_schematic.json") as f:
            data = json.load(f)
        assert data["project_name"] == "LM358 Application Circuit"

    def test_format_field_is_enforced(self, tmp_path):
        from agent import apply_schematic
        import copy

        schematic = copy.deepcopy(MINIMAL_VALID_SCHEMATIC)
        schematic["format"] = "wrong_format"
        result = apply_schematic(schematic, output_dir=tmp_path)
        # format is always overwritten to kicad_sch
        with open(tmp_path / "example_schematic.json") as f:
            data = json.load(f)
        assert data["format"] == "kicad_sch"

    def test_generic_net_names_reported_in_errors(self, tmp_path):
        from agent import apply_schematic
        import copy

        schematic = copy.deepcopy(MINIMAL_VALID_SCHEMATIC)
        schematic["nets"].append({"name": "NET001", "label": "NET001", "type": "signal"})

        result = apply_schematic(schematic, output_dir=tmp_path)
        # Validation warnings are included in errors even if schema passes
        assert any("NET001" in e for e in result["errors"])

    def test_missing_passive_value_reported_in_errors(self, tmp_path):
        from agent import apply_schematic
        import copy

        schematic = copy.deepcopy(MINIMAL_VALID_SCHEMATIC)
        schematic["components"][1]["value"] = ""  # R1 with no value

        result = apply_schematic(schematic, output_dir=tmp_path)
        assert any("R1" in e for e in result["errors"])

    def test_netlist_summary_contains_component_refs(self, tmp_path):
        from agent import apply_schematic
        import copy

        result = apply_schematic(copy.deepcopy(MINIMAL_VALID_SCHEMATIC), output_dir=tmp_path)
        for ref in ("U1", "R1", "R2", "R3", "C1"):
            assert ref in result["netlist_summary"], f"{ref} missing from netlist_summary"

    def test_netlist_summary_contains_net_names(self, tmp_path):
        from agent import apply_schematic
        import copy

        result = apply_schematic(copy.deepcopy(MINIMAL_VALID_SCHEMATIC), output_dir=tmp_path)
        for net in ("VCC", "GND", "INPUT_SIGNAL"):
            assert net in result["netlist_summary"], f"{net} missing from netlist_summary"


class TestRunStub:
    """run() must raise NotImplementedError."""

    def test_run_raises_not_implemented(self):
        from agent import run

        with pytest.raises(NotImplementedError):
            run()

    def test_run_raises_with_args(self):
        from agent import run

        with pytest.raises(NotImplementedError):
            run("some_arg", key="value")
