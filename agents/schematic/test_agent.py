"""
test_agent.py — Unit tests for the schematic agent tool layer.

Tests:
  1.  No NET001-style names in any net or connection
  2.  All power nets include a voltage designator (e.g. VCC_3V3, VCC_5V)
  3.  Every IC pin has a connection entry (no floating pins; NC markers present)
  4.  net_namer produces descriptive names for common pin functions
  5.  _extract_json_block correctly extracts fenced and bare JSON
  6.  _ensure_no_floating_pins fills missing pins with NC
  7.  apply_schematic() writes schematic.json and returns {success, errors}
  8.  get_design_context() returns correct structure from fixture files
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import patch

# ---------------------------------------------------------------------------
# Make sure local modules are importable without installing as a package
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).parent))

from net_namer import suggest_net_names  # noqa: E402
from agent import (  # noqa: E402
    _ensure_no_floating_pins,
    _extract_json_block,
    apply_schematic,
    get_design_context,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NET001_PATTERN = re.compile(r"\bNET\d{3,}\b", re.IGNORECASE)


def _all_net_names(schematic: dict[str, Any]) -> list[str]:
    """Return every net name that appears anywhere in the schematic."""
    names: list[str] = []
    for net in schematic.get("nets", []):
        names.append(net.get("name", ""))
        names.append(net.get("label", ""))
    for comp in schematic.get("components", []):
        for conn in comp.get("connections", []):
            names.append(conn.get("net", ""))
    return [n for n in names if n]


def _power_net_names(schematic: dict[str, Any]) -> list[str]:
    """Return net names where type == 'power'."""
    return [
        n.get("name", "")
        for n in schematic.get("nets", [])
        if n.get("type") == "power"
    ]


def _all_pin_numbers_connected(
    schematic: dict[str, Any], component_pins: list[dict[str, Any]], ref: str = "U1"
) -> tuple[bool, list[int]]:
    """
    Check that every pin number in component_pins appears in U1's connections.
    Returns (all_connected: bool, missing_pins: list[int]).
    """
    expected = {int(p["number"]) for p in component_pins}
    found: set[int] = set()
    for comp in schematic.get("components", []):
        if comp.get("reference") == ref:
            for conn in comp.get("connections", []):
                found.add(int(conn["pin"]))
    missing = sorted(expected - found)
    return len(missing) == 0, missing


# ---------------------------------------------------------------------------
# Minimal fixture schematics
# ---------------------------------------------------------------------------

def _valid_schematic() -> dict[str, Any]:
    return {
        "project_name": "Test Project",
        "nets": [
            {"name": "VCC_3V3", "label": "VCC_3V3", "type": "power"},
            {"name": "GND",     "label": "GND",     "type": "gnd"},
            {"name": "I2C_SDA", "label": "I2C_SDA", "type": "signal"},
            {"name": "I2C_SCL", "label": "I2C_SCL", "type": "signal"},
        ],
        "components": [
            {
                "reference": "U1",
                "value": "EXAMPLE_IC",
                "footprint": "SOIC-8",
                "position": {"x": 100.0, "y": 100.0},
                "connections": [
                    {"pin": 1, "net": "VCC_3V3"},
                    {"pin": 2, "net": "GND"},
                    {"pin": 3, "net": "I2C_SDA"},
                    {"pin": 4, "net": "I2C_SCL"},
                    {"pin": 5, "net": "NC"},
                    {"pin": 6, "net": "NC"},
                    {"pin": 7, "net": "NC"},
                    {"pin": 8, "net": "NC"},
                ],
            },
            {
                "reference": "C1",
                "value": "100nF",
                "footprint": "0402",
                "position": {"x": 200.0, "y": 100.0},
                "connections": [
                    {"pin": 1, "net": "VCC_3V3"},
                    {"pin": 2, "net": "GND"},
                ],
            },
        ],
        "power_symbols": ["VCC_3V3", "GND"],
        "format": "kicad_sch",
    }


def _bad_net_schematic() -> dict[str, Any]:
    """Schematic with NET001-style names — should FAIL the net name check."""
    sch = _valid_schematic()
    sch["nets"].append({"name": "NET001", "label": "NET001", "type": "signal"})
    sch["components"][0]["connections"].append({"pin": 5, "net": "NET001"})
    return sch


def _no_voltage_power_schematic() -> dict[str, Any]:
    """Power net without voltage in name — should FAIL the power net check."""
    sch = _valid_schematic()
    sch["nets"][0]["name"] = "VCC"   # no voltage suffix
    sch["nets"][0]["label"] = "VCC"
    sch["components"][0]["connections"][0]["net"] = "VCC"
    sch["power_symbols"] = ["VCC", "GND"]
    return sch


def _floating_pin_schematic() -> dict[str, Any]:
    """IC only connects 2 of 8 pins — rest are floating."""
    return {
        "project_name": "Floating Test",
        "nets": [
            {"name": "VCC_5V", "label": "VCC_5V", "type": "power"},
            {"name": "GND",    "label": "GND",    "type": "gnd"},
        ],
        "components": [
            {
                "reference": "U1",
                "value": "SOME_IC",
                "footprint": "DIP-8",
                "position": {"x": 50.0, "y": 50.0},
                "connections": [
                    {"pin": 1, "net": "VCC_5V"},
                    {"pin": 8, "net": "GND"},
                    # pins 2-7 missing → floating
                ],
            }
        ],
        "power_symbols": ["VCC_5V", "GND"],
        "format": "kicad_sch",
    }


# ---------------------------------------------------------------------------
# Component fixture
# ---------------------------------------------------------------------------

def _component_8pin() -> dict[str, Any]:
    return {
        "symbol": {
            "pins": [{"number": i, "name": f"PIN{i}", "type": "passive"} for i in range(1, 9)]
        }
    }


# ---------------------------------------------------------------------------
# Test functions — existing logic tests
# ---------------------------------------------------------------------------

def test_no_net001_names_in_valid_schematic() -> None:
    sch = _valid_schematic()
    names = _all_net_names(sch)
    bad = [n for n in names if _NET001_PATTERN.match(n)]
    assert not bad, f"Found NET001-style names: {bad}"
    print("PASS test_no_net001_names_in_valid_schematic")


def test_net001_detected_in_bad_schematic() -> None:
    sch = _bad_net_schematic()
    names = _all_net_names(sch)
    bad = [n for n in names if _NET001_PATTERN.match(n)]
    assert bad, "Expected to find NET001-style names but found none"
    print("PASS test_net001_detected_in_bad_schematic")


def test_power_nets_include_voltage() -> None:
    sch = _valid_schematic()
    power_nets = _power_net_names(sch)
    gnd_like = re.compile(r"^(A?P?GND|DGND)$", re.IGNORECASE)
    missing_voltage = [
        n for n in power_nets
        if not gnd_like.match(n) and not re.search(r"\d", n)
    ]
    assert not missing_voltage, f"Power nets without voltage in name: {missing_voltage}"
    print("PASS test_power_nets_include_voltage")


def test_power_net_without_voltage_detected() -> None:
    sch = _no_voltage_power_schematic()
    power_nets = _power_net_names(sch)
    gnd_like = re.compile(r"^(A?P?GND|DGND)$", re.IGNORECASE)
    missing_voltage = [
        n for n in power_nets
        if not gnd_like.match(n) and not re.search(r"\d", n)
    ]
    assert missing_voltage, "Expected to detect power net without voltage but none found"
    print("PASS test_power_net_without_voltage_detected")


def test_no_floating_pins_in_valid_schematic() -> None:
    sch = _valid_schematic()
    comp = _component_8pin()
    all_connected, missing = _all_pin_numbers_connected(sch, comp["symbol"]["pins"])
    assert all_connected, f"Floating pins in valid schematic: {missing}"
    print("PASS test_no_floating_pins_in_valid_schematic")


def test_ensure_no_floating_pins_fills_nc() -> None:
    sch = _floating_pin_schematic()
    comp = _component_8pin()
    fixed = _ensure_no_floating_pins(sch, comp)
    all_connected, missing = _all_pin_numbers_connected(fixed, comp["symbol"]["pins"])
    assert all_connected, f"Still floating after fix: {missing}"
    u1_conns = next(c for c in fixed["components"] if c["reference"] == "U1")["connections"]
    nc_pins = [c["pin"] for c in u1_conns if c["net"] == "NC"]
    assert len(nc_pins) == 6, f"Expected 6 NC pins, got {nc_pins}"
    print("PASS test_ensure_no_floating_pins_fills_nc")


def test_floating_pin_schematic_detected_before_fix() -> None:
    sch = _floating_pin_schematic()
    comp = _component_8pin()
    all_connected, missing = _all_pin_numbers_connected(sch, comp["symbol"]["pins"])
    assert not all_connected, "Expected floating pins but all seem connected"
    print("PASS test_floating_pin_schematic_detected_before_fix")


# ---------------------------------------------------------------------------
# net_namer tests
# ---------------------------------------------------------------------------

def test_net_namer_power_pins() -> None:
    ds = {
        "pins": [
            {"number": 1, "name": "VCC", "type": "power", "function": "Power supply input"},
            {"number": 2, "name": "GND", "type": "power", "function": "Ground"},
        ]
    }
    hints = suggest_net_names(ds)
    assert "VCC" in hints.values(), f"Expected VCC in hints, got {hints}"
    assert "GND" in hints.values(), f"Expected GND in hints, got {hints}"
    print("PASS test_net_namer_power_pins")


def test_net_namer_interface_pins() -> None:
    ds = {
        "pins": [
            {"number": 3, "name": "SDA", "type": "bidirectional", "function": "I2C data"},
            {"number": 4, "name": "SCL", "type": "input",        "function": "I2C clock"},
            {"number": 5, "name": "MOSI", "type": "input",       "function": "SPI master out"},
            {"number": 6, "name": "MISO", "type": "output",      "function": "SPI master in"},
        ]
    }
    hints = suggest_net_names(ds)
    values = set(hints.values())
    assert "I2C_SDA" in values, f"Expected I2C_SDA, got {hints}"
    assert "I2C_SCL" in values, f"Expected I2C_SCL, got {hints}"
    assert "SPI_MOSI" in values, f"Expected SPI_MOSI, got {hints}"
    assert "SPI_MISO" in values, f"Expected SPI_MISO, got {hints}"
    print("PASS test_net_namer_interface_pins")


def test_net_namer_nc_pins() -> None:
    ds = {
        "pins": [
            {"number": 7, "name": "NC", "type": "nc", "function": "No connect"},
        ]
    }
    hints = suggest_net_names(ds)
    assert "NC" in hints.values(), f"Expected NC in hints, got {hints}"
    print("PASS test_net_namer_nc_pins")


def test_net_namer_no_net001() -> None:
    ds = {
        "pins": [
            {"number": i, "name": f"PIN{i}", "type": "passive", "function": f"GPIO_{i}"}
            for i in range(1, 10)
        ]
    }
    hints = suggest_net_names(ds)
    bad = [v for v in hints.values() if _NET001_PATTERN.match(v)]
    assert not bad, f"net_namer produced NET001-style names: {bad}"
    print("PASS test_net_namer_no_net001")


# ---------------------------------------------------------------------------
# _extract_json_block tests
# ---------------------------------------------------------------------------

def test_extract_json_block_fenced() -> None:
    text = 'Here is your JSON:\n```json\n{"key": "value"}\n```\nDone.'
    extracted = _extract_json_block(text)
    data = json.loads(extracted)
    assert data == {"key": "value"}
    print("PASS test_extract_json_block_fenced")


def test_extract_json_block_bare() -> None:
    text = 'Some text {"key": "value"} more text'
    extracted = _extract_json_block(text)
    data = json.loads(extracted)
    assert data == {"key": "value"}
    print("PASS test_extract_json_block_bare")


def test_extract_json_block_raw() -> None:
    text = '{"key": "value"}'
    extracted = _extract_json_block(text)
    data = json.loads(extracted)
    assert data == {"key": "value"}
    print("PASS test_extract_json_block_raw")


# ---------------------------------------------------------------------------
# apply_schematic() tests
# ---------------------------------------------------------------------------

def test_apply_schematic_writes_file_and_returns_success() -> None:
    """apply_schematic() should write schematic.json to a temp dir."""
    sch = _valid_schematic()
    comp = _component_8pin()

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)

        # Patch COMPONENT_PATH so the floating-pin check uses our fixture
        with patch("agent.COMPONENT_PATH", new=tmp_path / "component.json"):
            # Write a component.json the function can load
            (tmp_path / "component.json").write_text(json.dumps(comp))

            result = apply_schematic(sch, output_dir=tmp_path)

        out_file = tmp_path / "schematic.json"
        assert out_file.exists(), "schematic.json was not written"
        written = json.loads(out_file.read_text())
        assert written["project_name"] == "Test Project"
        assert isinstance(result, dict), "apply_schematic must return a dict"
        assert "success" in result, "result must have 'success' key"
        assert "errors" in result, "result must have 'errors' key"

    print("PASS test_apply_schematic_writes_file_and_returns_success")


def test_apply_schematic_enforces_nc_for_floating_pins() -> None:
    """apply_schematic() must fill missing pins with NC automatically."""
    sch = _floating_pin_schematic()
    comp = _component_8pin()

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)

        with patch("agent.COMPONENT_PATH", new=tmp_path / "component.json"):
            (tmp_path / "component.json").write_text(json.dumps(comp))
            apply_schematic(sch, output_dir=tmp_path)

        written = json.loads((tmp_path / "schematic.json").read_text())

    all_connected, missing = _all_pin_numbers_connected(written, comp["symbol"]["pins"])
    assert all_connected, f"Floating pins remain after apply_schematic: {missing}"
    print("PASS test_apply_schematic_enforces_nc_for_floating_pins")


def test_apply_schematic_returns_dict_with_correct_keys() -> None:
    """apply_schematic() must return a dict with 'success' (bool) and 'errors' (list)."""
    sch = _valid_schematic()

    with tempfile.TemporaryDirectory() as tmp:
        result = apply_schematic(sch, output_dir=Path(tmp))

    assert isinstance(result["success"], bool), "'success' must be bool"
    assert isinstance(result["errors"], list), "'errors' must be list"
    print("PASS test_apply_schematic_returns_dict_with_correct_keys")


# ---------------------------------------------------------------------------
# get_design_context() tests
# ---------------------------------------------------------------------------

def _write_fixture_files(tmp: Path) -> None:
    """Write minimal datasheet.json and component.json to tmp for testing."""
    datasheet = {
        "pins": [
            {"number": 1, "name": "VCC", "type": "power", "function": "Supply"},
            {"number": 2, "name": "GND", "type": "power", "function": "Ground"},
            {"number": 3, "name": "SDA", "type": "bidirectional", "function": "I2C data"},
            {"number": 4, "name": "NC",  "type": "nc",   "function": "No connect"},
        ]
    }
    component = {
        "name": "TEST_IC",
        "symbol": {
            "pins": [
                {"number": i, "name": f"P{i}", "type": "passive"} for i in range(1, 5)
            ]
        }
    }
    (tmp / "datasheet.json").write_text(json.dumps(datasheet))
    (tmp / "component.json").write_text(json.dumps(component))


def test_get_design_context_returns_required_keys() -> None:
    """get_design_context() must return component_name, pins, example_nets, suggested_nets."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        _write_fixture_files(tmp_path)

        with (
            patch("agent.DATASHEET_PATH", new=tmp_path / "datasheet.json"),
            patch("agent.COMPONENT_PATH", new=tmp_path / "component.json"),
            patch("agent.EXAMPLE_SCH_PATH", new=tmp_path / "example_schematic.json"),
        ):
            ctx = get_design_context()

    assert "component_name" in ctx, "Missing 'component_name'"
    assert "pins" in ctx, "Missing 'pins'"
    assert "example_nets" in ctx, "Missing 'example_nets'"
    assert "suggested_nets" in ctx, "Missing 'suggested_nets'"
    print("PASS test_get_design_context_returns_required_keys")


