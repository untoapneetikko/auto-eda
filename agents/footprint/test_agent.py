"""
agents/footprint/test_agent.py

Tests for the footprint agent tools layer.
No Anthropic SDK involved — Claude Code handles reasoning; Python is tools only.

Run with:
  cd /path/to/pcb-agent-footprint
  python -m pytest agents/footprint/test_agent.py -v
  # or directly:
  python agents/footprint/test_agent.py
"""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import patch

# ── Add project root + tools to sys.path ──────────────────────────────────
_HERE = Path(__file__).resolve().parent
_PROJECT_ROOT = _HERE.parent.parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_PROJECT_ROOT / "backend" / "tools"))

# ── Sample SOT-23 datasheet (MMBT2222A NPN transistor) ────────────────────
SOT23_DATASHEET: dict[str, Any] = {
    "component_name": "MMBT2222A",
    "manufacturer": "ON Semiconductor",
    "package": "SOT-23",
    "pins": [
        {"number": 1, "name": "BASE", "type": "input", "function": "Base terminal"},
        {"number": 2, "name": "EMITTER", "type": "passive", "function": "Emitter terminal"},
        {"number": 3, "name": "COLLECTOR", "type": "output", "function": "Collector terminal"},
    ],
    "footprint": {
        "standard": "SOT-23",
        "pad_count": 3,
        "pitch_mm": 0.95,
        "courtyard_mm": {"x": 1.8, "y": 2.9},
    },
    "electrical": {
        "vcc_min": None,
        "vcc_max": 40.0,
        "i_max_ma": 600.0,
    },
    "example_application": {
        "description": "General-purpose NPN switching transistor",
        "required_passives": ["10k base resistor", "1k collector resistor"],
        "typical_schematic_notes": "Base drive from MCU GPIO through resistor",
    },
    "raw_text": "MMBT2222A NPN transistor SOT-23 package",
    "source_pdf": "data/uploads/mmbt2222a.pdf",
}

# ── Known-good SOT-23 footprint dict (orchestrator-designed) ─────────────
# IPC-7351 SOT-23 land pattern (IPC density level B / nominal):
#   Pad width (X): 1.0 mm + 0.1 tolerance = 1.1 mm
#   Pad height (Y): 1.3 mm + 0.1 tolerance = 1.4 mm
#   Pitch: 0.95 mm
#   Left pads at x=-0.95, right pad at x=+0.95
#   Courtyard: 3.50 mm x 2.85 mm (clears all pads by >= 0.25 mm)
SOT23_FOOTPRINT: dict[str, Any] = {
    "name": "SOT-23",
    "package": "SOT-23",
    "ipc_standard": "IPC-7351B",
    "pad_count": 3,
    "pitch_mm": 0.95,
    "sources": {
        "pitch_mm": "datasheet",
        "pad_width": "IPC-7351 nominal + 0.1mm tolerance",
        "pad_height": "IPC-7351 nominal + 0.1mm tolerance",
        "courtyard": "IPC-7351 nominal + 0.25mm clearance",
    },
    "pads": [
        {
            "number": 1,
            "type": "smd",
            "shape": "rect",   # square for pin 1 identification
            "x": -0.95,
            "y": -0.475,
            "width": 1.1,
            "height": 1.4,
            "drill": None,
        },
        {
            "number": 2,
            "type": "smd",
            "shape": "oval",
            "x": -0.95,
            "y": 0.475,
            "width": 1.1,
            "height": 1.4,
            "drill": None,
        },
        {
            "number": 3,
            "type": "smd",
            "shape": "oval",
            "x": 0.95,
            "y": 0.0,
            "width": 1.1,
            "height": 1.4,
            "drill": None,
        },
    ],
    "courtyard": {
        "x": 0.0,
        "y": 0.0,
        "width": 3.50,
        "height": 2.85,
    },
    "silkscreen": [
        # Pin 1 marker: short line segment above-left of pad 1 (outside pad area)
        {"type": "line", "data": {"x1": -1.5, "y1": -1.6, "x2": -0.4, "y2": -1.6}},
        # Component body outline (between the two pad columns, on F.SilkS)
        {"type": "line", "data": {"x1": -0.4, "y1": -1.6, "x2": 0.4, "y2": -1.6}},
        {"type": "line", "data": {"x1": 0.4, "y1": -1.6, "x2": 0.4, "y2": 1.6}},
        {"type": "line", "data": {"x1": 0.4, "y1": 1.6, "x2": -0.4, "y2": 1.6}},
        {"type": "line", "data": {"x1": -0.4, "y1": 1.6, "x2": -1.5, "y2": 1.6}},
    ],
    "format": "kicad_mod",
}


# ── kicad_mod_writer tests ────────────────────────────────────────────────

