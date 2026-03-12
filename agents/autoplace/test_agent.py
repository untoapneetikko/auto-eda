"""
Test suite for the Auto-Place agent.

Tests:
    1. All schematic components appear in placement output.
    2. Every placement has a non-trivial rationale string.
    3. No courtyard overlaps detected by the placement optimizer.
    4. Connectors are placed at (or very near) the board edge.

Run from the repository root:
    python -m pytest agents/autoplace/test_agent.py -v
or directly:
    python agents/autoplace/test_agent.py
"""
from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Ensure repo root is on the path when running directly.
_REPO_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from agents.autoplace.placement_optimizer import (
    check_courtyard_clearances,
    summarize_violations,
)

# ---------------------------------------------------------------------------
# Sample schematic: 8 components covering all placement categories
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

# ---------------------------------------------------------------------------
# A placement that correctly follows all rules — used for the optimizer tests
# ---------------------------------------------------------------------------

VALID_PLACEMENTS = [
    # Connectors at left edge
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
    # Power entry near left, away from sensitive analog
    {
        "reference": "U2",
        "x": 15.0, "y": 10.0, "rotation": 0.0, "layer": "F.Cu",
        "footprint": "Package_TO_SOT_THT:TO-220-3_Vertical",
        "rationale": "LM7805 regulator placed near power entry (J1/VCC) with 3mm margin for thermal airflow clearance",
    },
    # Main IC in center of functional block
    {
        "reference": "U1",
        "x": 50.0, "y": 40.0, "rotation": 0.0, "layer": "F.Cu",
        "footprint": "Package_DIP:DIP-28_W7.62mm",
        "rationale": "ATmega328P placed in center of board to equalise trace lengths to I2C connector and output",
    },
    # Decoupling caps close to U1 VCC pin
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
    # Signal passives adjacent to the pin they serve
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

# ---------------------------------------------------------------------------
# Connector edge threshold for tests
# ---------------------------------------------------------------------------

EDGE_MARGIN_MM = 5.0  # connectors must be within this distance of a board edge


def _is_at_edge(x: float, y: float, board_w: float = BOARD_WIDTH, board_h: float = BOARD_HEIGHT) -> bool:
    return (
        x <= EDGE_MARGIN_MM
        or x >= board_w - EDGE_MARGIN_MM
        or y <= EDGE_MARGIN_MM
        or y >= board_h - EDGE_MARGIN_MM
    )


# ---------------------------------------------------------------------------
# Tests for placement_optimizer
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
        # Both references should appear in the violation message
        msg = result["violations"][0]["message"]
        self.assertIn("C1", msg)
        self.assertIn("C2", msg)

    def test_just_within_clearance_passes(self) -> None:
        """Components separated by exactly 0.25 mm (plus courtyard expansion) should pass."""
        # 0402 is 1.0 x 0.5 mm, half_w=0.6, half_h=0.35 → courtyard edge at x+0.6
        # Place two side-by-side with 1.7 mm gap between centres → gap_x = 1.7 - 1.2 = 0.5 ≥ 0.25 ✓
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
        """An overlap should produce a negative gap_mm value."""
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
# Tests for agent output shape (run with mocked Anthropic client)
# ---------------------------------------------------------------------------

class TestAgentOutput(unittest.TestCase):

    def _make_mock_response(self, placements: list[dict]) -> MagicMock:
        """Build a mock Anthropic message response."""
        payload = {
            "board": {"width_mm": BOARD_WIDTH, "height_mm": BOARD_HEIGHT},
            "placements": placements,
        }
        msg = MagicMock()
        msg.content = [MagicMock()]
        msg.content[0].text = json.dumps(payload)
        return msg

    @patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"})
    @patch("anthropic.Anthropic")
    def test_all_components_placed(self, mock_anthropic_cls: MagicMock) -> None:
        """Every component in the schematic must appear in the output."""
        import tempfile

        client_mock = MagicMock()
        mock_anthropic_cls.return_value = client_mock
        client_mock.messages.create.return_value = self._make_mock_response(VALID_PLACEMENTS)

        from agents.autoplace.agent import run_placement  # noqa: PLC0415

        with tempfile.TemporaryDirectory() as tmp:
            with patch("agents.autoplace.agent.OUTPUT_DIR", Path(tmp)), \
                 patch("agents.autoplace.agent.PLACEMENT_FILE", Path(tmp) / "placement.json"), \
                 patch("agents.autoplace.agent._run_drc", return_value=(True, "DRC PASS")):
                result = run_placement(schematic=SAMPLE_SCHEMATIC)

        placed_refs = {p["reference"] for p in result["placements"]}
        expected_refs = {c["reference"] for c in SAMPLE_SCHEMATIC["components"]}
        self.assertEqual(placed_refs, expected_refs, f"Missing: {expected_refs - placed_refs}")

    @patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"})
    @patch("anthropic.Anthropic")
    def test_all_placements_have_rationale(self, mock_anthropic_cls: MagicMock) -> None:
        """Every placement must carry a non-trivial rationale string."""
        import tempfile

        client_mock = MagicMock()
        mock_anthropic_cls.return_value = client_mock
        client_mock.messages.create.return_value = self._make_mock_response(VALID_PLACEMENTS)

        from agents.autoplace.agent import run_placement  # noqa: PLC0415

        with tempfile.TemporaryDirectory() as tmp:
            with patch("agents.autoplace.agent.OUTPUT_DIR", Path(tmp)), \
                 patch("agents.autoplace.agent.PLACEMENT_FILE", Path(tmp) / "placement.json"), \
                 patch("agents.autoplace.agent._run_drc", return_value=(True, "DRC PASS")):
                result = run_placement(schematic=SAMPLE_SCHEMATIC)

        for p in result["placements"]:
            rationale = p.get("rationale", "").strip()
            self.assertTrue(
                len(rationale) >= 20,
                f"{p['reference']}: rationale too short or missing: '{rationale}'",
            )
            self.assertNotIn(
                rationale.lower(), {"placed here", "here", ""},
                f"{p['reference']}: rationale is trivially bad: '{rationale}'",
            )

    @patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"})
    @patch("anthropic.Anthropic")
    def test_no_courtyard_overlaps(self, mock_anthropic_cls: MagicMock) -> None:
        """The placement optimizer must report no violations for VALID_PLACEMENTS."""
        import tempfile

        client_mock = MagicMock()
        mock_anthropic_cls.return_value = client_mock
        client_mock.messages.create.return_value = self._make_mock_response(VALID_PLACEMENTS)

        from agents.autoplace.agent import run_placement  # noqa: PLC0415

        with tempfile.TemporaryDirectory() as tmp:
            with patch("agents.autoplace.agent.OUTPUT_DIR", Path(tmp)), \
                 patch("agents.autoplace.agent.PLACEMENT_FILE", Path(tmp) / "placement.json"), \
                 patch("agents.autoplace.agent._run_drc", return_value=(True, "DRC PASS")):
                result = run_placement(schematic=SAMPLE_SCHEMATIC)

        enriched = [
            dict(p, footprint=next(
                (c["footprint"] for c in SAMPLE_SCHEMATIC["components"] if c["reference"] == p["reference"]),
                "",
            ))
            for p in result["placements"]
        ]
        opt_result = check_courtyard_clearances(enriched)
        self.assertTrue(opt_result["is_valid"], summarize_violations(opt_result))

    @patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"})
    @patch("anthropic.Anthropic")
    def test_connectors_at_board_edge(self, mock_anthropic_cls: MagicMock) -> None:
        """Connector references (J*, P*) must be placed at or near a board edge."""
        import tempfile

        client_mock = MagicMock()
        mock_anthropic_cls.return_value = client_mock
        client_mock.messages.create.return_value = self._make_mock_response(VALID_PLACEMENTS)

        from agents.autoplace.agent import run_placement  # noqa: PLC0415

        with tempfile.TemporaryDirectory() as tmp:
            with patch("agents.autoplace.agent.OUTPUT_DIR", Path(tmp)), \
                 patch("agents.autoplace.agent.PLACEMENT_FILE", Path(tmp) / "placement.json"), \
                 patch("agents.autoplace.agent._run_drc", return_value=(True, "DRC PASS")):
                result = run_placement(schematic=SAMPLE_SCHEMATIC)

        connector_refs = [
            c["reference"]
            for c in SAMPLE_SCHEMATIC["components"]
            if c["reference"].startswith(("J", "P"))
            or any(k in c.get("footprint", "").upper() for k in ("CONN", "USB", "HDR", "HEADER"))
        ]

        placement_map = {p["reference"]: p for p in result["placements"]}
        for ref in connector_refs:
            p = placement_map.get(ref)
            self.assertIsNotNone(p, f"Connector {ref} not found in placements")
            self.assertTrue(
                _is_at_edge(p["x"], p["y"]),
                f"Connector {ref} at ({p['x']}, {p['y']}) is not near board edge "
                f"(board {BOARD_WIDTH}×{BOARD_HEIGHT}, margin {EDGE_MARGIN_MM}mm)",
            )


# ---------------------------------------------------------------------------
# Tests for the FastAPI endpoint (without starting a live server)
# ---------------------------------------------------------------------------

class TestEndpoint(unittest.TestCase):

    def test_status_idle_when_no_jobs(self) -> None:
        """GET /status with no jobs and no placement file should return idle."""
        from fastapi.testclient import TestClient
        from fastapi import FastAPI
        from agents.autoplace.endpoint import router, _state  # noqa: PLC0415

        # Reset internal state for a clean test
        with _state._lock:
            _state._jobs.clear()

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        with patch("agents.autoplace.endpoint.PLACEMENT_FILE", Path("/nonexistent/placement.json")):
            resp = client.get("/agents/autoplace/status")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "idle")

    def test_run_returns_job_id(self) -> None:
        """POST /run should return a job_id."""
        from fastapi.testclient import TestClient
        from fastapi import FastAPI
        from agents.autoplace.endpoint import router  # noqa: PLC0415

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)

        with patch("agents.autoplace.endpoint._run_agent"):
            resp = client.post(
                "/agents/autoplace/run",
                json={"board_width_mm": 100.0, "board_height_mm": 80.0},
            )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("job_id", data)
        self.assertEqual(data["status"], "queued")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main(verbosity=2)