def test_get_design_context_component_name() -> None:
    """get_design_context() extracts component name from component.json."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        _write_fixture_files(tmp_path)

        with (
            patch("agent.DATASHEET_PATH", new=tmp_path / "datasheet.json"),
            patch("agent.COMPONENT_PATH", new=tmp_path / "component.json"),
            patch("agent.EXAMPLE_SCH_PATH", new=tmp_path / "example_schematic.json"),
        ):
            ctx = get_design_context()

    assert ctx["component_name"] == "TEST_IC", (
        f"Expected 'TEST_IC', got '{ctx['component_name']}'"
    )
    print("PASS test_get_design_context_component_name")


def test_get_design_context_pins_list() -> None:
    """get_design_context() returns a list of pin dicts with required fields."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        _write_fixture_files(tmp_path)

        with (
            patch("agent.DATASHEET_PATH", new=tmp_path / "datasheet.json"),
            patch("agent.COMPONENT_PATH", new=tmp_path / "component.json"),
            patch("agent.EXAMPLE_SCH_PATH", new=tmp_path / "example_schematic.json"),
        ):
            ctx = get_design_context()

    pins = ctx["pins"]
    assert len(pins) == 4, f"Expected 4 pins, got {len(pins)}"
    for p in pins:
        assert "number" in p
        assert "name" in p
        assert "type" in p
        assert "function" in p
    print("PASS test_get_design_context_pins_list")


