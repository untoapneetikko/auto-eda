"""Tests for backend.engines.importer — schematic-to-PCB import.

Covers: package-to-footprint matching, _pick_footprint logic, import_schematic basics.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest
from backend.engines.importer import (
    _match_package_to_footprint,
    _pick_footprint,
    import_schematic,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _setup_footprints_dir(tmpdir: Path, stems: list[str]) -> Path:
    """Create a footprints directory with empty JSON files for given stems."""
    fp_dir = tmpdir / "footprints"
    fp_dir.mkdir()
    for stem in stems:
        (fp_dir / f"{stem}.json").write_text(json.dumps({
            "name": stem,
            "pads": [
                {"number": "1", "x": -1, "y": 0, "type": "smd", "shape": "rect",
                 "size_x": 0.6, "size_y": 0.6},
                {"number": "2", "x": 1, "y": 0, "type": "smd", "shape": "rect",
                 "size_x": 0.6, "size_y": 0.6},
            ],
        }))
    return fp_dir


def _setup_library_dir(tmpdir: Path) -> Path:
    """Create a minimal library directory."""
    lib_dir = tmpdir / "library"
    lib_dir.mkdir()
    return lib_dir


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestMatchPackageToFootprint:
    def test_exact_match(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            fp_dir = _setup_footprints_dir(Path(tmpdir), ["SOT-23", "DIP-8", "0402"])
            result = _match_package_to_footprint(["SOT-23"], fp_dir)
            assert result == "SOT-23"

    def test_case_insensitive(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            fp_dir = _setup_footprints_dir(Path(tmpdir), ["SOT-23"])
            result = _match_package_to_footprint(["sot-23"], fp_dir)
            assert result == "SOT-23"

    def test_strip_parenthetical(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            fp_dir = _setup_footprints_dir(Path(tmpdir), ["D2PAK"])
            result = _match_package_to_footprint(["D2PAK (SMD)"], fp_dir)
            assert result == "D2PAK"

    def test_first_token(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            fp_dir = _setup_footprints_dir(Path(tmpdir), ["0402"])
            result = _match_package_to_footprint(["0402 SMD"], fp_dir)
            assert result == "0402"

    def test_no_match(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            fp_dir = _setup_footprints_dir(Path(tmpdir), ["SOT-23"])
            result = _match_package_to_footprint(["UNKNOWN-PACKAGE"], fp_dir)
            assert result is None

    def test_dip_semantic(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            fp_dir = _setup_footprints_dir(Path(tmpdir), ["DIP-28"])
            result = _match_package_to_footprint(["28-pin DIP (PDIP28)"], fp_dir)
            assert result == "DIP-28"

    def test_so_to_soic(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            fp_dir = _setup_footprints_dir(Path(tmpdir), ["SOIC-8"])
            result = _match_package_to_footprint(["SO-8"], fp_dir)
            assert result == "SOIC-8"


class TestPickFootprint:
    def test_power_symbol_returns_none(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            fp_dir = _setup_footprints_dir(Path(tmpdir), ["0402"])
            result = _pick_footprint("GND", "gnd", 1, footprints_dir=fp_dir)
            assert result is None

    def test_resistor_gets_0402(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            fp_dir = _setup_footprints_dir(Path(tmpdir), ["0402"])
            result = _pick_footprint("RESISTOR", "resistor", 2, footprints_dir=fp_dir)
            assert result is not None
            assert result["name"] == "0402"

    def test_explicit_profile_footprint(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            fp_dir = _setup_footprints_dir(Path(tmpdir), ["SOT-223", "0402"])
            result = _pick_footprint(
                "AMS1117", "ic", 3,
                profile_footprint="SOT-223",
                footprints_dir=fp_dir,
            )
            assert result is not None
            assert result["name"] == "SOT-223"

    def test_pin_count_fallback(self):
        """Unknown IC with 8 pins falls back to DIP-8."""
        with tempfile.TemporaryDirectory() as tmpdir:
            fp_dir = _setup_footprints_dir(Path(tmpdir), ["DIP-8"])
            result = _pick_footprint("UNKNOWN_IC", "ic", 8, footprints_dir=fp_dir)
            assert result is not None
            assert result["name"] == "DIP-8"


class TestImportSchematic:
    def test_basic_import(self):
        """Import a minimal schematic with one resistor."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            fp_dir = _setup_footprints_dir(tmpdir, ["0402"])
            lib_dir = _setup_library_dir(tmpdir)

            project = {
                "name": "Test",
                "id": "test-1",
                "components": [
                    {
                        "id": "r1", "designator": "R1", "ref": "R1",
                        "slug": "RESISTOR", "symType": "resistor",
                        "value": "10k", "x": 100, "y": 200,
                    },
                ],
            }
            netlist = {"VCC": ["R1.P1"], "GND": ["R1.P2"]}

            result = import_schematic(
                project, netlist, 100, 80,
                library_dir=lib_dir,
                footprints_dir=fp_dir,
            )

            assert "components" in result
            assert len(result["components"]) == 1
            assert result["components"][0]["ref"] == "R1"
            assert result["components"][0]["footprint"] == "0402"
            assert result["board"]["width"] == 100
            assert result["board"]["height"] == 80

    def test_power_symbols_excluded(self):
        """VCC and GND symbols don't appear in PCB components."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            fp_dir = _setup_footprints_dir(tmpdir, ["0402"])
            lib_dir = _setup_library_dir(tmpdir)

            project = {
                "name": "Test",
                "components": [
                    {"id": "r1", "designator": "R1", "ref": "R1",
                     "slug": "RESISTOR", "symType": "resistor",
                     "value": "10k", "x": 100, "y": 200},
                    {"id": "vcc1", "designator": "VCC1",
                     "slug": "VCC", "symType": "vcc",
                     "value": "VCC", "x": 100, "y": 100},
                    {"id": "gnd1", "designator": "GND1",
                     "slug": "GND", "symType": "gnd",
                     "value": "GND", "x": 100, "y": 300},
                ],
            }

            result = import_schematic(
                project, {}, 100, 80,
                library_dir=lib_dir,
                footprints_dir=fp_dir,
            )

            refs = [c["ref"] for c in result["components"]]
            assert "R1" in refs
            assert "VCC1" not in refs
            assert "GND1" not in refs

    def test_nets_built_from_netlist(self):
        """Net list is built from the provided netlist dict."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            fp_dir = _setup_footprints_dir(tmpdir, ["0402"])
            lib_dir = _setup_library_dir(tmpdir)

            project = {
                "name": "Test",
                "components": [
                    {"id": "r1", "designator": "R1", "ref": "R1",
                     "slug": "RESISTOR", "symType": "resistor",
                     "value": "10k", "x": 100, "y": 200},
                ],
            }
            netlist = {"SIG": ["R1.P1", "R1.P2"]}

            result = import_schematic(
                project, netlist, 100, 80,
                library_dir=lib_dir,
                footprints_dir=fp_dir,
            )

            net_names = [n["name"] for n in result["nets"]]
            assert "SIG" in net_names

    def test_board_title_includes_version(self):
        """Board title includes project name and version."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            fp_dir = _setup_footprints_dir(tmpdir, ["0402"])
            lib_dir = _setup_library_dir(tmpdir)

            project = {"name": "MyProject", "components": []}

            result = import_schematic(
                project, {}, 100, 80,
                library_dir=lib_dir,
                footprints_dir=fp_dir,
            )

            assert "MyProject" in result["title"]
            assert "V1" in result["title"]