def test_kicad_mod_writer_produces_valid_sexpr():
    """kicad_mod_writer.write() must produce balanced S-expressions."""
    from kicad_mod_writer import write  # type: ignore

    content = write(SOT23_FOOTPRINT)

    depth = 0
    for ch in content:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            assert depth >= 0, "Unmatched closing parenthesis found"
    assert depth == 0, f"Unclosed parentheses: depth={depth}"

    print("PASS: kicad_mod_writer produces valid S-expressions")


def test_kicad_mod_writer_contains_required_elements():
    """kicad_mod_writer output must contain key KiCad tokens."""
    from kicad_mod_writer import write  # type: ignore

    content = write(SOT23_FOOTPRINT)

    assert '(footprint "SOT-23"' in content, "Missing footprint declaration"
    assert '(layer "F.Cu")' in content, "Missing layer declaration"
    assert '"Reference"' in content, "Missing Reference property"
    assert '"Value"' in content, "Missing Value property"
    assert '"F.CrtYd"' in content, "Missing courtyard layer"
    assert '"F.SilkS"' in content, "Missing silkscreen layer"
    assert '"F.Fab"' in content, "Missing fab layer"

    for pad_num in ("1", "2", "3"):
        assert f'(pad "{pad_num}"' in content, f"Missing pad {pad_num}"

    assert '(pad "1" smd rect' in content, "Pin 1 must be rect (square) for identification"
    assert '"F.Cu" "F.Paste" "F.Mask"' in content, "SMD pad missing paste/mask layers"

    print("PASS: kicad_mod_writer contains all required elements")


def test_kicad_validator_accepts_output():
    """kicad_validator must accept the generated .kicad_mod file."""
    from kicad_mod_writer import write  # type: ignore
    import kicad_validator  # type: ignore

    content = write(SOT23_FOOTPRINT)

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".kicad_mod", delete=False, encoding="utf-8"
    ) as f:
        f.write(content)
        tmp_path = f.name

    try:
        buf = io.StringIO()
        with patch("builtins.print", lambda *a, **k: buf.write(" ".join(str(x) for x in a) + "\n")):
            result = kicad_validator.validate_sexpr(tmp_path)
        assert result is True, f"kicad_validator rejected the .kicad_mod file. Output: {buf.getvalue()}"
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    print("PASS: kicad_validator accepts generated .kicad_mod")


def test_schema_validator_accepts_footprint_json():
    """schema_validator must accept footprint.json stripped to required keys."""
    import schema_validator  # type: ignore

    required_keys = {"name", "pads", "courtyard", "silkscreen", "format"}
    stripped = {k: v for k, v in SOT23_FOOTPRINT.items() if k in required_keys}

    schema_path = _PROJECT_ROOT / "shared" / "schemas" / "footprint_output.json"
    assert schema_path.exists(), f"Schema not found: {schema_path}"

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    ) as f:
        json.dump(stripped, f)
        tmp_path = f.name

    try:
        buf = io.StringIO()
        with patch("builtins.print", lambda *a, **k: buf.write(" ".join(str(x) for x in a) + "\n")):
            result = schema_validator.validate(tmp_path, str(schema_path))
        assert result is True, f"schema_validator rejected footprint.json. Output: {buf.getvalue()}"
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    print("PASS: schema_validator accepts footprint.json")


# ── get_datasheet_summary tests ───────────────────────────────────────────

def test_get_datasheet_summary_returns_compact_dict():
    """get_datasheet_summary() must return all expected keys from a datasheet file."""
    import agent as agent_module  # type: ignore

    with tempfile.TemporaryDirectory() as tmpdir:
        ds_path = Path(tmpdir) / "datasheet.json"
        ds_path.write_text(json.dumps(SOT23_DATASHEET, indent=2), encoding="utf-8")

        summary = agent_module.get_datasheet_summary(ds_path)

    assert summary["component_name"] == "MMBT2222A", f"Wrong name: {summary['component_name']}"
    assert summary["package"] == "SOT-23", f"Wrong package: {summary['package']}"
    assert summary["pad_count"] == 3, f"Wrong pad_count: {summary['pad_count']}"
    assert summary["pitch_mm"] == 0.95, f"Wrong pitch_mm: {summary['pitch_mm']}"
    assert summary["courtyard_mm"] == {"x": 1.8, "y": 2.9}, (
        f"Wrong courtyard_mm: {summary['courtyard_mm']}"
    )
    assert isinstance(summary["electrical"], dict), "electrical must be a dict"

    print("PASS: get_datasheet_summary returns compact dict")