def test_get_design_context_suggested_nets_uses_net_namer() -> None:
    """get_design_context() 'suggested_nets' is non-empty and maps pin functions."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        _write_fixture_files(tmp_path)

        with (
            patch("agent.DATASHEET_PATH", new=tmp_path / "datasheet.json"),
            patch("agent.COMPONENT_PATH", new=tmp_path / "component.json"),
            patch("agent.EXAMPLE_SCH_PATH", new=tmp_path / "example_schematic.json"),
        ):
            ctx = get_design_context()

    suggested = ctx["suggested_nets"]
    assert isinstance(suggested, dict), "'suggested_nets' must be a dict"
    assert len(suggested) > 0, "'suggested_nets' must not be empty"
    # GND and VCC should be present in values
    values = set(suggested.values())
    assert "GND" in values, f"Expected GND in suggested nets, got {values}"
    print("PASS test_get_design_context_suggested_nets_uses_net_namer")


def test_get_design_context_example_nets_empty_when_file_missing() -> None:
    """get_design_context() returns empty example_nets when example_schematic.json absent."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        _write_fixture_files(tmp_path)
        # Intentionally do NOT write example_schematic.json

        with (
            patch("agent.DATASHEET_PATH", new=tmp_path / "datasheet.json"),
            patch("agent.COMPONENT_PATH", new=tmp_path / "component.json"),
            patch("agent.EXAMPLE_SCH_PATH", new=tmp_path / "example_schematic.json"),
        ):
            ctx = get_design_context()

    assert ctx["example_nets"] == [], (
        f"Expected empty list when file missing, got {ctx['example_nets']}"
    )
    print("PASS test_get_design_context_example_nets_empty_when_file_missing")


