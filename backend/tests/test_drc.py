"""Tests for backend.engines.drc — Design Rule Check.

Covers: clean board (0 violations), trace width violations, out-of-bounds,
clearance violations, empty board, unconnected nets, duplicate refs.
"""
from __future__ import annotations

import pytest
from backend.engines.drc import run_drc


# ── Helpers ───────────────────────────────────────────────────────────────────

def _minimal_board(**overrides):
    """Return a minimal valid board dict."""
    board = {
        "board": {"width": 100, "height": 80},
        "designRules": {
            "clearance": 0.2,
            "minTraceWidth": 0.15,
            "edgeClearance": 0.5,
            "viaSize": 1.0,
            "viaDrill": 0.6,
            "minAnnularRing": 0.15,
        },
        "components": [],
        "traces": [],
        "vias": [],
        "nets": [],
        "zones": [],
        "areas": [],
    }
    board.update(overrides)
    return board


def _trace(net, x1, y1, x2, y2, width=0.25, layer="F.Cu"):
    return {
        "net": net, "layer": layer, "width": width,
        "segments": [{"start": {"x": x1, "y": y1}, "end": {"x": x2, "y": y2}}],
    }


def _comp(ref, x, y, pads=None, rotation=0, layer="F"):
    return {
        "id": ref, "ref": ref, "x": x, "y": y, "rotation": rotation,
        "layer": layer, "pads": pads or [],
    }


def _pad(number, x, y, net="", size_x=1.0, size_y=1.0, pad_type="smd"):
    return {
        "number": number, "name": str(number),
        "x": x, "y": y, "net": net,
        "size_x": size_x, "size_y": size_y, "type": pad_type,
    }


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestCleanBoard:
    def test_empty_board_passes(self):
        """Empty board with no traces/components has zero violations."""
        result = run_drc(_minimal_board())
        assert result["passed"] is True
        assert len(result["violations"]) == 0

    def test_valid_trace_passes(self):
        """A properly-sized trace inside board boundaries passes."""
        board = _minimal_board(traces=[
            _trace("VCC", 10, 10, 50, 10, width=0.25),
        ])
        result = run_drc(board)
        # No trace width, out-of-bounds, or edge clearance errors
        width_v = [v for v in result["violations"] if v["type"] == "TRACE_WIDTH"]
        oob_v = [v for v in result["violations"] if v["type"] == "OUT_OF_BOUNDS"]
        assert len(width_v) == 0
        assert len(oob_v) == 0


class TestTraceWidth:
    def test_thin_trace_flagged(self):
        """Trace thinner than minTraceWidth triggers TRACE_WIDTH violation."""
        board = _minimal_board(traces=[
            _trace("SIG", 10, 10, 50, 10, width=0.10),  # 0.10 < 0.15 min
        ])
        result = run_drc(board)
        tw_violations = [v for v in result["violations"] if v["type"] == "TRACE_WIDTH"]
        assert len(tw_violations) >= 1
        assert "0.100" in tw_violations[0]["msg"]


class TestOutOfBounds:
    def test_trace_outside_board(self):
        """Trace endpoint outside board triggers OUT_OF_BOUNDS."""
        board = _minimal_board(traces=[
            _trace("SIG", -5, 10, 20, 10),  # x=-5 is outside
        ])
        result = run_drc(board)
        oob = [v for v in result["violations"] if v["type"] == "OUT_OF_BOUNDS"]
        assert len(oob) >= 1

    def test_via_outside_board(self):
        """Via outside board triggers OUT_OF_BOUNDS."""
        board = _minimal_board(vias=[
            {"x": -2, "y": 10, "size": 1.0, "drill": 0.6, "net": "SIG"},
        ])
        result = run_drc(board)
        oob = [v for v in result["violations"] if v["type"] == "OUT_OF_BOUNDS"]
        assert len(oob) >= 1


class TestClearance:
    def test_different_net_traces_too_close(self):
        """Two traces of different nets closer than clearance trigger CLEARANCE."""
        board = _minimal_board(traces=[
            _trace("NET1", 10, 10, 50, 10, width=0.25),
            _trace("NET2", 10, 10.2, 50, 10.2, width=0.25),  # 0.2mm apart, need 0.2 + widths
        ])
        result = run_drc(board)
        cl = [v for v in result["violations"] if v["type"] == "CLEARANCE"]
        assert len(cl) >= 1

    def test_same_net_traces_no_violation(self):
        """Two traces of the same net don't trigger clearance violations."""
        board = _minimal_board(traces=[
            _trace("VCC", 10, 10, 50, 10, width=0.25),
            _trace("VCC", 10, 10.3, 50, 10.3, width=0.25),
        ])
        result = run_drc(board)
        cl = [v for v in result["violations"] if v["type"] == "CLEARANCE"]
        assert len(cl) == 0


class TestDuplicateRefs:
    def test_duplicate_ref_flagged(self):
        """Two components with the same ref trigger DUPLICATE_REF."""
        board = _minimal_board(components=[
            _comp("R1", 20, 20),
            _comp("R1", 40, 40),
        ])
        result = run_drc(board)
        dup = [v for v in result["violations"] if v["type"] == "DUPLICATE_REF"]
        assert len(dup) >= 1


class TestViaAnnularRing:
    def test_small_annular_ring_flagged(self):
        """Via with annular ring < min triggers ANNULAR_RING."""
        board = _minimal_board(vias=[
            {"x": 50, "y": 40, "size": 0.5, "drill": 0.4, "net": "SIG"},
            # ring = (0.5 - 0.4) / 2 = 0.05 < 0.15 min
        ])
        result = run_drc(board)
        ar = [v for v in result["violations"] if v["type"] == "ANNULAR_RING"]
        assert len(ar) >= 1


class TestMissingNets:
    def test_unconnected_net_detected(self):
        """Two pads on the same net with no trace between them trigger UNCONNECTED."""
        board = _minimal_board(
            components=[
                _comp("R1", 20, 20, pads=[_pad(1, -2, 0, "SIG"), _pad(2, 2, 0, "SIG")]),
                _comp("R2", 60, 20, pads=[_pad(1, -2, 0, "SIG"), _pad(2, 2, 0, "SIG")]),
            ],
            nets=[{"name": "SIG", "pads": ["R1.1", "R1.2", "R2.1", "R2.2"]}],
        )
        result = run_drc(board)
        uc = [v for v in result["violations"] if v["type"] == "UNCONNECTED"]
        assert len(uc) >= 1