def test_get_datasheet_summary_missing_file_raises():
    """get_datasheet_summary() must raise FileNotFoundError when file is absent."""
    import agent as agent_module  # type: ignore

    try:
        agent_module.get_datasheet_summary("/nonexistent/path/datasheet.json")
        assert False, "Expected FileNotFoundError"
    except FileNotFoundError:
        pass

    print("PASS: get_datasheet_summary raises FileNotFoundError for missing file")


def test_get_datasheet_summary_fallback_fields():
    """get_datasheet_summary() must fall back gracefully when footprint block is absent."""
    import agent as agent_module  # type: ignore

    minimal_ds = {
        "component_name": "MyChip",
        "package": "DIP-8",
        "pins": [{"number": i} for i in range(1, 9)],
    }

    with tempfile.TemporaryDirectory() as tmpdir:
        ds_path = Path(tmpdir) / "datasheet.json"
        ds_path.write_text(json.dumps(minimal_ds), encoding="utf-8")

        summary = agent_module.get_datasheet_summary(ds_path)

    assert summary["package"] == "DIP-8"
    assert summary["pad_count"] == 8
    assert summary["pitch_mm"] is None
    assert summary["courtyard_mm"] is None

    print("PASS: get_datasheet_summary falls back gracefully")


# ── apply_footprint tests ─────────────────────────────────────────────────

def test_apply_footprint_sot23_success():
    """apply_footprint() with a known-good SOT-23 dict must write valid outputs."""
    import agent as agent_module  # type: ignore

    with tempfile.TemporaryDirectory() as tmpdir:
        result = agent_module.apply_footprint(SOT23_FOOTPRINT, output_dir=tmpdir)

    assert result["success"] is True, f"apply_footprint failed: {result['errors']}"
    assert len(result["errors"]) == 0, f"Unexpected errors: {result['errors']}"

    # Both artefacts must be listed in files
    file_names = [Path(p).name for p in result["files"]]
    assert "footprint.json" in file_names, f"footprint.json not in files: {result['files']}"
    assert "footprint.kicad_mod" in file_names, f"footprint.kicad_mod not in files: {result['files']}"

    print("PASS: apply_footprint SOT-23 success")


def test_apply_footprint_writes_valid_json():
    """apply_footprint() must write a valid JSON file with the expected keys."""
    import agent as agent_module  # type: ignore

    with tempfile.TemporaryDirectory() as tmpdir:
        result = agent_module.apply_footprint(SOT23_FOOTPRINT, output_dir=tmpdir)
        assert result["success"], f"apply_footprint failed: {result['errors']}"

        with open(Path(tmpdir) / "footprint.json", encoding="utf-8") as f:
            fp = json.load(f)

    assert fp["name"] == "SOT-23", f"Wrong name: {fp['name']}"
    assert len(fp["pads"]) == 3, f"Expected 3 pads, got {len(fp['pads'])}"
    assert fp["format"] == "kicad_mod"
    # Extra keys (package, ipc_standard, sources) must be preserved
    assert fp.get("package") == "SOT-23", "Extra key 'package' should be preserved"
    assert "sources" in fp, "Extra key 'sources' should be preserved"

    print("PASS: apply_footprint writes valid JSON with preserved keys")


def test_apply_footprint_writes_valid_kicad_mod():
    """apply_footprint() must produce a .kicad_mod that passes kicad_validator."""
    import agent as agent_module  # type: ignore
    import kicad_validator  # type: ignore

    with tempfile.TemporaryDirectory() as tmpdir:
        result = agent_module.apply_footprint(SOT23_FOOTPRINT, output_dir=tmpdir)
        assert result["success"], f"apply_footprint failed: {result['errors']}"

        kicad_path = Path(tmpdir) / "footprint.kicad_mod"
        buf = io.StringIO()
        with patch("builtins.print", lambda *a, **k: buf.write(" ".join(str(x) for x in a) + "\n")):
            ok = kicad_validator.validate_sexpr(str(kicad_path))

    assert ok, f"footprint.kicad_mod failed KiCad validation: {buf.getvalue()}"
    print("PASS: apply_footprint writes valid .kicad_mod")


