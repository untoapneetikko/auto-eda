"""Tests for backend.engines.netlist — union-find based net extraction.

Covers: 2-component circuit, label merging, VCC/GND naming, NC handling,
passive propagation, wire-only clusters.
"""
from __future__ import annotations

import pytest
from backend.engines.netlist import build_netlist, ic_layout, _snap, _pt_key


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _resistor(cid, x, y, designator="R1", value="10k", rotation=0):
    return {
        "id": cid, "x": x, "y": y, "rotation": rotation,
        "symType": "resistor", "designator": designator, "value": value,
        "slug": "RESISTOR",
    }

def _capacitor(cid, x, y, designator="C1", value="100nF", rotation=0):
    return {
        "id": cid, "x": x, "y": y, "rotation": rotation,
        "symType": "capacitor", "designator": designator, "value": value,
        "slug": "CAPACITOR",
    }

def _vcc(cid, x, y, value="VCC"):
    return {
        "id": cid, "x": x, "y": y, "rotation": 0,
        "symType": "vcc", "designator": cid, "value": value,
        "slug": "VCC",
    }

def _gnd(cid, x, y):
    return {
        "id": cid, "x": x, "y": y, "rotation": 0,
        "symType": "gnd", "designator": cid, "value": "GND",
        "slug": "GND",
    }

def _wire(wid, points):
    return {"id": wid, "points": [{"x": p[0], "y": p[1]} for p in points]}

def _label(lid, x, y, name):
    return {"id": lid, "x": x, "y": y, "name": name}


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestBuildNetlist:
    def test_two_resistors_connected_by_wire(self):
        """Two resistors connected by a wire share the same net."""
        # R1 at (0,0): P1 at (-30,0), P2 at (30,0)
        # R2 at (120,0): P1 at (90,0), P2 at (150,0)
        # Wire from R1.P2 (30,0) to R2.P1 (90,0)
        comps = [_resistor("r1", 0, 0, "R1"), _resistor("r2", 120, 0, "R2")]
        wires = [_wire("w1", [(30, 0), (90, 0)])]
        result = build_netlist(comps, wires, [])

        nets = result["namedNets"]
        # R1.P2 and R2.P1 should be on the same net
        net_names_for_r1p2 = [n["name"] for n in nets if "R1.P2" in n["pins"]]
        net_names_for_r2p1 = [n["name"] for n in nets if "R2.P1" in n["pins"]]
        assert len(net_names_for_r1p2) == 1
        assert net_names_for_r1p2 == net_names_for_r2p1

    def test_vcc_net_naming(self):
        """VCC symbols name their net 'VCC'."""
        comps = [
            _vcc("vcc1", 0, 0),
            _resistor("r1", 0, 40, "R1"),
        ]
        # Wire from VCC port (0,20) to R1.P1 which is at (0-30, 40) = (-30, 40)
        # Actually VCC port is at (0, 20), R1.P1 at (-30, 40) — not connected
        # Let's connect VCC to R1 via wire
        wires = [_wire("w1", [(0, 20), (0, 40), (-30, 40)])]
        result = build_netlist(comps, wires, [])

        # Find net containing VCC
        vcc_nets = [n for n in result["namedNets"] if n["name"] == "VCC"]
        assert len(vcc_nets) >= 1

    def test_gnd_net_naming(self):
        """GND symbols name their net 'GND'."""
        comps = [
            _gnd("gnd1", 0, 0),
            _resistor("r1", 0, -40, "R1"),
        ]
        wires = [_wire("w1", [(0, -20), (0, -40), (-30, -40)])]
        result = build_netlist(comps, wires, [])

        gnd_nets = [n for n in result["namedNets"] if n["name"] == "GND"]
        assert len(gnd_nets) >= 1

    def test_label_merging(self):
        """Two labels with the same name merge their connected wires."""
        comps = [
            _resistor("r1", 0, 0, "R1"),
            _resistor("r2", 300, 0, "R2"),
        ]
        # R1.P2 at (30,0), R2.P1 at (270,0)
        # Two separate wires, each with a label "SIG"
        wires = [
            _wire("w1", [(30, 0), (60, 0)]),
            _wire("w2", [(240, 0), (270, 0)]),
        ]
        labels = [
            _label("l1", 60, 0, "SIG"),
            _label("l2", 240, 0, "SIG"),
        ]
        result = build_netlist(comps, wires, labels)

        # Both R1.P2 and R2.P1 should be on the same net named "SIG"
        sig_nets = [n for n in result["namedNets"] if n["name"] == "SIG"]
        assert len(sig_nets) == 1
        assert "R1.P2" in sig_nets[0]["pins"]
        assert "R2.P1" in sig_nets[0]["pins"]

    def test_no_connect_handling(self):
        """No-connect markers assign NC net name."""
        comps = [_resistor("r1", 0, 0, "R1")]
        # R1.P2 at (30,0) — mark as NC
        no_connects = [{"x": 30, "y": 0}]
        result = build_netlist(comps, [], [], no_connects)

        # Find net for R1.P2
        p2_nets = [n for n in result["namedNets"] if "R1.P2" in n["pins"]]
        assert len(p2_nets) == 1
        assert p2_nets[0]["name"] == "NC"

    def test_wire_to_net_mapping(self):
        """wireToNet dict maps wire IDs to net names."""
        comps = [_resistor("r1", 0, 0, "R1")]
        wires = [_wire("w1", [(-30, 0), (-60, 0)])]
        result = build_netlist(comps, wires, [])

        assert "w1" in result["wireToNet"]
        assert isinstance(result["wireToNet"]["w1"], str)

    def test_empty_schematic(self):
        """Empty schematic returns empty nets."""
        result = build_netlist([], [], [])
        assert result["namedNets"] == []
        assert result["nets"] == []
        assert result["wireToNet"] == {}

    def test_3v3_vcc_value(self):
        """VCC with value '3V3' names net '3V3' instead of 'VCC'."""
        comps = [
            _vcc("vcc1", 0, 0, value="3V3"),
            _resistor("r1", 0, 40, "R1"),
        ]
        wires = [_wire("w1", [(0, 20), (-30, 40)])]
        result = build_netlist(comps, wires, [])

        net_names = {n["name"] for n in result["namedNets"]}
        assert "3V3" in net_names


