"""backend.engines — extracted core algorithms from schematic_api.py.

Each module is a pure function: dicts in, dicts out. No Redis, no endpoints.
"""
from .netlist import build_netlist, ic_layout, _SYMDEFS, _rotate_offset, _snap, _pt_key
from .drc import run_drc
from .autoroute import run_autoroute, _autoroute_skip_net, _autoroute_trace_width
from .autoplace import run_autoplace, _load_schematic_hints, _positions_are_spread
from .importer import import_schematic, _pick_footprint, _match_package_to_footprint

__all__ = [
    "build_netlist", "ic_layout",
    "run_drc",
    "run_autoroute", "_autoroute_skip_net", "_autoroute_trace_width",
    "run_autoplace", "_load_schematic_hints", "_positions_are_spread",
    "import_schematic", "_pick_footprint", "_match_package_to_footprint",
]
