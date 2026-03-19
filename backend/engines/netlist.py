"""Netlist extraction engine — union-find based net assignment from schematic JSON.

Pure algorithm: takes dicts + a library_dir Path, returns dicts. No Redis, no endpoints.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

# ── IC layout helper ──────────────────────────────────────────────────────────

def ic_layout(pins: list) -> dict:
    """Compute IC symbol geometry from a list of pin dicts (port of JS _icLayout)."""
    PIN_STUB = 40
    ROW_H    = 20
    PAD_Y    = 16
    BOX_W    = 120

    sorted_pins = sorted(pins, key=lambda p: (
        int(p.get("number", 0)) if str(p.get("number", "")).isdigit() else 0
    ))

    half = math.ceil(len(sorted_pins) / 2) if sorted_pins else 2
    left_pins  = sorted_pins[:half]
    right_pins = list(reversed(sorted_pins[half:]))

    max_rows = max(len(left_pins), len(right_pins), 1)
    BOX_H    = max_rows * ROW_H + 2 * PAD_Y

    ports = []
    for i, pin in enumerate(left_pins):
        dx = -(BOX_W / 2 + PIN_STUB)
        dy = -(BOX_H / 2 - PAD_Y) + i * ROW_H
        ports.append({"name": pin.get("name", str(pin.get("number", i + 1))), "dx": dx, "dy": dy})
    for i, pin in enumerate(right_pins):
        dx = +(BOX_W / 2 + PIN_STUB)
        dy = -(BOX_H / 2 - PAD_Y) + i * ROW_H
        ports.append({"name": pin.get("name", str(pin.get("number", i + 1))), "dx": dx, "dy": dy})

    return {
        "BOX_W":      BOX_W,
        "BOX_H":      BOX_H,
        "PIN_STUB":   PIN_STUB,
        "ROW_H":      ROW_H,
        "PAD_Y":      PAD_Y,
        "leftPins":   left_pins,
        "rightPins":  right_pins,
        "ports":      ports,
        "w":          BOX_W + 2 * PIN_STUB,
        "h":          BOX_H,
    }


# ── Netlist extraction helpers ────────────────────────────────────────────────

_SNAP_GRID = 12

def _snap(v: float) -> int:
    return round(v / _SNAP_GRID) * _SNAP_GRID

def _pt_key(x, y) -> str:
    return f"{_snap(x)},{_snap(y)}"

# SYMDEFS port offsets (dx, dy) for each symbol type at rotation 0
_SYMDEFS: dict[str, list[tuple[str, int, int]]] = {
    "resistor":      [("P1", -30, 0), ("P2", 30, 0)],
    "capacitor":     [("P1", 0, -20), ("P2", 0, 20)],
    "capacitor_pol": [("+"  , 0, -20), ("-"  , 0, 20)],
    "inductor":      [("P1", -40, 0), ("P2", 40, 0)],
    "vcc":           [("VCC", 0, 20)],
    "gnd":           [("GND", 0, -20)],
    "diode":         [("A", -30, 0), ("K", 30, 0)],
    "led":           [("A", -30, 0), ("K", 30, 0)],
    "npn":           [("B", -30, 0), ("C", 20, -25), ("E", 20, 25)],
    "pnp":           [("B", -30, 0), ("E", 20, -25), ("C", 20, 25)],
    "nmos":          [("G", -30, 0), ("D", 20, -25), ("S", 20, 25)],
    "pmos":          [("G", -30, 0), ("D", 20, -25), ("S", 20, 25)],
    "amplifier":     [("IN", -50, 0), ("OUT", 50, 0), ("GND", 0, 40)],
    "opamp":         [("+"  , -50, -20), ("-"  , -50, 20), ("OUT", 50, 0)],
}

def _rotate_offset(dx: int, dy: int, r: int) -> tuple[int, int]:
    for _ in range(r % 4):
        dx, dy = dy, -dx
    return dx, dy

_POWER_NAMES = {"VCC", "VDD", "GND", "GND", "3V3", "5V", "12V", "AGND", "DGND", "PGND", "VSS"}


def build_netlist(
    components: list,
    wires: list,
    labels: list,
    no_connects: list | None = None,
    *,
    library_dir: Path | None = None,
) -> dict:
    """Extract netlist from schematic JSON using union-find.

    Parameters
    ----------
    components, wires, labels, no_connects : schematic element lists
    library_dir : path to library directory for IC profile loading
    """

    # Union-Find
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        if parent.setdefault(x, x) != x:
            parent[x] = find(parent[x])
        return parent[x]

    def union(a: str, b: str):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    def ensure(x: str):
        parent.setdefault(x, x)

    # Load IC profiles for port computation
    def _comp_ports(comp: dict) -> list[tuple[str, int, int]]:
        """Return list of (node_id, wx, wy) for each port of a component."""
        cid  = comp.get("id", "")
        cx   = comp.get("x", 0)
        cy   = comp.get("y", 0)
        r    = comp.get("rotation", 0)
        sym  = comp.get("symType", "ic")

        offsets = _SYMDEFS.get(sym)
        if offsets:
            result = []
            for name, dx, dy in offsets:
                rdx, rdy = _rotate_offset(dx, dy, r)
                node_id = f"port::{cid}::{name}"
                result.append((node_id, cx + rdx, cy + rdy))
            return result

        # IC: load layout from profile
        slug = comp.get("slug", "")
        pins: list = []
        if library_dir is not None:
            profile_path = library_dir / slug / "profile.json"
            if profile_path.exists():
                try:
                    profile = json.loads(profile_path.read_text("utf-8"))
                    pins = profile.get("pins", [])
                except Exception:
                    pass

        layout = ic_layout(pins)
        result = []
        for port in layout["ports"]:
            rdx, rdy = _rotate_offset(int(port["dx"]), int(port["dy"]), r)
            node_id = f"port::{cid}::{port['name']}"
            result.append((node_id, cx + rdx, cy + rdy))
        return result

    # 1. Build portNodes and ptMap
    portNodes: list[tuple[str, int, int]] = []  # (node_id, wx, wy)
    ptMap: dict[str, list[str]] = {}            # key -> [node_ids]

    for comp in components:
        for node_id, wx, wy in _comp_ports(comp):
            ensure(node_id)
            portNodes.append((node_id, wx, wy))
            k = _pt_key(wx, wy)
            ptMap.setdefault(k, []).append(node_id)

    # 2. Wire nodes
    for wire in wires:
        wid = wire.get("id", id(wire))
        pts = wire.get("points", [])
        for pi, pt in enumerate(pts):
            node_id = f"wire::{wid}::{pi}"
            ensure(node_id)
            k = _pt_key(pt["x"], pt["y"])
            ptMap.setdefault(k, []).append(node_id)

    # 3. Union all nodes sharing same pt_key
    for key, nodes in ptMap.items():
        for ni in nodes[1:]:
            union(nodes[0], ni)

    # 4. Wire chain: adjacent points within each wire
    for wire in wires:
        wid = wire.get("id", id(wire))
        pts = wire.get("points", [])
        for pi in range(len(pts) - 1):
            union(f"wire::{wid}::{pi}", f"wire::{wid}::{pi + 1}")

    # 5. T-junction: wire endpoint on interior segment of another wire
    def _pt_on_segment(px, py, ax, ay, bx, by) -> bool:
        if ax == bx:  # vertical
            if _snap(px) != _snap(ax):
                return False
            miny, maxy = (ay, by) if ay < by else (by, ay)
            return miny < _snap(py) < maxy
        if ay == by:  # horizontal
            if _snap(py) != _snap(ay):
                return False
            minx, maxx = (ax, bx) if ax < bx else (bx, ax)
            return minx < _snap(px) < maxx
        return False

    wire_segs: list[tuple[str, str, int, int, int, int, int, int]] = []
    for wire in wires:
        wid = wire.get("id", id(wire))
        pts = wire.get("points", [])
        for pi in range(len(pts) - 1):
            p0, p1 = pts[pi], pts[pi + 1]
            wire_segs.append((
                wid, f"wire::{wid}::{pi}", f"wire::{wid}::{pi + 1}",
                _snap(p0["x"]), _snap(p0["y"]),
                _snap(p1["x"]), _snap(p1["y"]),
            ))

    for wire2 in wires:
        wid2 = wire2.get("id", id(wire2))
        pts2 = wire2.get("points", [])
        for pi, pt in enumerate(pts2):
            if pi not in (0, len(pts2) - 1):
                continue  # only endpoints
            ep_node = f"wire::{wid2}::{pi}"
            px, py = _snap(pt["x"]), _snap(pt["y"])
            for seg in wire_segs:
                seg_wid = seg[0]
                if seg_wid == wid2:
                    continue
                n0, n1, ax, ay, bx, by = seg[1], seg[2], seg[3], seg[4], seg[5], seg[6]
                if _pt_on_segment(px, py, ax, ay, bx, by):
                    union(ep_node, n0)
                    union(ep_node, n1)

    # 6. Port-on-wire: comp port on interior of a wire segment
    for node_id, wx, wy in portNodes:
        px, py = _snap(wx), _snap(wy)
        for seg in wire_segs:
            n0, n1, ax, ay, bx, by = seg[1], seg[2], seg[3], seg[4], seg[5], seg[6]
            if _pt_on_segment(px, py, ax, ay, bx, by):
                union(node_id, n0)
                union(node_id, n1)

    # 7. Labels: union with wire/port at same position + same-name labels
    label_nodes: dict[str, str] = {}  # label_id -> node_id
    label_name_to_nodes: dict[str, list[str]] = {}

    for lbl in labels:
        lid = lbl.get("id", id(lbl))
        lx  = lbl.get("x", 0)
        ly  = lbl.get("y", 0)
        lname = lbl.get("name", lbl.get("text", ""))
        lnode = f"lbl::{lid}"
        ensure(lnode)
        label_nodes[str(lid)] = lnode
        # union with coincident wire/port nodes
        k = _pt_key(lx, ly)
        for other in ptMap.get(k, []):
            union(lnode, other)
        label_name_to_nodes.setdefault(lname, []).append(lnode)

    # Union all nodes with same label name
    for lname, lnodes in label_name_to_nodes.items():
        for ni in lnodes[1:]:
            union(lnodes[0], ni)

    # 8. Group portNodes by union root -> nets
    root_to_ports: dict[str, list[str]] = {}
    for node_id, wx, wy in portNodes:
        r = find(node_id)
        root_to_ports.setdefault(r, []).append(node_id)

    # 9. Name nets
    def _port_designator(node_id: str, comp_map: dict) -> str:
        """Convert port::compId::pinName -> DESIGNATOR.pinName"""
        parts = node_id.split("::")
        if len(parts) < 3:
            return node_id
        cid   = parts[1]
        pname = parts[2]
        comp  = comp_map.get(cid, {})
        des   = comp.get("designator") or comp.get("id", cid)
        return f"{des}.{pname}"

    comp_map = {c.get("id", ""): c for c in components}
    lbl_name_map: dict[str, str] = {}
    for lbl in labels:
        lid   = str(lbl.get("id", id(lbl)))
        lname = lbl.get("name", lbl.get("text", ""))
        lnode = label_nodes.get(lid, "")
        if lnode:
            lbl_name_map[find(lnode)] = lname

    net_counter = [0]
    named_nets: list[dict] = []
    root_to_net: dict[str, str] = {}
    used_names: set[str] = set()

    def _assign(root: str, name: str):
        name = name.upper()
        root_to_net[root] = name
        used_names.add(name)

    # Priority 1: VCC/GND symbol types
    for comp in components:
        sym = comp.get("symType", "")
        cid = comp.get("id", "")
        if sym == "vcc":
            r = find(f"port::{cid}::VCC") if f"port::{cid}::VCC" in parent else None
            if r and r not in root_to_net:
                net_label = comp.get("value") or "VCC"
                _assign(r, net_label)
        elif sym == "gnd":
            r = find(f"port::{cid}::GND") if f"port::{cid}::GND" in parent else None
            if r and r not in root_to_net:
                _assign(r, "GND")

    # Priority 2: named power pins
    for comp in components:
        cid = comp.get("id", "")
        for node_id, wx, wy in portNodes:
            if not node_id.startswith(f"port::{cid}::"):
                continue
            pname = node_id.split("::")[-1].upper()
            if pname in _POWER_NAMES:
                r = find(node_id)
                if r not in root_to_net:
                    net_name = pname if pname not in used_names else f"{pname}_{cid}"
                    _assign(r, net_name)

    # Priority 3: wire labels
    for root, lname in lbl_name_map.items():
        if root not in root_to_net and lname:
            _assign(root, lname)

    # Priority 3b: No-Connect markers
    if no_connects:
        nc_positions: set[str] = {_pt_key(nc.get("x", 0), nc.get("y", 0)) for nc in no_connects}
        for node_id, wx, wy in portNodes:
            if _pt_key(wx, wy) in nc_positions:
                r = find(node_id)
                if r not in root_to_net:
                    _assign(r, "NC")

    # Priority 4: NC check (single port, name NC or pin type nc)
    for root, ports_in in root_to_ports.items():
        if root in root_to_net:
            continue
        if len(ports_in) == 1:
            pname = ports_in[0].split("::")[-1].upper()
            if pname in ("NC", "NO_CONNECT"):
                _assign(root, "NC")

    # Priority 5: passive 1-hop propagation
    for root, ports_in in root_to_ports.items():
        if root in root_to_net:
            continue
        for pnode in ports_in:
            parts = pnode.split("::")
            if len(parts) < 3:
                continue
            cid   = parts[1]
            pname = parts[2]
            comp  = comp_map.get(cid, {})
            sym   = comp.get("symType", "ic")
            if sym not in ("resistor", "capacitor", "capacitor_pol", "inductor", "diode", "led"):
                continue
            other_port = "P2" if pname == "P1" else "P1"
            other_node = f"port::{cid}::{other_port}"
            if other_node not in parent:
                other_node = f"port::{cid}::cathode" if pname == "anode" else f"port::{cid}::anode"
            if other_node in parent:
                other_root = find(other_node)
                inherited = root_to_net.get(other_root)
                if inherited:
                    _assign(root, f"{inherited}_{pname}")

    # Priority 6: auto-name remaining port-bearing nets N1, N2 ...
    for root in root_to_ports:
        if root not in root_to_net:
            net_counter[0] += 1
            _assign(root, f"N{net_counter[0]}")

    # Priority 7: wire-only clusters (no component ports)
    for wire in wires:
        wid = wire.get("id", id(wire))
        node0 = f"wire::{wid}::0"
        if node0 not in parent:
            continue
        r = find(node0)
        if r not in root_to_net:
            net_counter[0] += 1
            _assign(r, f"N{net_counter[0]}")

    # 10. Build wireToNet
    wire_to_net: dict[str, str] = {}
    for wire in wires:
        wid = wire.get("id", id(wire))
        node0 = f"wire::{wid}::0"
        if node0 in parent:
            r = find(node0)
            net_name = root_to_net.get(r, "")
            if net_name:
                wire_to_net[str(wid)] = net_name

    # Build namedNets (pin-reference format for PCB/downstream tools)
    for root, ports_in in root_to_ports.items():
        net_name = root_to_net.get(root, f"N?_{root[:6]}")
        pins_out = [_port_designator(n, comp_map) for n in ports_in]
        named_nets.append({"name": net_name, "pins": pins_out})

    named_nets.sort(key=lambda n: n["name"])

    # Build frontend-friendly nets list
    root_to_port_coords: dict[str, list[dict]] = {}
    for node_id, wx, wy in portNodes:
        r = find(node_id)
        parts = node_id.split("::")
        cid = parts[1] if len(parts) > 1 else ""
        comp = comp_map.get(cid, {})
        port_name = parts[2] if len(parts) > 2 else ""
        root_to_port_coords.setdefault(r, []).append({
            "x": int(wx), "y": int(wy),
            "symType": comp.get("symType", "ic"),
            "portName": port_name,
            "nodeId": f"{cid}::{port_name}",
        })

    nets_out: list[dict] = []
    emitted: set[str] = set()
    for root, net_name in root_to_net.items():
        if root in emitted:
            continue
        emitted.add(root)
        ports_coords = root_to_port_coords.get(root, [])
        nets_out.append({"name": net_name, "ports": ports_coords})
    nets_out.sort(key=lambda n: n["name"])

    return {"namedNets": named_nets, "nets": nets_out, "wireToNet": wire_to_net}