def test_get_design_context_example_nets_populated() -> None:
    """get_design_context() populates example_nets from example_schematic.json."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        _write_fixture_files(tmp_path)

        example_sch = {
            "nets": [
                {"name": "VCC_3V3", "type": "power"},
                {"name": "GND",     "type": "gnd"},
            ]
        }
        (tmp_path / "example_schematic.json").write_text(json.dumps(example_sch))

        with (
            patch("agent.DATASHEET_PATH", new=tmp_path / "datasheet.json"),
            patch("agent.COMPONENT_PATH", new=tmp_path / "component.json"),
            patch("agent.EXAMPLE_SCH_PATH", new=tmp_path / "example_schematic.json"),
        ):
            ctx = get_design_context()

    assert "VCC_3V3" in ctx["example_nets"]
    assert "GND" in ctx["example_nets"]
    print("PASS test_get_design_context_example_nets_populated")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

_ALL_TESTS = [
    # Existing schematic-logic tests
    test_no_net001_names_in_valid_schematic,
    test_net001_detected_in_bad_schematic,
    test_power_nets_include_voltage,
    test_power_net_without_voltage_detected,
    test_no_floating_pins_in_valid_schematic,
    test_ensure_no_floating_pins_fills_nc,
    test_floating_pin_schematic_detected_before_fix,
    # net_namer tests
    test_net_namer_power_pins,
    test_net_namer_interface_pins,
    test_net_namer_nc_pins,
    test_net_namer_no_net001,
    # _extract_json_block tests
    test_extract_json_block_fenced,
    test_extract_json_block_bare,
    test_extract_json_block_raw,
    # apply_schematic() tests
    test_apply_schematic_writes_file_and_returns_success,
    test_apply_schematic_enforces_nc_for_floating_pins,
    test_apply_schematic_returns_dict_with_correct_keys,
    # get_design_context() tests
    test_get_design_context_returns_required_keys,
    test_get_design_context_component_name,
    test_get_design_context_pins_list,
    test_get_design_context_suggested_nets_uses_net_namer,
    test_get_design_context_example_nets_empty_when_file_missing,
    test_get_design_context_example_nets_populated,
]


def main() -> None:
    failures: list[str] = []
    for test in _ALL_TESTS:
        try:
            test()
        except AssertionError as exc:
            print(f"FAIL {test.__name__}: {exc}")
            failures.append(test.__name__)
        except Exception as exc:  # noqa: BLE001
            print(f"ERROR {test.__name__}: {type(exc).__name__}: {exc}")
            failures.append(test.__name__)

    print()
    if failures:
        print(f"FAILED {len(failures)}/{len(_ALL_TESTS)} tests: {failures}")
        sys.exit(1)
    else:
        print(f"All {len(_ALL_TESTS)} tests passed.")


if __name__ == "__main__":
    main()
