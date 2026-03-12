"""
agents/footprint/test_agent.py

Tests for the footprint agent using a synthetic SOT-23 datasheet.

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
import textwrap
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

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

# ── A deterministic mock LLM response for SOT-23 ─────────────────────────
# IPC-7351 SOT-23 land pattern (IPC density level B / nominal):
#   Pad width (X): 1.0 mm + 0.1 tolerance = 1.1 mm
#   Pad height (Y): 1.4 mm + 0.1 tolerance = 1.5 mm
#   Pitch: 0.95 mm
#   Left pads at x=-0.95, right pad at x=+0.95
#   Courtyard: 3.0 mm x 2.8 mm (clears all pads by >= 0.25 mm)
MOCK_LLM_FOOTPRINT: dict[str, Any] = {
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
    # SOT-23 IPC-7351 land pattern (density level B):
    #   Pads 1 & 2 on the left column (x = -0.95), offset ±0.475 in Y
    #   Pad 3 on the right (x = +0.95), centred in Y
    #   Pad width = 1.0mm + 0.1mm tolerance = 1.1mm
    #   Pad height = 1.3mm + 0.1mm tolerance = 1.4mm
    #   Leftmost pad edge  = -0.95 - 0.55 = -1.50
    #   Rightmost pad edge = +0.95 + 0.55 = +1.50
    #   Top pad edge       = -0.475 - 0.70 = -1.175
    #   Bottom pad edge    = +0.475 + 0.70 = +1.175
    #   Courtyard must clear all by >= 0.25 mm:
    #     width  = (1.50 + 0.25)*2 = 3.50
    #     height = (1.175 + 0.25)*2 = 2.85  → use 2.90 for margin
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
        # width:  left pad left edge=-1.50, courtyard left = -1.75 → half=1.75 → width=3.50
        # height: top pad top=-1.175, courtyard top=-1.425 → half=1.425 → height=2.85
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


# ── Helpers ───────────────────────────────────────────────────────────────

def _make_mock_anthropic_client(response_json: dict) -> MagicMock:
    """Create a mock anthropic.Anthropic client that returns response_json."""
    mock_content = MagicMock()
    mock_content.text = json.dumps(response_json)

    mock_response = MagicMock()
    mock_response.content = [mock_content]

    mock_messages = MagicMock()
    mock_messages.create.return_value = mock_response

    mock_client = MagicMock()
    mock_client.messages = mock_messages

    return mock_client


# ── Tests ─────────────────────────────────────────────────────────────────

def test_kicad_mod_writer_produces_valid_sexpr():
    """kicad_mod_writer.write() must produce balanced S-expressions."""
    from kicad_mod_writer import write  # type: ignore

    content = write(MOCK_LLM_FOOTPRINT)

    # Check balanced parentheses
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

    content = write(MOCK_LLM_FOOTPRINT)

    assert '(footprint "SOT-23"' in content, "Missing footprint declaration"
    assert '(layer "F.Cu")' in content, "Missing layer declaration"
    assert '"Reference"' in content, "Missing Reference property"
    assert '"Value"' in content, "Missing Value property"
    assert '"F.CrtYd"' in content, "Missing courtyard layer"
    assert '"F.SilkS"' in content, "Missing silkscreen layer"
    assert '"F.Fab"' in content, "Missing fab layer"

    # All 3 pads must appear
    for pad_num in ("1", "2", "3"):
        assert f'(pad "{pad_num}"' in content, f"Missing pad {pad_num}"

    # Pin 1 must be rect shape
    assert '(pad "1" smd rect' in content, "Pin 1 must be rect (square) for identification"

    # SMD layers
    assert '"F.Cu" "F.Paste" "F.Mask"' in content, "SMD pad missing paste/mask layers"

    print("PASS: kicad_mod_writer contains all required elements")


def test_kicad_validator_accepts_output():
    """kicad_validator must accept the generated .kicad_mod file."""
    from kicad_mod_writer import write  # type: ignore
    import kicad_validator  # type: ignore

    content = write(MOCK_LLM_FOOTPRINT)

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".kicad_mod", delete=False, encoding="utf-8"
    ) as f:
        f.write(content)
        tmp_path = f.name

    try:
        # Redirect stdout to avoid Unicode console issues on Windows
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
    stripped = {k: v for k, v in MOCK_LLM_FOOTPRINT.items() if k in required_keys}

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


def test_agent_run_end_to_end_mocked():
    """
    Full agent.run() pipeline with mocked Anthropic client and temp directories.
    Verifies that footprint.json and footprint.kicad_mod are written and valid.
    """
    from kicad_mod_writer import write  # type: ignore
    import schema_validator  # type: ignore
    import kicad_validator  # type: ignore

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        outputs = tmp / "outputs"
        outputs.mkdir()

        # Write datasheet.json
        (outputs / "datasheet.json").write_text(
            json.dumps(SOT23_DATASHEET, indent=2), encoding="utf-8"
        )

        mock_client = _make_mock_anthropic_client(MOCK_LLM_FOOTPRINT)

        # Patch environment and anthropic.Anthropic constructor
        env_overrides = {
            "OUTPUT_DIR": str(outputs),
            "SCHEMA_DIR": str(_PROJECT_ROOT / "shared" / "schemas"),
            "ANTHROPIC_API_KEY": "test-key-mock",
        }

        import agent as agent_module  # type: ignore  # noqa: PLC0415

        # Temporarily re-point module-level path constants
        orig_output_dir = agent_module.OUTPUT_DIR
        orig_datasheet = agent_module.DATASHEET_JSON
        orig_footprint_json = agent_module.FOOTPRINT_JSON
        orig_footprint_kicad = agent_module.FOOTPRINT_KICAD

        agent_module.OUTPUT_DIR = outputs
        agent_module.DATASHEET_JSON = outputs / "datasheet.json"
        agent_module.FOOTPRINT_JSON = outputs / "footprint.json"
        agent_module.FOOTPRINT_KICAD = outputs / "footprint.kicad_mod"

        try:
            with patch("anthropic.Anthropic", return_value=mock_client):
                with patch.dict(os.environ, env_overrides):
                    result = agent_module.run()
        finally:
            agent_module.OUTPUT_DIR = orig_output_dir
            agent_module.DATASHEET_JSON = orig_datasheet
            agent_module.FOOTPRINT_JSON = orig_footprint_json
            agent_module.FOOTPRINT_KICAD = orig_footprint_kicad

        # Verify outputs exist
        assert (outputs / "footprint.json").exists(), "footprint.json was not written"
        assert (outputs / "footprint.kicad_mod").exists(), "footprint.kicad_mod was not written"

        # Verify footprint.json is valid JSON with expected keys
        with open(outputs / "footprint.json", encoding="utf-8") as f:
            fp = json.load(f)

        assert fp["name"] == "SOT-23", f"Unexpected name: {fp['name']}"
        assert len(fp["pads"]) == 3, f"Expected 3 pads, got {len(fp['pads'])}"
        assert fp["format"] == "kicad_mod"

        # Verify courtyard clears pads by >= 0.25mm
        cy = fp["courtyard"]
        hw = cy["width"] / 2.0
        hh = cy["height"] / 2.0
        for pad in fp["pads"]:
            pad_right = pad["x"] + pad["width"] / 2.0
            pad_left = pad["x"] - pad["width"] / 2.0
            pad_top = pad["y"] - pad["height"] / 2.0
            pad_bottom = pad["y"] + pad["height"] / 2.0

            cy_right = cy["x"] + hw
            cy_left = cy["x"] - hw
            cy_top = cy["y"] - hh
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

        # Validate kicad_mod
        kicad_ok = kicad_validator.validate_sexpr(str(outputs / "footprint.kicad_mod"))
        assert kicad_ok, "footprint.kicad_mod failed kicad_validator"

        print("PASS: end-to-end agent run produces valid outputs")
        return result


def test_pin1_is_rect_in_kicad_mod():
    """Pin 1 pad must be rendered as 'rect' in the kicad_mod (for identification)."""
    from kicad_mod_writer import write  # type: ignore

    content = write(MOCK_LLM_FOOTPRINT)
    assert '(pad "1" smd rect' in content, (
        "Pin 1 must be rect shape in .kicad_mod for manufacturing identification"
    )
    print("PASS: pin 1 is rect in kicad_mod")


def test_courtyard_dimensions_are_positive():
    """Courtyard dimensions must be positive floats."""
    cy = MOCK_LLM_FOOTPRINT["courtyard"]
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
        test_pin1_is_rect_in_kicad_mod,
        test_courtyard_dimensions_are_positive,
        test_agent_run_end_to_end_mocked,
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
