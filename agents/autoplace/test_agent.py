"""
Test suite for the Auto-Place agent.

Tests:
    1. placement_optimizer — courtyard clearance checks (Polars logic, no LLM).
    2. get_placement_context — schematic parsing and context structure.
    3. apply_placement — end-to-end validation + DRC + file write.
    4. FastAPI endpoints — context and apply routes.

Run from the repository root:
    python -m pytest agents/autoplace/test_agent.py -v
or directly:
    python agents/autoplace/test_agent.py
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# Ensure repo root is on the path when running directly.
_REPO_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from agents.autoplace.placement_optimizer import (
    check_courtyard_clearances,
    summarize_violations,
)

# ---------------------------------------------------------------------------
# Sample data shared across test classes
# ---------------------------------------------------------------------------

BOARD_WIDTH = 100.0
BOARD_HEIGHT = 80.0

SAMPLE_SCHEMATIC: dict = {
    "project_name": "test_board",
    "format": "kicad_sch",
    "nets": [
        {"name": "VCC", "label": "VCC", "type": "power"},
        {"name": "GND", "label": "GND", "type": "gnd"},
        {"name": "SDA", "label": "SDA", "type": "signal"},
        {"name": "SCL", "label": "SCL", "type": "signal"},
        {"name": "OUT", "label": "OUT", "type": "signal"},
    ],
    "power_symbols": ["VCC", "GND"],
    "components": [
        {
            "reference": "J1",
            "value": "USB-C",
            "footprint": "Connector_USB:USB_C_Receptacle_GCT_USB4085",
            "position": {"x": 0.0, "y": 0.0},
            "connections": [
                {"pin": 1, "net": "VCC"},
                {"pin": 2, "net": "GND"},
            ],
        },
        {
            "reference": "J2",
            "value": "Header 2x3",
            "footprint": "Connector_PinHeader_2.54mm:PinHeader_2x03_P2.54mm_Vertical",
            "position": {"x": 0.0, "y": 0.0},
            "connections": [
                {"pin": 1, "net": "SDA"},
                {"pin": 2, "net": "SCL"},
                {"pin": 3, "net": "GND"},
            ],
        },
        {
            "reference": "U1",
            "value": "ATmega328P",
            "footprint": "Package_DIP:DIP-28_W7.62mm",
            "position": {"x": 0.0, "y": 0.0},
            "connections": [
                {"pin": 7,  "net": "VCC"},
                {"pin": 8,  "net": "GND"},
                {"pin": 23, "net": "SDA"},
                {"pin": 24, "net": "SCL"},
                {"pin": 15, "net": "OUT"},
            ],
        },
        {
            "reference": "U2",
            "value": "LM7805",
            "footprint": "Package_TO_SOT_THT:TO-220-3_Vertical",
            "position": {"x": 0.0, "y": 0.0},
            "connections": [
                {"pin": 1, "net": "VCC"},
                {"pin": 2, "net": "GND"},
            ],
        },
        {
            "reference": "C1",
            "value": "100nF",
            "footprint": "Capacitor_SMD:C_0402_1005Metric",
            "position": {"x": 0.0, "y": 0.0},
            "connections": [
                {"pin": 1, "net": "VCC"},
                {"pin": 2, "net": "GND"},
            ],
        },
        {
            "reference": "C2",
            "value": "10uF",
            "footprint": "Capacitor_SMD:C_0805_2012Metric",
            "position": {"x": 0.0, "y": 0.0},
            "connections": [
                {"pin": 1, "net": "VCC"},
                {"pin": 2, "net": "GND"},
            ],
        },
        {
            "reference": "R1",
            "value": "10k",
            "footprint": "Resistor_SMD:R_0402_1005Metric",
            "position": {"x": 0.0, "y": 0.0},
            "connections": [
                {"pin": 1, "net": "SDA"},
                {"pin": 2, "net": "VCC"},
            ],
        },
        {
            "reference": "R2",
            "value": "10k",
            "footprint": "Resistor_SMD:R_0402_1005Metric",
            "position": {"x": 0.0, "y": 0.0},
            "connections": [
                {"pin": 1, "net": "SCL"},
                {"pin": 2, "net": "VCC"},
            ],
        },
    ],
}

# A well-spread placement that passes courtyard checks.
VALID_PLACEMENTS = [
    {
        "reference": "J1",
        "x": 2.0, "y": 15.0, "rotation": 0.0, "layer": "F.Cu",
        "footprint": "Connector_USB:USB_C_Receptacle_GCT_USB4085",
        "rationale": "USB-C connector placed at left board edge for direct cable access, minimising strain on traces",
    },
    {
        "reference": "J2",
        "x": 2.0, "y": 35.0, "rotation": 0.0, "layer": "F.Cu",
        "footprint": "Connector_PinHeader_2.54mm:PinHeader_2x03_P2.54mm_Vertical",
        "rationale": "I2C header placed at left edge near J1 for short SDA/SCL stub traces before entering board",
    },
    {
        "reference": "U2",
        "x": 15.0, "y": 10.0, "rotation": 0.0, "layer": "F.Cu",
        "footprint": "Package_TO_SOT_THT:TO-220-3_Vertical",
        "rationale": "LM7805 regulator placed near power entry (J1/VCC) with 3mm margin for thermal airflow clearance",
    },
    {
        "reference": "U1",
        "x": 50.0, "y": 40.0, "rotation": 0.0, "layer": "F.Cu",
        "footprint": "Package_DIP:DIP-28_W7.62mm",
        "rationale": "ATmega328P placed in center of board to equalise trace lengths to I2C connector and output",
    },
    {
        "reference": "C1",
        "x": 42.0, "y": 34.0, "rotation": 0.0, "layer": "F.Cu",
        "footprint": "Capacitor_SMD:C_0402_1005Metric",
        "rationale": "100nF decoupling cap placed within 0.4mm of U1 pin 7 (VCC) to suppress high-frequency noise on supply",
    },
    {
        "reference": "C2",
        "x": 15.0, "y": 20.0, "rotation": 0.0, "layer": "F.Cu",
        "footprint": "Capacitor_SMD:C_0805_2012Metric",
        "rationale": "10uF bulk cap placed adjacent to U2 output (LM7805) to stabilise low-frequency voltage ripple",
    },
    {
        "reference": "R1",
        "x": 38.0, "y": 40.0, "rotation": 90.0, "layer": "F.Cu",
        "footprint": "Resistor_SMD:R_0402_1005Metric",
        "rationale": "I2C SDA pull-up R1 placed directly between J2 pin 1 and VCC rail, keeping stub length < 3mm",
    },
    {
        "reference": "R2",
        "x": 38.0, "y": 46.0, "rotation": 90.0, "layer": "F.Cu",
        "footprint": "Resistor_SMD:R_0402_1005Metric",
        "rationale": "I2C SCL pull-up R2 placed adjacent to R1 for identical stub geometry, matching impedance on both lines",
    },
]

VALID_PLACEMENT_DICT: dict = {
    "board": {"width_mm": BOARD_WIDTH, "height_mm": BOARD_HEIGHT},
    "placements": VALID_PLACEMENTS,
}

EDGE_MARGIN_MM = 5.0  # connectors must be within this distance of a board edge


def _is_at_edge(x: float, y: float, board_w: float = BOARD_WIDTH, board_h: float = BOARD_HEIGHT) -> bool:
    return (
        x <= EDGE_MARGIN_MM
        or x >= board_w - EDGE_MARGIN_MM
        or y <= EDGE_MARGIN_MM
        or y >= board_h - EDGE_MARGIN_MM
    )


# ---------------------------------------------------------------------------
# Tests for placement_optimizer (pure Polars logic, no LLM)
# ---------------------------------------------------------------------------

class TestPlacementOptimizer(unittest.TestCase):

    def test_valid_placements_pass(self) -> None:
        """Well-spaced placements should produce no violations."""
        result = check_courtyard_clearances(VALID_PLACEMENTS)
        self.assertTrue(result["is_valid"], summarize_violations(result))
        self.assertEqual(len(result["violations"]), 0)

    def test_overlapping_placements_fail(self) -> None:
        """Two components at the same position should fail."""
        bad = [
            {
                "reference": "C1", "x": 50.0, "y": 50.0, "rotation": 0.0,
                "footprint": "Capacitor_SMD:C_0402_1005Metric",
            },
            {
                "reference": "C2", "x": 50.0, "y": 50.0, "rotation": 0.0,
                "footprint": "Capacitor_SMD:C_0402_1005Metric",
            },
        ]
        result = check_courtyard_clearances(bad)
        self.assertFalse(result["is_valid"])
        self.assertGreater(len(result["violations"]), 0)
        msg = result["violations"][0]["message"]
        self.assertIn("C1", msg)
        self.assertIn("C2", msg)

    def test_just_within_clearance_passes(self) -> None:
        """Components separated by >= 0.25 mm (plus courtyard expansion) should pass."""
        ok = [
            {
                "reference": "R1", "x": 10.0, "y": 10.0, "rotation": 0.0,
                "footprint": "Resistor_SMD:R_0402_1005Metric",
            },
            {
                "reference": "R2", "x": 11.7, "y": 10.0, "rotation": 0.0,
                "footprint": "Resistor_SMD:R_0402_1005Metric",
            },
        ]
        result = check_courtyard_clearances(ok)
        self.assertTrue(result["is_valid"], summarize_violations(result))

    def test_empty_placements(self) -> None:
        """Empty list should return valid."""
        result = check_courtyard_clearances([])
        self.assertTrue(result["is_valid"])

    def test_violation_gap_is_negative_for_overlap(self) -> None:
        """An overlap should produce a gap_mm below the minimum clearance."""
        bad = [
            {
                "reference": "U1", "x": 50.0, "y": 50.0, "rotation": 0.0,
                "footprint": "Package_DIP:DIP-28_W7.62mm",
            },
            {
                "reference": "U2", "x": 51.0, "y": 50.0, "rotation": 0.0,
                "footprint": "Package_DIP:DIP-28_W7.62mm",
            },
        ]
        result = check_courtyard_clearances(bad)
        self.assertFalse(result["is_valid"])
        self.assertLess(result["violations"][0]["gap_mm"], 0.25)


# ---------------------------------------------------------------------------
# Tests for get_placement_context
# ---------------------------------------------------------------------------

class TestGetPlacementContext(unittest.TestCase):

    def _write_schematic(self, tmp_dir: str) -> Path:
        path = Path(tmp_dir) / "schematic.json"
        path.write_text(json.dumps(SAMPLE_SCHEMATIC))
        return path

    def test_returns_required_keys(self) -> None:
        """Context dict must have project_name, board, components, net_connections."""
        from agents.autoplace.agent import get_placement_context  # noqa: PLC0415

        with tempfile.TemporaryDirectory() as tmp:
            path = self._write_schematic(tmp)
            ctx = get_placement_context(schematic_path=path)

        self.assertIn("project_name", ctx)
        self.assertIn("board", ctx)
        self.assertIn("components", ctx)
        self.assertIn("net_connections", ctx)

    def test_board_dimensions(self) -> None:
        """Board dimensions are passed through correctly."""
        from agents.autoplace.agent import get_placement_context  # noqa: PLC0415

        with tempfile.TemporaryDirectory() as tmp:
            path = self._write_schematic(tmp)
            ctx = get_placement_context(schematic_path=path, board_width_mm=120.0, board_height_mm=90.0)

        self.assertEqual(ctx["board"]["width_mm"], 120.0)
        self.assertEqual(ctx["board"]["height_mm"], 90.0)

    def test_all_components_present(self) -> None:
        """Every component in the schematic must appear in the context."""
        from agents.autoplace.agent import get_placement_context  # noqa: PLC0415

        with tempfile.TemporaryDirectory() as tmp:
            path = self._write_schematic(tmp)
            ctx = get_placement_context(schematic_path=path)

        expected_refs = {c["reference"] for c in SAMPLE_SCHEMATIC["components"]}
        actual_refs = {c["reference"] for c in ctx["components"]}
        self.assertEqual(actual_refs, expected_refs)

    def test_components_have_value_and_footprint(self) -> None:
        """Each component entry must carry value and footprint strings."""
        from agents.autoplace.agent import get_placement_context  # noqa: PLC0415

        with tempfile.TemporaryDirectory() as tmp:
            path = self._write_schematic(tmp)
            ctx = get_placement_context(schematic_path=path)

        for comp in ctx["components"]:
            self.assertIn("value", comp)
            self.assertIn("footprint", comp)

    def test_net_connections_populated(self) -> None:
        """net_connections must include known nets from the schematic."""
        from agents.autoplace.agent import get_placement_context  # noqa: PLC0415

        with tempfile.TemporaryDirectory() as tmp:
            path = self._write_schematic(tmp)
            ctx = get_placement_context(schematic_path=path)

        self.assertIn("VCC", ctx["net_connections"])
        self.assertIn("GND", ctx["net_connections"])
        # VCC net must reference at least one component-pin string
        vcc_pads = ctx["net_connections"]["VCC"]
        self.assertTrue(len(vcc_pads) >= 1)
        self.assertTrue(all("." in pad for pad in vcc_pads))

    def test_file_not_found_raises(self) -> None:
        """Missing schematic file must raise FileNotFoundError."""
        from agents.autoplace.agent import get_placement_context  # noqa: PLC0415

        with self.assertRaises(FileNotFoundError):
            get_placement_context(schematic_path="/nonexistent/schematic.json")


# ---------------------------------------------------------------------------
# Tests for apply_placement
# ---------------------------------------------------------------------------

class TestApplyPlacement(unittest.TestCase):

    def test_valid_placement_succeeds(self) -> None:
        """A well-formed placement with no courtyard violations must succeed (DRC mocked)."""
        from agents.autoplace.agent import apply_placement  # noqa: PLC0415

        with tempfile.TemporaryDirectory() as tmp:
            with patch("agents.autoplace.agent._run_drc", return_value=(True, "DRC PASS")):
                result = apply_placement(
                    placement_dict=VALID_PLACEMENT_DICT,
                    output_dir=tmp,
                )

        self.assertTrue(result["success"])
        self.assertEqual(result["violations"], [])
        self.assertTrue(result["drc_passed"])
        self.assertIsNotNone(result["placement_path"])
        # Verify the file was written
        self.assertTrue(Path(result["placement_path"]).exists())

    def test_placement_file_content(self) -> None:
        """Written placement.json must match the input dict."""
        from agents.autoplace.agent import apply_placement  # noqa: PLC0415

        with tempfile.TemporaryDirectory() as tmp:
            with patch("agents.autoplace.agent._run_drc", return_value=(True, "DRC PASS")):
                result = apply_placement(
                    placement_dict=VALID_PLACEMENT_DICT,
                    output_dir=tmp,
                )

        with open(result["placement_path"]) as f:
            written = json.load(f)
        self.assertEqual(written["board"], VALID_PLACEMENT_DICT["board"])
        self.assertEqual(len(written["placements"]), len(VALID_PLACEMENT_DICT["placements"]))

    def test_courtyard_violation_fails_before_write(self) -> None:
        """Overlapping components must fail courtyard check; no file written."""
        from agents.autoplace.agent import apply_placement  # noqa: PLC0415

        bad_dict = {
            "board": {"width_mm": BOARD_WIDTH, "height_mm": BOARD_HEIGHT},
            "placements": [
                {
                    "reference": "C1", "x": 50.0, "y": 50.0, "rotation": 0.0, "layer": "F.Cu",
                    "footprint": "Capacitor_SMD:C_0402_1005Metric",
                    "rationale": "placed here",
                },
                {
                    "reference": "C2", "x": 50.0, "y": 50.0, "rotation": 0.0, "layer": "F.Cu",
                    "footprint": "Capacitor_SMD:C_0402_1005Metric",
                    "rationale": "placed here",
                },
            ],
        }

        with tempfile.TemporaryDirectory() as tmp:
            result = apply_placement(placement_dict=bad_dict, output_dir=tmp)

        self.assertFalse(result["success"])
        self.assertGreater(len(result["violations"]), 0)
        self.assertIsNone(result["placement_path"])
        # File must NOT have been written
        self.assertFalse((Path(tmp) / "placement.json").exists())

    def test_drc_failure_reports_correctly(self) -> None:
        """If DRC fails, success=False and the output is captured."""
        from agents.autoplace.agent import apply_placement  # noqa: PLC0415

        with tempfile.TemporaryDirectory() as tmp:
            with patch(
                "agents.autoplace.agent._run_drc",
                return_value=(False, "ERROR: clearance violation on net VCC"),
            ):
                result = apply_placement(
                    placement_dict=VALID_PLACEMENT_DICT,
                    output_dir=tmp,
                )

        self.assertFalse(result["success"])
        self.assertFalse(result["drc_passed"])
        self.assertIn("clearance violation", result["drc_output"])
        # File should still be written even if DRC fails
        self.assertIsNotNone(result["placement_path"])
        self.assertTrue(Path(result["placement_path"]).exists())

    def test_run_raises_not_implemented(self) -> None:
        """run() must raise NotImplementedError."""
        from agents.autoplace.agent import run  # noqa: PLC0415

        with self.assertRaises(NotImplementedError):
            run()


# ---------------------------------------------------------------------------
# Tests for FastAPI endpoints
# ---------------------------------------------------------------------------

class TestEndpoint(unittest.TestCase):

    def _make_app(self):
        from fastapi import FastAPI
        from agents.autoplace.endpoint import router  # noqa: PLC0415
        app = FastAPI()
        app.include_router(router)
        return app

    def test_context_endpoint_with_valid_schematic(self) -> None:
        """GET /context with a valid schematic should return 200 and context."""
        from fastapi.testclient import TestClient

        with tempfile.TemporaryDirectory() as tmp:
            sch_path = Path(tmp) / "schematic.json"
            sch_path.write_text(json.dumps(SAMPLE_SCHEMATIC))

            client = TestClient(self._make_app())
            resp = client.get(
                "/agents/autoplace/context",
                params={"schematic_path": str(sch_path), "board_width_mm": 100.0, "board_height_mm": 80.0},
            )

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("components", data)
        self.assertIn("net_connections", data)
        self.assertEqual(data["project_name"], "test_board")

    def test_context_endpoint_missing_file(self) -> None:
        """GET /context with a non-existent schematic path should return 404."""
        from fastapi.testclient import TestClient

        client = TestClient(self._make_app())
        resp = client.get(
            "/agents/autoplace/context",
            params={"schematic_path": "/nonexistent/schematic.json"},
        )
        self.assertEqual(resp.status_code, 404)

    def test_apply_endpoint_valid(self) -> None:
        """POST /apply with a valid placement dict should return 200 success."""
        from fastapi.testclient import TestClient

        client = TestClient(self._make_app())
        with patch("agents.autoplace.agent._run_drc", return_value=(True, "DRC PASS")), \
             patch("agents.autoplace.agent.OUTPUT_DIR", Path(tempfile.mkdtemp())):
            resp = client.post(
                "/agents/autoplace/apply",
                json={
                    "placement_dict": VALID_PLACEMENT_DICT,
                    "board_width_mm": BOARD_WIDTH,
                    "board_height_mm": BOARD_HEIGHT,
                },
            )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["violations"], [])

    def test_apply_endpoint_overlapping(self) -> None:
        """POST /apply with overlapping components should return 200 but success=False."""
        from fastapi.testclient import TestClient

        bad_dict = {
            "board": {"width_mm": BOARD_WIDTH, "height_mm": BOARD_HEIGHT},
            "placements": [
                {
                    "reference": "U1", "x": 50.0, "y": 50.0, "rotation": 0.0, "layer": "F.Cu",
                    "footprint": "Package_DIP:DIP-28_W7.62mm",
                    "rationale": "center of board",
                },
                {
                    "reference": "U2", "x": 50.5, "y": 50.0, "rotation": 0.0, "layer": "F.Cu",
                    "footprint": "Package_DIP:DIP-28_W7.62mm",
                    "rationale": "also center of board",
                },
            ],
        }

        client = TestClient(self._make_app())
        resp = client.post(
            "/agents/autoplace/apply",
            json={"placement_dict": bad_dict},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertFalse(data["success"])
        self.assertGreater(len(data["violations"]), 0)

    def test_run_endpoint_returns_501(self) -> None:
        """POST /run must return 501 Not Implemented."""
        from fastapi.testclient import TestClient

        client = TestClient(self._make_app())
        resp = client.post("/agents/autoplace/run")
        self.assertEqual(resp.status_code, 501)

    def test_status_endpoint_returns_501(self) -> None:
        """GET /status must return 501 Not Implemented."""
        from fastapi.testclient import TestClient

        client = TestClient(self._make_app())
        resp = client.get("/agents/autoplace/status")
        self.assertEqual(resp.status_code, 501)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main(verbosity=2)
