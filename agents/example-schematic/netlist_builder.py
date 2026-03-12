"""
Netlist Builder

Takes a schematic JSON (as produced by agent.py) and produces a
human-readable netlist summary for logging and debugging.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any


def build_netlist_summary(schematic: dict[str, Any]) -> str:
    """
    Build a human-readable netlist summary string from a schematic dict.

    The summary includes:
    - Project name
    - Component list with values and footprints
    - Net connectivity table (net name -> list of "REF.pin" strings)

    Returns a formatted string suitable for logging or debug output.
    """
    lines: list[str] = []
    project = schematic.get("project_name", "Unnamed Project")
    fmt = schematic.get("format", "kicad_sch")

    lines.append("=" * 60)
    lines.append(f"NETLIST SUMMARY — {project}")
    lines.append(f"Format: {fmt}")
    lines.append("=" * 60)

    # -----------------------------------------------------------------------
    # Component list
    # -----------------------------------------------------------------------
    components = schematic.get("components", [])
    lines.append(f"\nCOMPONENTS ({len(components)} total):")
    lines.append("-" * 40)

    for comp in components:
        ref = comp.get("reference", "?")
        value = comp.get("value", "?")
        footprint = comp.get("footprint", "?")
        assumed = comp.get("assumed", False)
        pos = comp.get("position", {})
        x = pos.get("x", 0.0)
        y = pos.get("y", 0.0)
        assumed_tag = "  [ASSUMED]" if assumed else ""
        lines.append(f"  {ref:<6}  {value:<20}  {footprint}  @ ({x:.0f}, {y:.0f}){assumed_tag}")

    # -----------------------------------------------------------------------
    # Net connectivity
    # Build a map: net_name -> list of "REF.pin"
    # -----------------------------------------------------------------------
    net_connections: dict[str, list[str]] = defaultdict(list)

    for comp in components:
        ref = comp.get("reference", "?")
        for conn in comp.get("connections", []):
            pin = conn.get("pin", "?")
            net = conn.get("net", "UNNAMED")
            net_connections[net].append(f"{ref}.{pin}")

    # -----------------------------------------------------------------------
    # Power symbols contribute to net names (listed in power_symbols)
    # -----------------------------------------------------------------------
    power_symbols = schematic.get("power_symbols", [])

    # Declared nets from the nets array
    declared_nets = {n["name"]: n for n in schematic.get("nets", [])}

    lines.append(f"\nNETS ({len(net_connections)} connected, {len(declared_nets)} declared):")
    lines.append("-" * 40)

    # Sort: power first, then gnd, then signals alphabetically
    def _net_sort_key(name: str) -> tuple[int, str]:
        net_info = declared_nets.get(name, {})
        ntype = net_info.get("type", "signal")
        order = {"power": 0, "gnd": 1, "signal": 2}.get(ntype, 2)
        return (order, name)

    all_net_names = sorted(set(list(net_connections.keys()) + list(declared_nets.keys())), key=_net_sort_key)

    for net_name in all_net_names:
        net_info = declared_nets.get(net_name, {})
        ntype = net_info.get("type", "signal")
        label = net_info.get("label", net_name)
        pins = net_connections.get(net_name, [])
        pin_str = ", ".join(sorted(pins)) if pins else "(no connections)"
        type_tag = f"[{ntype.upper()}]"
        lines.append(f"  {label:<20}  {type_tag:<10}  {pin_str}")

    # -----------------------------------------------------------------------
    # Power rail summary
    # -----------------------------------------------------------------------
    if power_symbols:
        lines.append(f"\nPOWER SYMBOLS: {', '.join(power_symbols)}")

    # -----------------------------------------------------------------------
    # Simple connectivity check: warn about nets with only one connection
    # -----------------------------------------------------------------------
    dangling = [
        net for net, pins in net_connections.items()
        if len(pins) == 1 and net not in power_symbols
    ]
    if dangling:
        lines.append("\nWARNINGS — dangling nets (only one connection):")
        for net in sorted(dangling):
            lines.append(f"  ! {net} -> {net_connections[net][0]}")

    # -----------------------------------------------------------------------
    # Unconnected declared nets
    # -----------------------------------------------------------------------
    unconnected = [n for n in declared_nets if n not in net_connections]
    if unconnected:
        lines.append("\nWARNINGS — declared nets with no component connections:")
        for net in sorted(unconnected):
            lines.append(f"  ! {net}")

    lines.append("\n" + "=" * 60)
    return "\n".join(lines)


def build_netlist_from_file(schematic_path: str | Path) -> str:
    """
    Convenience wrapper: load schematic JSON from a file path and return
    the human-readable netlist summary string.
    """
    with open(schematic_path) as f:
        schematic = json.load(f)
    return build_netlist_summary(schematic)


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python netlist_builder.py <example_schematic.json>", file=sys.stderr)
        sys.exit(1)

    summary = build_netlist_from_file(sys.argv[1])
    print(summary)