class TestIcLayout:
    def test_basic_2pin(self):
        """2-pin IC produces left and right ports."""
        pins = [{"name": "A", "number": 1}, {"name": "B", "number": 2}]
        layout = ic_layout(pins)
        assert len(layout["ports"]) == 2
        assert layout["ports"][0]["name"] == "A"
        assert layout["ports"][1]["name"] == "B"

    def test_4pin(self):
        """4-pin IC splits pins: 2 left, 2 right."""
        pins = [
            {"name": "A", "number": 1}, {"name": "B", "number": 2},
            {"name": "C", "number": 3}, {"name": "D", "number": 4},
        ]
        layout = ic_layout(pins)
        assert len(layout["ports"]) == 4
        assert len(layout["leftPins"]) == 2
        assert len(layout["rightPins"]) == 2

    def test_empty_pins(self):
        """Empty pin list doesn't crash."""
        layout = ic_layout([])
        assert "ports" in layout
        assert layout["BOX_W"] == 120

    def test_box_height_scales_with_pins(self):
        """More pins = taller box."""
        layout_4 = ic_layout([{"name": str(i), "number": i} for i in range(1, 5)])
        layout_8 = ic_layout([{"name": str(i), "number": i} for i in range(1, 9)])
        assert layout_8["BOX_H"] > layout_4["BOX_H"]


class TestHelpers:
    def test_snap_rounds_to_grid(self):
        assert _snap(11) == 12
        assert _snap(12) == 12
        assert _snap(13) == 12
        assert _snap(18) == 24

    def test_pt_key_format(self):
        key = _pt_key(12, 24)
        assert key == "12,24"