def test_apply_footprint_courtyard_clears_pads():
    """Courtyard in SOT23_FOOTPRINT must clear all pads by >= 0.25 mm."""
    import agent as agent_module  # type: ignore

    with tempfile.TemporaryDirectory() as tmpdir:
        result = agent_module.apply_footprint(SOT23_FOOTPRINT, output_dir=tmpdir)
        assert result["success"], f"apply_footprint failed: {result['errors']}"

        with open(Path(tmpdir) / "footprint.json", encoding="utf-8") as f:
            fp = json.load(f)

    cy = fp["courtyard"]
    hw = cy["width"] / 2.0
    hh = cy["height"] / 2.0

    for pad in fp["pads"]:
        pad_right  = pad["x"] + pad["width"] / 2.0
        pad_left   = pad["x"] - pad["width"] / 2.0
        pad_top    = pad["y"] - pad["height"] / 2.0
        pad_bottom = pad["y"] + pad["height"] / 2.0

        cy_right  = cy["x"] + hw
        cy_left   = cy["x"] - hw
        cy_top    = cy["y"] - hh
        cy_bottom = cy["y"] + hh

        assert cy_right - pad_right >= 0.24, (
            f"Pad {pad['number']} right edge too close to courtyard: "
            f"clearance={cy_right - pad_right:.3f}mm < 0.25mm"
        )
        assert pad_left - cy_left >= 0.24, (
            f"Pad {pad['number']} left edge too close to courtyard: "
            f"clearance={pad_left - cy_left:.3f}mm < 0.25mm"
        )
        assert pad_top - cy_top >= 0.24, (
            f"Pad {pad['number']} top edge too close to courtyard: "
            f"clearance={pad_top - cy_top:.3f}mm < 0.25mm"
        )
        assert cy_bottom - pad_bottom >= 0.24, (
            f"Pad {pad['number']} bottom edge too close to courtyard: "
            f"clearance={cy_bottom - pad_bottom:.3f}mm < 0.25mm"
        )

    print("PASS: courtyard clears all pads by >= 0.25mm")


def test_apply_footprint_adds_default_keys():
    """apply_footprint() must add 'format' and 'silkscreen' if absent."""
    import agent as agent_module  # type: ignore

    minimal = {
        "name": "SOT-23",
        "pads": SOT23_FOOTPRINT["pads"],
        "courtyard": SOT23_FOOTPRINT["courtyard"],
        # no 'format', no 'silkscreen'
    }

    with tempfile.TemporaryDirectory() as tmpdir:
        result = agent_module.apply_footprint(minimal, output_dir=tmpdir)
        assert result["success"], f"apply_footprint failed: {result['errors']}"

        with open(Path(tmpdir) / "footprint.json", encoding="utf-8") as f:
            fp = json.load(f)

    assert fp["format"] == "kicad_mod", "format default not added"
    assert fp["silkscreen"] == [], "silkscreen default not added"

    print("PASS: apply_footprint adds default keys")


def test_run_raises_not_implemented():
    """run() must raise NotImplementedError (orchestration moved to Claude Code)."""
    import agent as agent_module  # type: ignore

    try:
        agent_module.run()
        assert False, "Expected NotImplementedError"
    except NotImplementedError:
        pass

    print("PASS: run() raises NotImplementedError")


def test_pin1_is_rect_in_kicad_mod():
    """Pin 1 pad must be rendered as 'rect' in the kicad_mod (for identification)."""
    from kicad_mod_writer import write  # type: ignore

    content = write(SOT23_FOOTPRINT)
    assert '(pad "1" smd rect' in content, (
        "Pin 1 must be rect shape in .kicad_mod for manufacturing identification"
    )
    print("PASS: pin 1 is rect in kicad_mod")


def test_courtyard_dimensions_are_positive():
    """Courtyard dimensions must be positive floats."""
    cy = SOT23_FOOTPRINT["courtyard"]
    assert cy["width"] > 0, "Courtyard width must be positive"
    assert cy["height"] > 0, "Courtyard height must be positive"
    print("PASS: courtyard dimensions are positive")


# ── Test runner ───────────────────────────────────────────────────────────

def run_all_tests() -> None:
    tests = [
        test_kicad_mod_writer_produces_valid_sexpr,
        test_kicad_mod_writer_contains_required_elements,
        test_kicad_validator_accepts_output,
        test_schema_validator_accepts_footprint_json,
        test_get_datasheet_summary_returns_compact_dict,
        test_get_datasheet_summary_missing_file_raises,
        test_get_datasheet_summary_fallback_fields,
        test_apply_footprint_sot23_success,
        test_apply_footprint_writes_valid_json,
        test_apply_footprint_writes_valid_kicad_mod,
        test_apply_footprint_courtyard_clears_pads,
        test_apply_footprint_adds_default_keys,
        test_run_raises_not_implemented,
        test_pin1_is_rect_in_kicad_mod,
        test_courtyard_dimensions_are_positive,
    ]

    passed = 0
    failed = 0
    errors: list[str] = []

    for test_fn in tests:
        try:
            test_fn()
            passed += 1
        except Exception as exc:  # noqa: BLE001
            failed += 1
            errors.append(f"{test_fn.__name__}: {exc}")
            print(f"FAIL: {test_fn.__name__}: {exc}")

    print(f"\n{'='*60}")
    print(f"Results: {passed} passed, {failed} failed out of {len(tests)} tests")
    if errors:
        print("\nFailed tests:")
        for err in errors:
            print(f"  - {err}")
    print("=" * 60)

    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    run_all_tests()
