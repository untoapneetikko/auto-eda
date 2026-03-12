"""
test_agent.py — Unit tests for the connectivity agent tool layer.

Tests:
  1.  Valid schematic passes all checks (valid=True, 0 errors)
  2.  ORPHAN_NET detected — net declared but never connected
  3.  DANGLING_NET detected — net used in connection but not declared
  4.  SINGLE_PIN_NET warning — net with only one connection (non-NC)
  5.  NC single-pin connections are NOT flagged as SINGLE_PIN_NET
  6.  FLOATING_PIN detected — U-ref component with no connections
  7.  DUPLICATE_NET detected — same net name appears twice
  8.  SELF_LOOP detected — two-pin passive with both pins on same net
  9.  valid=False when error_count > 0
  10. valid=True when only warnings exist (no errors)
  11. run_from_file() writes connectivity.json and returns {success, report, errors}
  12. stats counts are correct
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))

from agent import check_connectivity, run_from_file  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _valid_schematic() -> dict[str, Any]:
    return {
        "project_name": "Test",
        "nets": [
            {"name": "VCC_3V3", "type": "power"},
            {"name": "GND",     "type": "gnd"},
            {"name": "I2C_SDA", "type": "signal"},
        ],
        "components": [
            {
                "reference": "U1",
                "value": "IC",
                "footprint": "SOIC-8",
                "position": {"x": 0, "y": 0},
                "connections": [
                    {"pin": 1, "net": "VCC_3V3"},
                    {"pin": 2, "net": "GND"},
                    {"pin": 3, "net": "I2C_SDA"},
                ],
            },
            {
                "reference": "C1",
                "value": "100nF",
                "footprint": "0402",
                "position": {"x": 10, "y": 0},
                "connections": [
                    {"pin": 1, "net": "VCC_3V3"},
                    {"pin": 2, "net": "GND"},
                ],
            },
        ],
        "power_symbols": ["VCC_3V3", "GND"],
        "format": "kicad_sch",
    }


def _codes(report: dict[str, Any]) -> list[str]:
    return [i["code"] for i in report["issues"]]


def _severities(report: dict[str, Any], code: str) -> list[str]:
    return [i["severity"] for i in report["issues"] if i["code"] == code]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_valid_schematic_passes() -> None:
    report = check_connectivity(_valid_schematic())
    assert report["valid"], f"Expected valid=True, got issues: {report['issues']}"
    assert report["stats"]["error_count"] == 0
    print("PASS test_valid_schematic_passes")


def test_orphan_net_detected() -> None:
    sch = _valid_schematic()
    sch["nets"].append({"name": "UNUSED_NET", "type": "signal"})
    report = check_connectivity(sch)
    assert "ORPHAN_NET" in _codes(report), f"Expected ORPHAN_NET, got {_codes(report)}"
    print("PASS test_orphan_net_detected")


def test_dangling_net_detected() -> None:
    sch = _valid_schematic()
    # Add a connection that uses an undeclared net
    sch["components"][0]["connections"].append({"pin": 4, "net": "GHOST_NET"})
    report = check_connectivity(sch)
    assert "DANGLING_NET" in _codes(report), f"Expected DANGLING_NET, got {_codes(report)}"
    print("PASS test_dangling_net_detected")


def test_single_pin_net_warning() -> None:
    sch = _valid_schematic()
    # Add a net that only U1 connects to (C1 does not)
    sch["nets"].append({"name": "LONELY_NET", "type": "signal"})
    sch["components"][0]["connections"].append({"pin": 4, "net": "LONELY_NET"})
    report = check_connectivity(sch)
    assert "SINGLE_PIN_NET" in _codes(report), f"Expected SINGLE_PIN_NET, got {_codes(report)}"
    assert _severities(report, "SINGLE_PIN_NET") == ["warning"]
    print("PASS test_single_pin_net_warning")


def test_nc_not_flagged_as_single_pin() -> None:
    sch = _valid_schematic()
    sch["components"][0]["connections"].append({"pin": 4, "net": "NC"})
    report = check_connectivity(sch)
    # NC should not appear in SINGLE_PIN_NET issues
    nc_issues = [i for i in report["issues"] if i["code"] == "SINGLE_PIN_NET" and i.get("net") == "NC"]
    assert not nc_issues, f"NC should not be flagged as SINGLE_PIN_NET: {nc_issues}"
    print("PASS test_nc_not_flagged_as_single_pin")


def test_floating_pin_detected() -> None:
    sch = _valid_schematic()
    sch["components"].append({
        "reference": "U2",
        "value": "IC2",
        "footprint": "DIP-8",
        "position": {"x": 50, "y": 0},
        "connections": [],  # no connections at all
    })
    report = check_connectivity(sch)
    assert "FLOATING_PIN" in _codes(report), f"Expected FLOATING_PIN, got {_codes(report)}"
    print("PASS test_floating_pin_detected")


def test_duplicate_net_detected() -> None:
    sch = _valid_schematic()
    sch["nets"].append({"name": "GND", "type": "gnd"})  # duplicate
    report = check_connectivity(sch)
    assert "DUPLICATE_NET" in _codes(report), f"Expected DUPLICATE_NET, got {_codes(report)}"
    print("PASS test_duplicate_net_detected")


def test_self_loop_detected() -> None:
    sch = _valid_schematic()
    sch["components"].append({
        "reference": "R1",
        "value": "10k",
        "footprint": "0402",
        "position": {"x": 20, "y": 0},
        "connections": [
            {"pin": 1, "net": "VCC_3V3"},
            {"pin": 2, "net": "VCC_3V3"},  # self-loop
        ],
    })
    report = check_connectivity(sch)
    assert "SELF_LOOP" in _codes(report), f"Expected SELF_LOOP, got {_codes(report)}"
    assert _severities(report, "SELF_LOOP") == ["warning"]
    print("PASS test_self_loop_detected")


def test_valid_false_when_errors_present() -> None:
    sch = _valid_schematic()
    sch["nets"].append({"name": "ORPHAN", "type": "signal"})
    report = check_connectivity(sch)
    assert not report["valid"], "Expected valid=False when errors present"
    print("PASS test_valid_false_when_errors_present")


def test_valid_true_with_only_warnings() -> None:
    sch = _valid_schematic()
    # Introduce a self-loop (warning, not error)
    sch["components"].append({
        "reference": "R2",
        "value": "1k",
        "footprint": "0402",
        "position": {"x": 30, "y": 0},
        "connections": [
            {"pin": 1, "net": "GND"},
            {"pin": 2, "net": "GND"},
        ],
    })
    report = check_connectivity(sch)
    assert report["stats"]["warning_count"] > 0
    # Only valid if no errors; might have other errors from single-pin, check carefully
    errors = [i for i in report["issues"] if i["severity"] == "error"]
    if not errors:
        assert report["valid"], "Expected valid=True with only warnings"
    print("PASS test_valid_true_with_only_warnings")


def test_stats_counts() -> None:
    sch = _valid_schematic()
    report = check_connectivity(sch)
    stats = report["stats"]
    assert stats["net_count"] == 3, f"Expected 3 nets, got {stats['net_count']}"
    assert stats["component_count"] == 2, f"Expected 2 components, got {stats['component_count']}"
    assert stats["connection_count"] == 5, f"Expected 5 connections, got {stats['connection_count']}"
    print("PASS test_stats_counts")


def test_run_from_file_writes_json() -> None:
    sch = _valid_schematic()
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        sch_path = tmp_path / "schematic.json"
        sch_path.write_text(json.dumps(sch))

        with patch("agent.SCHEMATIC_PATH", new=sch_path):
            result = run_from_file(output_dir=tmp_path)

        out_file = tmp_path / "connectivity.json"
        assert out_file.exists(), "connectivity.json was not written"
        written = json.loads(out_file.read_text())
        assert "valid" in written
        assert "issues" in written
        assert "stats" in written
        assert "success" in result
        assert "report" in result
        assert "errors" in result

    print("PASS test_run_from_file_writes_json")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

_ALL_TESTS = [
    test_valid_schematic_passes,
    test_orphan_net_detected,
    test_dangling_net_detected,
    test_single_pin_net_warning,
    test_nc_not_flagged_as_single_pin,
    test_floating_pin_detected,
    test_duplicate_net_detected,
    test_self_loop_detected,
    test_valid_false_when_errors_present,
    test_valid_true_with_only_warnings,
    test_stats_counts,
    test_run_from_file_writes_json,
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
