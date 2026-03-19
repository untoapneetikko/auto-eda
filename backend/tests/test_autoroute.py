"""Tests for backend.engines.autoroute — multi-layer A* PCB router.

Covers: small board routing, net-skip logic, power trace width, skip-net detection.
"""
from __future__ import annotations

import pytest
from backend.engines.autoroute import run_autoroute, _autoroute_skip_net, _autoroute_trace_width


# ── Helpers ───────────────────────────────────────────────────────────────────

def _board_with_2_pads(net_name="SIG", bw=50, bh=50):
    """Board with two components, each with one pad on the same net."""
    return {
        "board": {"width": bw, "height": bh},
        "designRules": {
            "clearance": 0.2,
            "minTraceWidth": 0.15,
            "traceWidth": 0.25,
            "edgeClearance": 0.5,
            "viaSize": 1.0,
            "viaDrill": 0.6,
            "routingGrid": 0.5,
            "allowVias": True,
        },
        "components": [
            {
                "id": "R1", "ref": "R1", "x": 15, "y": 25,
                "rotation": 0, "layer": "F",
                "pads": [
                    {"number": "1", "name": "1", "x": -2, "y": 0, "net": net_name,
                     "size_x": 1.0, "size_y": 1.0, "type": "smd"},
                    {"number": "2", "name": "2", "x": 2, "y": 0, "net": "OTHER",
                     "size_x": 1.0, "size_y": 1.0, "type": "smd"},
                ],
            },
            {
                "id": "R2", "ref": "R2", "x": 35, "y": 25,
                "rotation": 0, "layer": "F",
                "pads": [
                    {"number": "1", "name": "1", "x": -2, "y": 0, "net": net_name,
                     "size_x": 1.0, "size_y": 1.0, "type": "smd"},
                    {"number": "2", "name": "2", "x": 2, "y": 0, "net": "OTHER",
                     "size_x": 1.0, "size_y": 1.0, "type": "smd"},
                ],
            },
        ],
        "nets": [
            {"name": net_name, "pads": ["R1.1", "R2.1"]},
            {"name": "OTHER", "pads": ["R1.2", "R2.2"]},
        ],
        "traces": [],
        "vias": [],
        "zones": [],
        "areas": [],
    }


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestAutorouteSkipNet:
    def test_empty_net_skipped(self):
        assert _autoroute_skip_net("") is True

    def test_gnd_skipped(self):
        assert _autoroute_skip_net("GND") is True
        assert _autoroute_skip_net("AGND") is True
        assert _autoroute_skip_net("DGND") is True
        assert _autoroute_skip_net("GND_DIGITAL") is True

    def test_nc_skipped(self):
        assert _autoroute_skip_net("NC") is True
        assert _autoroute_skip_net("NC_1") is True

    def test_signal_not_skipped(self):
        assert _autoroute_skip_net("SIG") is False
        assert _autoroute_skip_net("CLK") is False
        assert _autoroute_skip_net("VCC") is False


class TestAutorouteTraceWidth:
    def test_signal_default_width(self):
        dr = {"traceWidth": 0.25}
        assert _autoroute_trace_width("SIG", dr) == 0.25

    def test_power_wider(self):
        dr = {"traceWidth": 0.25, "powerTraceWidth": 0.4}
        w = _autoroute_trace_width("VCC", dr)
        assert w >= 0.4

    def test_power_keywords(self):
        dr = {"traceWidth": 0.25, "powerTraceWidth": 0.5}
        for kw in ("VCC", "VDD", "3V3", "5V", "12V", "VIN"):
            assert _autoroute_trace_width(kw, dr) >= 0.5


class TestRunAutoroute:
    def test_routes_simple_net(self):
        """Two pads on the same net get routed with at least one trace."""
        board = _board_with_2_pads("SIG")
        result = run_autoroute(board)
        meta = result.get("_autoroute", {})
        assert meta["total"] >= 1
        assert meta["routed"] >= 1
        # Should have new traces
        assert len(result["traces"]) >= 1

    def test_gnd_net_not_routed(self):
        """GND nets are skipped (handled by copper pour)."""
        board = _board_with_2_pads("GND")
        result = run_autoroute(board)
        meta = result.get("_autoroute", {})
        # GND is skipped but OTHER net still counts
        # Check that no trace has net=GND
        gnd_traces = [t for t in result["traces"] if t.get("net") == "GND"]
        assert len(gnd_traces) == 0

    def test_existing_traces_preserved(self):
        """Pre-existing traces are kept in the output."""
        board = _board_with_2_pads("SIG")
        existing_trace = {
            "net": "EXISTING", "layer": "F.Cu", "width": 0.3,
            "segments": [{"start": {"x": 5, "y": 5}, "end": {"x": 10, "y": 5}}],
        }
        board["traces"] = [existing_trace]
        result = run_autoroute(board)
        # The existing trace should still be in the output
        existing_found = any(
            t.get("net") == "EXISTING" for t in result["traces"]
        )
        assert existing_found

    def test_net_names_uppercased(self):
        """All net names in output are uppercased for consistency."""
        board = _board_with_2_pads("SIG")
        result = run_autoroute(board)
        for comp in result.get("components", []):
            for pad in comp.get("pads", []):
                if pad.get("net"):
                    assert pad["net"] == pad["net"].upper()
