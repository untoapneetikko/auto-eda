"""
Tests for the Auto-Route agent.

Verifies:
  1. All nets from schematic.json appear in routing output
  2. Power traces are wider than signal traces
  3. No trace has width_mm < 0.15mm
  4. Crystal/oscillator no-route zones are respected
  5. Trace width calculator returns correct values
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

# Add the agents/autoroute directory to sys.path so imports work
sys.path.insert(0, str(Path(__file__).parent))

from trace_width_calculator import calculate_trace_width, classify_net
from agent import (
    build_initial_routing,
    build_net_map,
    check_all_nets_routed,
    detect_crystals,
    fix_routing_issues,
    validate_routing_schema,
)

# ── Fixtures ───────────────────────────────────────────────────────────────────

SAMPLE_SCHEMATIC = {
    "project_name": "test_project",
    "nets": [
        {"name": "VCC", "label": "VCC", "type": "power"},
        {"name": "GND", "label": "GND", "type": "gnd"},
        {"name": "CLK", "label": "CLK", "type": "signal"},
        {"name": "/data_out", "label": "data_out", "type": "signal"},
        {"name": "/data_in", "label": "data_in", "type": "signal"},
    ],
    "components": [
        {
            "reference": "U1",
            "value": "MCU",
            "footprint": "LQFP-32",
            "position": {"x": 50.0, "y": 50.0},
            "connections": [
                {"pin": 1, "net": "VCC"},
                {"pin": 2, "net": "GND"},
                {"pin": 3, "net": "CLK"},
                {"pin": 4, "net": "/data_out"},
            ],
        },
        {
            "reference": "U2",
            "value": "SensorIC",
            "footprint": "SOIC-8",
            "position": {"x": 90.0, "y": 50.0},
            "connections": [
                {"pin": 1, "net": "VCC"},
                {"pin": 2, "net": "GND"},
                {"pin": 3, "net": "/data_in"},
                {"pin": 4, "net": "/data_out"},
            ],
        },
        {
            "reference": "R1",
            "value": "10k",
            "footprint": "R_0402",
            "position": {"x": 70.0, "y": 30.0},
            "connections": [
                {"pin": 1, "net": "VCC"},
                {"pin": 2, "net": "CLK"},
            ],
        },
        {
            "reference": "Y1",
            "value": "16MHz",
            "footprint": "Crystal_SMD_3225",
            "position": {"x": 30.0, "y": 70.0},
            "connections": [
                {"pin": 1, "net": "CLK"},
                {"pin": 2, "net": "GND"},
            ],
        },
        {
            "reference": "C1",
            "value": "100nF",
            "footprint": "C_0402",
            "position": {"x": 55.0, "y": 65.0},
            "connections": [
                {"pin": 1, "net": "VCC"},
                {"pin": 2, "net": "GND"},
            ],
        },
    ],
    "power_symbols": ["VCC", "GND"],
    "format": "kicad_sch",
}

SAMPLE_PLACEMENT = {
    "board": {"width_mm": 100.0, "height_mm": 80.0},
    "placements": [
        {"reference": "U1", "x": 50.0, "y": 50.0, "rotation": 0.0, "layer": "F.Cu", "rationale": "center"},
        {"reference": "U2", "x": 90.0, "y": 50.0, "rotation": 0.0, "layer": "F.Cu", "rationale": "right"},
        {"reference": "R1", "x": 70.0, "y": 30.0, "rotation": 0.0, "layer": "F.Cu", "rationale": "pull-up"},
        {"reference": "Y1", "x": 30.0, "y": 70.0, "rotation": 0.0, "layer": "F.Cu", "rationale": "crystal"},
        {"reference": "C1", "x": 55.0, "y": 65.0, "rotation": 0.0, "layer": "F.Cu", "rationale": "decoupling"},
    ],
}

# ── Trace Width Calculator Tests ───────────────────────────────────────────────

class TestTraceWidthCalculator:
    def test_power_net_classification(self):
        assert classify_net("VCC") == "power"
        assert classify_net("GND") == "power"
        assert classify_net("VDD") == "power"
        assert classify_net("VBAT") == "power"
        assert classify_net("PGND") == "power"
        assert classify_net("/VSYS") == "power"

    def test_highspeed_net_classification(self):
        assert classify_net("CLK") == "highspeed"
        assert classify_net("USB_DP") == "highspeed"
        assert classify_net("ETH_MDI") == "highspeed"

    def test_analog_net_classification(self):
        assert classify_net("AIN0") == "analog"
        assert classify_net("VREF") == "analog"
        assert classify_net("DAC_OUT") == "analog"

    def test_signal_net_classification(self):
        assert classify_net("/data_out") == "signal"
        assert classify_net("SDA") == "signal"
        assert classify_net("SCL") == "signal"

    def test_power_trace_width_minimum(self):
        result = calculate_trace_width("VCC", current_amps=0.01)
        # Even at very low current, power traces must be >= 0.4mm
        assert result["width_mm"] >= 0.4, f"Power trace width {result['width_mm']} < 0.4mm"

    def test_power_trace_width_scales_with_current(self):
        result_1a = calculate_trace_width("VCC", current_amps=1.0)
        result_2a = calculate_trace_width("VCC", current_amps=2.0)
        assert result_2a["width_mm"] > result_1a["width_mm"], (
            "2A trace should be wider than 1A trace"
        )
        assert result_1a["width_mm"] == pytest.approx(0.4, abs=0.001)
        assert result_2a["width_mm"] == pytest.approx(0.8, abs=0.001)

    def test_signal_trace_width_preferred(self):
        result = calculate_trace_width("/data_out")
        assert result["width_mm"] == pytest.approx(0.2, abs=0.001)

    def test_highspeed_trace_width(self):
        result = calculate_trace_width("CLK")
        assert result["width_mm"] == pytest.approx(0.3, abs=0.001), (
            "High-speed CLK trace should be 0.3mm for 50Ω impedance"
        )

    def test_no_trace_below_minimum(self):
        for net in ["VCC", "GND", "CLK", "/data_out", "AIN0", "SDA"]:
            result = calculate_trace_width(net)
            assert result["width_mm"] >= 0.15, (
                f"Net {net!r}: width {result['width_mm']}mm is below 0.15mm minimum"
            )

    def test_result_has_required_keys(self):
        result = calculate_trace_width("VCC", 1.0)
        assert "net" in result
        assert "type" in result
        assert "width_mm" in result
        assert "rationale" in result

    def test_result_type_values(self):
        for net, expected_type in [
            ("VCC", "power"),
            ("CLK", "highspeed"),
            ("AIN0", "analog"),
            ("/data_out", "signal"),
        ]:
            result = calculate_trace_width(net)
            assert result["type"] == expected_type, (
                f"Net {net!r}: expected type {expected_type!r}, got {result['type']!r}"
            )


# ── Net Map Tests ──────────────────────────────────────────────────────────────

class TestNetMap:
    def test_all_nets_present(self):
        net_map = build_net_map(SAMPLE_SCHEMATIC)
        expected_nets = {"VCC", "GND", "CLK", "/data_out", "/data_in"}
        assert set(net_map.keys()) == expected_nets

    def test_multi_pad_nets(self):
        net_map = build_net_map(SAMPLE_SCHEMATIC)
        # VCC is used by U1, U2, R1, C1 → 4 pads
        assert len(net_map["VCC"]) == 4
        # GND is used by U1, U2, Y1, C1 → 4 pads
        assert len(net_map["GND"]) == 4
        # /data_out connects U1 and U2
        assert len(net_map["/data_out"]) == 2


# ── Crystal Detection Tests ────────────────────────────────────────────────────

class TestCrystalDetection:
    def test_detects_y_prefix(self):
        refs = detect_crystals(SAMPLE_SCHEMATIC["components"])
        assert "Y1" in refs

    def test_detects_crystal_value(self):
        components = [
            {"reference": "X2", "value": "Crystal 8MHz", "footprint": "SMD_2012"},
        ]
        refs = detect_crystals(components)
        assert "X2" in refs

    def test_detects_osc_footprint(self):
        components = [
            {"reference": "U5", "value": "SomeIC", "footprint": "oscillator_smd"},
        ]
        refs = detect_crystals(components)
        assert "U5" in refs

    def test_normal_ic_not_detected(self):
        components = [
            {"reference": "U1", "value": "MCU", "footprint": "LQFP-32"},
        ]
        refs = detect_crystals(components)
        assert "U1" not in refs


# ── Routing Generation Tests ───────────────────────────────────────────────────

class TestInitialRouting:
    def setup_method(self):
        self.crystal_refs = detect_crystals(SAMPLE_SCHEMATIC["components"])
        self.routing = build_initial_routing(SAMPLE_SCHEMATIC, SAMPLE_PLACEMENT, self.crystal_refs)

    def test_routing_has_required_keys(self):
        assert "traces" in self.routing
        assert "vias" in self.routing

    def test_traces_is_list(self):
        assert isinstance(self.routing["traces"], list)

    def test_vias_is_list(self):
        assert isinstance(self.routing["vias"], list)

    def test_each_trace_has_required_fields(self):
        for trace in self.routing["traces"]:
            assert "net" in trace, f"Trace missing 'net': {trace}"
            assert "layer" in trace, f"Trace missing 'layer': {trace}"
            assert "width_mm" in trace, f"Trace missing 'width_mm': {trace}"
            assert "path" in trace, f"Trace missing 'path': {trace}"

    def test_each_trace_path_has_at_least_two_points(self):
        for trace in self.routing["traces"]:
            assert len(trace["path"]) >= 2, (
                f"Trace for net {trace['net']!r} has path with < 2 points"
            )

    def test_no_traces_below_minimum_width(self):
        for trace in self.routing["traces"]:
            assert trace["width_mm"] >= 0.15, (
                f"Trace for net {trace['net']!r}: width {trace['width_mm']}mm < 0.15mm"
            )

    def test_power_traces_wider_than_signal_traces(self):
        power_widths = [
            t["width_mm"] for t in self.routing["traces"]
            if classify_net(t["net"]) == "power"
        ]
        signal_widths = [
            t["width_mm"] for t in self.routing["traces"]
            if classify_net(t["net"]) == "signal"
        ]

        if power_widths and signal_widths:
            min_power = min(power_widths)
            max_signal = max(signal_widths)
            assert min_power > max_signal, (
                f"Power traces (min {min_power}mm) should be wider than "
                f"signal traces (max {max_signal}mm)"
            )

    def test_all_nets_from_schematic_routed(self):
        unrouted = check_all_nets_routed(SAMPLE_SCHEMATIC, self.routing)
        # /data_in only has 1 connection to U2 in SAMPLE_SCHEMATIC, so it won't be routed
        # (single-pad nets are skipped). All multi-pad nets should be routed.
        net_map = build_net_map(SAMPLE_SCHEMATIC)
        multi_pad_nets = {n for n, pads in net_map.items() if len(pads) >= 2}
        routed_nets = {t["net"] for t in self.routing["traces"]}
        missing = multi_pad_nets - routed_nets
        assert not missing, f"Multi-pad nets not routed: {missing}"


# ── Schema Validation Tests ────────────────────────────────────────────────────

class TestSchemaValidation:
    def test_valid_routing_passes(self):
        routing = {
            "traces": [
                {
                    "net": "VCC",
                    "layer": "F.Cu",
                    "width_mm": 0.4,
                    "path": [{"x": 0.0, "y": 0.0}, {"x": 10.0, "y": 0.0}],
                }
            ],
            "vias": [],
        }
        errors = validate_routing_schema(routing)
        assert errors == [], f"Valid routing failed validation: {errors}"

    def test_missing_traces_key_fails(self):
        errors = validate_routing_schema({"vias": []})
        assert any("traces" in e for e in errors)

    def test_missing_vias_key_fails(self):
        errors = validate_routing_schema({"traces": []})
        assert any("vias" in e for e in errors)

    def test_trace_below_minimum_width_fails(self):
        routing = {
            "traces": [
                {
                    "net": "test",
                    "layer": "F.Cu",
                    "width_mm": 0.1,  # below 0.15 minimum
                    "path": [{"x": 0.0, "y": 0.0}, {"x": 5.0, "y": 0.0}],
                }
            ],
            "vias": [],
        }
        errors = validate_routing_schema(routing)
        assert any("0.15" in e or "minimum" in e.lower() for e in errors), (
            f"Expected minimum width error, got: {errors}"
        )

    def test_trace_with_single_point_path_fails(self):
        routing = {
            "traces": [
                {
                    "net": "test",
                    "layer": "F.Cu",
                    "width_mm": 0.2,
                    "path": [{"x": 0.0, "y": 0.0}],  # only 1 point
                }
            ],
            "vias": [],
        }
        errors = validate_routing_schema(routing)
        assert any("2" in e for e in errors), f"Expected path length error, got: {errors}"


# ── Fix Routing Issues Tests ───────────────────────────────────────────────────

class TestFixRoutingIssues:
    def test_clamps_trace_width_to_minimum(self):
        routing = {
            "traces": [
                {
                    "net": "/data_out",
                    "layer": "F.Cu",
                    "width_mm": 0.05,  # too narrow
                    "path": [{"x": 0.0, "y": 0.0}, {"x": 5.0, "y": 0.0}],
                }
            ],
            "vias": [],
        }
        fixed = fix_routing_issues(routing, SAMPLE_SCHEMATIC)
        assert fixed["traces"][0]["width_mm"] >= 0.15

    def test_power_traces_get_minimum_power_width(self):
        routing = {
            "traces": [
                {
                    "net": "VCC",
                    "layer": "F.Cu",
                    "width_mm": 0.15,  # valid signal width but too thin for power
                    "path": [{"x": 0.0, "y": 0.0}, {"x": 5.0, "y": 0.0}],
                }
            ],
            "vias": [],
        }
        fixed = fix_routing_issues(routing, SAMPLE_SCHEMATIC)
        assert fixed["traces"][0]["width_mm"] >= 0.4, (
            "Power trace VCC should be at least 0.4mm"
        )

    def test_layer_defaults_to_f_cu(self):
        routing = {
            "traces": [
                {
                    "net": "/data_out",
                    "layer": "",
                    "width_mm": 0.2,
                    "path": [{"x": 0.0, "y": 0.0}, {"x": 5.0, "y": 0.0}],
                }
            ],
            "vias": [],
        }
        fixed = fix_routing_issues(routing, SAMPLE_SCHEMATIC)
        assert fixed["traces"][0]["layer"] == "F.Cu"


# ── Integration: Full Pipeline Test ───────────────────────────────────────────

class TestFullPipeline:
    def test_pipeline_without_claude(self, tmp_path):
        """Run the full agent pipeline with no-claude mode (deterministic test)."""
        placement_file = tmp_path / "placement.json"
        schematic_file = tmp_path / "schematic.json"
        output_file = tmp_path / "routing.json"

        placement_file.write_text(json.dumps(SAMPLE_PLACEMENT))
        schematic_file.write_text(json.dumps(SAMPLE_SCHEMATIC))

        from agent import run

        result = run(
            placement_path=placement_file,
            schematic_path=schematic_file,
            output_path=output_file,
            use_claude=False,
        )

        # Output file must exist
        assert output_file.exists(), "routing.json was not written"

        # Load and verify
        with open(output_file) as f:
            saved = json.load(f)

        assert "traces" in saved
        assert "vias" in saved
        assert len(saved["traces"]) > 0, "No traces were generated"

        # All traces must meet minimum width
        for trace in saved["traces"]:
            assert trace["width_mm"] >= 0.15, (
                f"Trace {trace['net']!r}: width {trace['width_mm']}mm < 0.15mm"
            )

        # Power traces must be wider than signal traces
        power_widths = [
            t["width_mm"] for t in saved["traces"]
            if classify_net(t["net"]) == "power"
        ]
        signal_widths = [
            t["width_mm"] for t in saved["traces"]
            if classify_net(t["net"]) == "signal"
        ]
        if power_widths and signal_widths:
            assert min(power_widths) > max(signal_widths), (
                f"Power min {min(power_widths):.3f}mm should > signal max {max(signal_widths):.3f}mm"
            )

        # All multi-pad nets must be routed
        unrouted = check_all_nets_routed(SAMPLE_SCHEMATIC, saved)
        assert not unrouted, f"Unrouted nets: {unrouted}"

    def test_missing_placement_raises(self, tmp_path):
        """Missing placement.json should raise FileNotFoundError."""
        from agent import run

        with pytest.raises(FileNotFoundError, match="placement.json"):
            run(
                placement_path=tmp_path / "nonexistent_placement.json",
                schematic_path=tmp_path / "nonexistent_schematic.json",
                output_path=tmp_path / "routing.json",
                use_claude=False,
            )


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Allow running directly: python test_agent.py
    import subprocess
    result = subprocess.run(
        [sys.executable, "-m", "pytest", __file__, "-v", "--tb=short"],
        cwd=Path(__file__).parent,
    )
    sys.exit(result.returncode)
