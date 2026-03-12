"""
agents/layout/test_agent.py — Test suite for the layout agent.

Tests:
  1. Board outline exists in .kicad_pcb (Edge.Cuts gr_rect)
  2. All four M3 mounting holes are present
  3. All nets from schematic appear in the PCB file
  4. .kicad_pcb has balanced parentheses (no S-expression corruption)
  5. final_layout.json is valid JSON and has required top-level keys
  6. KiCad version header is present
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

# Ensure the agents/layout directory is on the path so we can import the modules.
_AGENT_DIR = Path(__file__).parent
if str(_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(_AGENT_DIR))

from kicad_pcb_writer import KiCadPCBWriter

# ---------------------------------------------------------------------------
# Fixtures — minimal valid input data
# ---------------------------------------------------------------------------

PLACEMENT_DATA = {
    "board": {"width_mm": 80.0, "height_mm": 60.0},
    "placements": [
        {"reference": "U1", "x": 20.0, "y": 20.0, "rotation": 0.0, "layer": "F.Cu",
         "rationale": "center of board"},
        {"reference": "R1", "x": 50.0, "y": 30.0, "rotation": 0.0, "layer": "F.Cu",
         "rationale": "near U1"},
    ],
}

SCHEMATIC_DATA = {
    "project_name": "test-project",
    "nets": [
        {"name": "VCC",  "label": "VCC",  "type": "power"},
        {"name": "GND",  "label": "GND",  "type": "gnd"},
        {"name": "SIG1", "label": "SIG1", "type": "signal"},
    ],
    "components": [
        {
            "reference": "U1",
            "value": "LM358",
            "footprint": "SOIC-8",
            "position": {"x": 20.0, "y": 20.0},
            "connections": [
                {"pin": 1, "net": "SIG1"},
                {"pin": 8, "net": "VCC"},
                {"pin": 4, "net": "GND"},
            ],
        },
        {
            "reference": "R1",
            "value": "10k",
            "footprint": "R_0402",
            "position": {"x": 50.0, "y": 30.0},
            "connections": [
                {"pin": 1, "net": "SIG1"},
                {"pin": 2, "net": "GND"},
            ],
        },
    ],
    "power_symbols": ["VCC", "GND"],
    "format": "kicad_sch",
}

ROUTING_DATA = {
    "traces": [
        {
            "net": "VCC",
            "layer": "F.Cu",
            "width_mm": 0.4,
            "path": [{"x": 20.0, "y": 20.0}, {"x": 50.0, "y": 20.0}],
        },
        {
            "net": "GND",
            "layer": "F.Cu",
            "width_mm": 0.4,
            "path": [{"x": 20.0, "y": 30.0}, {"x": 50.0, "y": 30.0}],
        },
        {
            "net": "SIG1",
            "layer": "F.Cu",
            "width_mm": 0.2,
            "path": [{"x": 20.0, "y": 25.0}, {"x": 50.0, "y": 25.0}],
        },
    ],
    "vias": [
        {"net": "GND", "x": 35.0, "y": 30.0, "drill_mm": 0.3},
    ],
}

FOOTPRINT_DATA = {
    "name": "SOIC-8",
    "pads": [
        {"number": 1, "type": "smd", "shape": "rect",
         "x": -2.7, "y": -1.5, "width": 1.55, "height": 0.6, "drill": 0},
        {"number": 2, "type": "smd", "shape": "rect",
         "x": -2.7, "y": -0.5, "width": 1.55, "height": 0.6, "drill": 0},
        {"number": 3, "type": "smd", "shape": "rect",
         "x": -2.7, "y":  0.5, "width": 1.55, "height": 0.6, "drill": 0},
        {"number": 4, "type": "smd", "shape": "rect",
         "x": -2.7, "y":  1.5, "width": 1.55, "height": 0.6, "drill": 0},
        {"number": 5, "type": "smd", "shape": "rect",
         "x":  2.7, "y":  1.5, "width": 1.55, "height": 0.6, "drill": 0},
        {"number": 6, "type": "smd", "shape": "rect",
         "x":  2.7, "y":  0.5, "width": 1.55, "height": 0.6, "drill": 0},
        {"number": 7, "type": "smd", "shape": "rect",
         "x":  2.7, "y": -0.5, "width": 1.55, "height": 0.6, "drill": 0},
        {"number": 8, "type": "smd", "shape": "rect",
         "x":  2.7, "y": -1.5, "width": 1.55, "height": 0.6, "drill": 0},
    ],
    "courtyard": {"x": -3.5, "y": -2.0, "width": 7.0, "height": 4.0},
    "silkscreen": [],
    "format": "kicad_mod",
}


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _make_pcb() -> str:
    """Generate a .kicad_pcb string from the fixture data."""
    writer = KiCadPCBWriter(
        placement_data=PLACEMENT_DATA,
        routing_data=ROUTING_DATA,
        schematic_data=SCHEMATIC_DATA,
        footprint_data=FOOTPRINT_DATA,
        project_name="test-project",
    )
    return writer.write()


def _count_parens(text: str) -> tuple[int, int]:
    opens  = text.count("(")
    closes = text.count(")")
    return opens, closes


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_board_outline_exists():
    """Board outline (gr_rect on Edge.Cuts) must be present."""
    pcb = _make_pcb()
    assert "Edge.Cuts" in pcb, "Board outline layer Edge.Cuts not found in PCB file"
    assert "gr_rect" in pcb, "gr_rect (board outline) not found in PCB file"
    print("PASS: board outline exists")


def test_mounting_holes_present():
    """All four M3 mounting holes must be present."""
    pcb = _make_pcb()
    for ref in ("MH1", "MH2", "MH3", "MH4"):
        assert ref in pcb, f"Mounting hole {ref} not found in PCB file"
    assert pcb.count("MountingHole") >= 4, "Expected at least 4 mounting hole footprints"
    assert pcb.count("np_thru_hole") >= 4, "Expected at least 4 NPTH drill pads for M3 holes"
    print("PASS: mounting holes present (MH1–MH4)")


def test_all_schematic_nets_in_pcb():
    """Every net declared in the schematic must appear in the PCB file."""
    pcb = _make_pcb()
    for net in SCHEMATIC_DATA["nets"]:
        name = net["name"]
        assert f'"{name}"' in pcb, f"Net '{name}' from schematic not found in PCB file"
    print("PASS: all schematic nets present in PCB file")


def test_balanced_parentheses():
    """S-expression parentheses must be balanced (no corruption)."""
    pcb = _make_pcb()
    depth = 0
    for i, ch in enumerate(pcb):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            assert depth >= 0, (
                f"Unmatched closing parenthesis at character position {i}. "
                f"Context: {pcb[max(0,i-40):i+40]!r}"
            )
    assert depth == 0, (
        f"PCB file has {depth} unclosed parenthesis/parentheses at end of file"
    )
    opens, closes = _count_parens(pcb)
    print(f"PASS: balanced parentheses ({opens} open = {closes} close)")


def test_kicad_version_header():
    """kicad_pcb and version header must be the first token."""
    pcb = _make_pcb()
    assert pcb.startswith("(kicad_pcb"), "File does not start with (kicad_pcb …)"
    assert "version 20231120" in pcb, "Missing KiCad version stamp (version 20231120)"
    print("PASS: KiCad version header present")


def test_final_layout_json_structure():
    """
    Run the writer and verify that final_layout.json has the required keys
    when written by the agent's _build_final_layout_json helper.
    """
    # Import agent helpers without invoking a live Claude call
    import importlib
    agent_mod = importlib.import_module("agent")

    ai_review_stub = {
        "project_name": "test-project",
        "board_notes":  "Looks good.",
        "drc_notes":    "None.",
        "layer_notes":  "2-layer FR4 1.6mm",
    }

    layout_json = agent_mod._build_final_layout_json(
        PLACEMENT_DATA, ROUTING_DATA, SCHEMATIC_DATA, FOOTPRINT_DATA, ai_review_stub
    )

    required_keys = {"schema_version", "generated_at", "project_name", "board", "stats", "ai_review", "drc"}
    missing = required_keys - set(layout_json.keys())
    assert not missing, f"final_layout.json missing keys: {missing}"

    stats = layout_json["stats"]
    assert stats["components"] == 2,      "Expected 2 components"
    assert stats["nets_total"]  == 3,      "Expected 3 nets"
    assert stats["mounting_holes"] == 4,   "Expected 4 mounting holes"
    assert stats["traces"] == 3,           "Expected 3 traces"
    assert stats["vias"]   == 1,           "Expected 1 via"
    print("PASS: final_layout.json structure and stats correct")


def test_write_to_temp_dir():
    """End-to-end: write PCB file to a temp dir and verify it exists and is non-empty."""
    pcb_content = _make_pcb()
    with tempfile.TemporaryDirectory() as tmpdir:
        pcb_path = Path(tmpdir) / "final_layout.kicad_pcb"
        pcb_path.write_text(pcb_content, encoding="utf-8")

        assert pcb_path.exists(), ".kicad_pcb file was not written"
        size = pcb_path.stat().st_size
        assert size > 500, f".kicad_pcb is suspiciously small: {size} bytes"
        print(f"PASS: wrote {size} bytes to {pcb_path.name}")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_all_tests() -> bool:
    tests = [
        test_board_outline_exists,
        test_mounting_holes_present,
        test_all_schematic_nets_in_pcb,
        test_balanced_parentheses,
        test_kicad_version_header,
        test_final_layout_json_structure,
        test_write_to_temp_dir,
    ]

    passed = 0
    failed = 0

    for test_fn in tests:
        try:
            test_fn()
            passed += 1
        except AssertionError as exc:
            print(f"FAIL: {test_fn.__name__} — {exc}")
            failed += 1
        except Exception as exc:
            print(f"ERROR: {test_fn.__name__} — {type(exc).__name__}: {exc}")
            failed += 1

    print(f"\n{'='*50}")
    print(f"Results: {passed} passed, {failed} failed out of {len(tests)} tests")
    return failed == 0


if __name__ == "__main__":
    ok = run_all_tests()
    sys.exit(0 if ok else 1)
