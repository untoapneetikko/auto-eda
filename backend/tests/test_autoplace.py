"""Tests for backend.engines.autoplace — force-directed component placement.

Covers: _positions_are_spread, _load_schematic_hints basics.
Note: run_autoplace requires the agents.autoplace module to be importable,
so full integration tests require the agent environment.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest
from backend.engines.autoplace import _positions_are_spread, _load_schematic_hints


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestPositionsAreSpread:
    def test_single_component(self):
        """Single component is considered spread (nothing to compare)."""
        assert _positions_are_spread([{"x": 50, "y": 50}]) is True

    def test_stacked_components(self):
        """Components all at the same position are NOT spread."""
        comps = [{"x": 50, "y": 50} for _ in range(10)]
        assert _positions_are_spread(comps) is False

    def test_spread_components(self):
        """Components with varied positions ARE spread."""
        comps = [
            {"x": 10, "y": 10},
            {"x": 30, "y": 20},
            {"x": 50, "y": 40},
            {"x": 70, "y": 60},
        ]
        assert _positions_are_spread(comps) is True

    def test_nearly_stacked(self):
        """Components within 2mm of each other are NOT spread."""
        comps = [
            {"x": 50, "y": 50},
            {"x": 51, "y": 50},
            {"x": 50, "y": 51},
        ]
        assert _positions_are_spread(comps) is False

    def test_majority_stacked(self):
        """If majority share same position, not spread even if a few differ."""
        comps = [
            {"x": 50, "y": 50},
            {"x": 50, "y": 50},
            {"x": 50, "y": 50},
            {"x": 50, "y": 50},
            {"x": 50, "y": 50},
            {"x": 50, "y": 50},
            {"x": 50, "y": 50},
            {"x": 50, "y": 50},
            {"x": 10, "y": 10},
            {"x": 90, "y": 90},
        ]
        assert _positions_are_spread(comps) is False


class TestLoadSchematicHints:
    def test_missing_project_returns_empty(self):
        """Non-existent project file returns empty hints."""
        with tempfile.TemporaryDirectory() as tmpdir:
            hints = _load_schematic_hints(
                "nonexistent", 100, 80,
                projects_dir=Path(tmpdir),
            )
            assert hints == {}

    def test_loads_component_positions(self):
        """Valid project file returns normalised hints."""
        with tempfile.TemporaryDirectory() as tmpdir:
            proj = {
                "components": [
                    {"designator": "R1", "x": 100, "y": 200, "symType": "resistor"},
                    {"designator": "C1", "x": 300, "y": 200, "symType": "capacitor"},
                ],
            }
            proj_path = Path(tmpdir) / "test.json"
            proj_path.write_text(json.dumps(proj))

            hints = _load_schematic_hints(
                "test", 100, 80,
                projects_dir=Path(tmpdir),
            )
            assert "R1" in hints
            assert "C1" in hints
            # Positions should be within board bounds
            for ref, (hx, hy) in hints.items():
                assert 0 <= hx <= 100
                assert 0 <= hy <= 80

    def test_skips_power_symbols(self):
        """Power/GND symbols are excluded from hints."""
        with tempfile.TemporaryDirectory() as tmpdir:
            proj = {
                "components": [
                    {"designator": "R1", "x": 100, "y": 200, "symType": "resistor"},
                    {"designator": "VCC1", "x": 100, "y": 100, "symType": "vcc"},
                    {"designator": "GND1", "x": 100, "y": 300, "symType": "gnd"},
                ],
            }
            proj_path = Path(tmpdir) / "test.json"
            proj_path.write_text(json.dumps(proj))

            hints = _load_schematic_hints(
                "test", 100, 80,
                projects_dir=Path(tmpdir),
            )
            assert "R1" in hints
            assert "VCC1" not in hints
            assert "GND1" not in hints
