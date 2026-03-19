"""Autoroute engine — multi-layer A* trace router with via support.

Pure algorithm: takes board dict + optional projects_dir, returns updated board dict.
No Redis, no endpoints, no filesystem writes.
"""
from __future__ import annotations

import heapq
import json
import math
import os
from pathlib import Path

from .netlist import build_netlist


def _autoroute_skip_net(net_name: str) -> bool:
    """Return True for nets that must NOT be routed as individual traces."""
    if not net_name:
        return True
    upper = net_name.upper().lstrip("/\\~ ")
    for prefix in ("NC", "PWR_FLAG"):
        if upper == prefix or upper.startswith(prefix + "_") or upper.startswith(prefix + "-"):
            return True
    for sub in ("GND", "UNCONNECTED", "NOCONNECT", "NO_CONNECT"):
        if sub in upper:
            return True
    return False


def _autoroute_trace_width(net_name: str, dr: dict) -> float:
    """Return trace width in mm for a given net (power nets get wider traces)."""
    default_w = float(dr.get("traceWidth", dr.get("minTraceWidth", 0.25)))
    upper = net_name.upper()
    power_keywords = ("VCC", "VDD", "VIN", "VBAT", "VBUS", "3V3", "5V", "12V",
                      "24V", "AVCC", "DVCC", "VPW", "VMOT", "VPWR", "PWR")
    if any(kw in upper for kw in power_keywords):
        pwr_w = float(dr.get("powerTraceWidth", 0.4))
        return max(default_w, pwr_w)
    return default_w


def run_autoroute(
    board: dict,
    *,
    projects_dir: Path | None = None,
    library_dir: Path | None = None,
) -> dict:
    """Route unconnected nets on a PCB board using multi-layer A*.

    Parameters
    ----------
    board : board JSON dict
    projects_dir : path to projects directory (for fallback netlist recovery)
    library_dir : path to library directory (passed to build_netlist)
    """
    dr   = board.get("designRules", {})
    GRID = max(0.1, float(dr.get("routingGrid", 0.25)))
    bw   = float(board.get("board", {}).get("width",  board.get("width",  200)))
    bh   = float(board.get("board", {}).get("height", board.get("height", 200)))
    cu_clearance = float(dr.get("clearance", 0.2))
    edge_clearance = float(dr.get("routeEdgeClearance", dr.get("edgeClearance", 0.5)))

    # ── Expand grid to cover all component positions ──
    _max_comp_x = bw
    _max_comp_y = bh
    for _c in board.get("components", []):
        _cx_c = float(_c.get("x", 0))
        _cy_c = float(_c.get("y", 0))
        _max_comp_x = max(_max_comp_x, _cx_c + 3)
        _max_comp_y = max(_max_comp_y, _cy_c + 3)
    _eff_w = max(bw, _max_comp_x)
    _eff_h = max(bh, _max_comp_y)

    # ── Occupancy grid ────────────────────────────────────────────────────────
    grid_w = int(_eff_w / GRID) + 2
    grid_h = int(_eff_h / GRID) + 2
    occupied: set[tuple[int, int]] = set()

    _default_trace_w = float(dr.get("traceWidth", 0.25))
    _trace_expand = max(1, int(math.ceil((_default_trace_w / 2 + cu_clearance) / GRID)))

    def _mark_segment(x0: float, y0: float, x1: float, y1: float,
                      occ: set[tuple[int, int]] | None = None) -> None:
        target = occ if occ is not None else occupied
        steps = max(int(abs(x1 - x0) / GRID), int(abs(y1 - y0) / GRID)) + 1
        for s in range(steps + 1):
            t = s / max(steps, 1)
            gx = int(round((x0 + t * (x1 - x0)) / GRID))
            gy = int(round((y0 + t * (y1 - y0)) / GRID))
            for ex in range(-_trace_expand, _trace_expand + 1):
                for ey in range(-_trace_expand, _trace_expand + 1):
                    target.add((gx + ex, gy + ey))

    for trace in board.get("traces", []):
        for seg in trace.get("segments", []):
            _mark_segment(
                seg.get("start", {}).get("x", 0), seg.get("start", {}).get("y", 0),
                seg.get("end",   {}).get("x", 0), seg.get("end",   {}).get("y", 0),
            )

    # ── Block board edges ──────────────────────────────────────────────────────
    edge_cells = max(1, int(math.ceil(edge_clearance / GRID)))
    for gx in range(grid_w):
        for gy in range(edge_cells):
            occupied.add((gx, gy))
            occupied.add((gx, grid_h - 1 - gy))
    for gy in range(grid_h):
        for gx in range(edge_cells):
            occupied.add((gx, gy))
            occupied.add((grid_w - 1 - gx, gy))

    # ── Overwrite pad.net from board.nets[] array ─────────────────────────────
    _pad_ref_to_net_ar: dict[str, str] = {}
    for _net_entry in board.get("nets", []):
        _nn = (_net_entry.get("name", "") or "").upper()
        if not _nn:
            continue
        for _pr in _net_entry.get("pads", []):
            _pad_ref_to_net_ar[_pr.upper()] = _nn

    # ── Fallback: compute netlist from schematic project ──────────────────────
    if not _pad_ref_to_net_ar and projects_dir is not None:
        project_id = board.get("projectId", "")
        if project_id:
            _proj_path = projects_dir / f"{project_id}.json"
            if _proj_path.exists():
                try:
                    _proj = json.loads(_proj_path.read_text("utf-8"))
                    _netlist_result = build_netlist(
                        _proj.get("components", []),
                        _proj.get("wires", []),
                        _proj.get("labels", []),
                        _proj.get("noConnects", []),
                        library_dir=library_dir,
                    )
                    _pin_to_board_key: dict[str, str] = {}
                    for _bc in board.get("components", []):
                        _bc_ref = _bc.get("ref", "")
                        for _bp in _bc.get("pads", []):
                            _bp_name = (_bp.get("name", "") or "").upper()
                            _bp_num  = str(_bp.get("number", ""))
                            _board_key = f"{_bc_ref}.{_bp_num}".upper()
                            if _bp_name and _bc_ref:
                                _pin_to_board_key[f"{_bc_ref}.{_bp_name}"] = _board_key
                                _clean = _bp_name.replace("-", "").replace(" ", "").replace("(", "").replace(")", "").replace("!", "").replace("/", "")
                                if _clean != _bp_name:
                                    _pin_to_board_key[f"{_bc_ref}.{_clean}"] = _board_key
                            _pin_to_board_key[f"{_bc_ref}.P{_bp_num}"] = _board_key
                            _pin_to_board_key[_board_key] = _board_key

                    _named_nets = _netlist_result.get("namedNets", [])
                    if isinstance(_named_nets, list):
                        for _net_entry in _named_nets:
                            _nn_name = str(_net_entry.get("name", "")).upper()
                            if not _nn_name:
                                continue
                            for _pin_ref in _net_entry.get("pins", []):
                                _pr_upper = str(_pin_ref).upper()
                                _pr_parts = _pr_upper.split(".", 1)
                                if len(_pr_parts) == 2 and _pr_parts[0] in (
                                    "GND", "GND1", "GND2", "VDD", "VCC", "PWR"):
                                    continue
                                _board_key = _pin_to_board_key.get(_pr_upper)
                                if _board_key:
                                    _pad_ref_to_net_ar[_board_key] = _nn_name
                except Exception:
                    pass

    # Build old->new net name map
    _old_to_new_net: dict[str, str] = {}
    if _pad_ref_to_net_ar:
        for comp in board.get("components", []):
            ref = comp.get("ref", comp.get("id", ""))
            for pad in comp.get("pads", []):
                pnum = pad.get("number", pad.get("name", ""))
                pkey = f"{ref}.{pnum}".upper()
                if pkey in _pad_ref_to_net_ar:
                    old_net = (pad.get("net", "") or "").upper()
                    new_net = _pad_ref_to_net_ar[pkey]
                    if old_net and old_net != new_net:
                        _old_to_new_net[old_net] = new_net
                    pad["net"] = new_net

        for trace in board.get("traces", []):
            tn = (trace.get("net", "") or "").upper()
            if tn in _old_to_new_net:
                trace["net"] = _old_to_new_net[tn]

    # ── Build net -> [(x, y)] map ─────────────────────────────────────────────
    net_pads: dict[str, list[tuple[float, float, int]]] = {}
    nc_pad_positions: list[tuple[float, float]] = []
    nc_nets: set[str] = set()
    _net_has_real_pad: set[str] = set()
    _NC_EXACT = {"NC", "N/C", "N.C.", "NOCONNECT", "NO_CONNECT", "NO CONNECT"}
    _NC_PREFIXES = ("NC", "N/C", "NOCONNECT", "NO_CONNECT")

    def _is_nc(name: str, net: str) -> bool:
        n = name.upper().strip()
        t = net.upper().strip()
        if n in _NC_EXACT or t in _NC_EXACT:
            return True
        for pfx in _NC_PREFIXES:
            if n.startswith(pfx + " ") or n.startswith(pfx + "(") or n.startswith(pfx + "_"):
                return True
            if t.startswith(pfx + " ") or t.startswith(pfx + "(") or t.startswith(pfx + "_"):
                return True
        return False

    _nc_cells_by_comp: dict[str, set[tuple[int, int]]] = {}
    _comp_nets: dict[str, set[str]] = {}

    for comp in board.get("components", []):
        cx  = float(comp.get("x", 0))
        cy  = float(comp.get("y", 0))
        rot = float(comp.get("rotation", 0)) * math.pi / 180
        cos_r, sin_r = math.cos(rot), math.sin(rot)
        comp_layer = 1 if comp.get("layer", "F") == "B" else 0
        comp_ref = comp.get("ref", comp.get("id", ""))
        for pad in comp.get("pads", []):
            net = (pad.get("net", "") or "").upper()
            lx = float(pad.get("x", 0))
            ly = float(pad.get("y", 0))
            px = cx + lx * cos_r - ly * sin_r
            py = cy + lx * sin_r + ly * cos_r
            pad_name = (pad.get("name", "") or "").upper().strip()
            is_nc_pad = _is_nc(pad_name, net)
            if is_nc_pad:
                nc_pad_positions.append((px, py))
                if comp_ref:
                    _nc_cells_by_comp.setdefault(comp_ref, set())
                if net:
                    nc_nets.add(net)
                continue
            if net and comp_ref:
                _comp_nets.setdefault(comp_ref, set()).add(net)
            if not net:
                continue
            pad_layer = comp_layer
            if pad.get("type") == "thru_hole":
                pad_layer = 0
            _net_has_real_pad.add(net)
            net_pads.setdefault(net, []).append((px, py, pad_layer))
    nc_only_nets = nc_nets - _net_has_real_pad

    # ── Block occupancy around pad positions ──────────────────────────────────
    def _pad_cells(px: float, py: float,
                   half_w: float = 0.0, half_h: float = 0.0) -> set[tuple[int, int]]:
        rx = max(1, int(math.ceil((half_w + cu_clearance + _default_trace_w / 2) / GRID)))
        ry = max(1, int(math.ceil((half_h + cu_clearance + _default_trace_w / 2) / GRID)))
        gc_x = int(round(px / GRID))
        gc_y = int(round(py / GRID))
        cells: set[tuple[int, int]] = set()
        for dx in range(-rx, rx + 1):
            for dy in range(-ry, ry + 1):
                cells.add((gc_x + dx, gc_y + dy))
        return cells

    _pad_hw_map: dict[tuple[float, float], tuple[float, float]] = {}
    for comp in board.get("components", []):
        cx  = float(comp.get("x", 0))
        cy  = float(comp.get("y", 0))
        rot = float(comp.get("rotation", 0)) * math.pi / 180
        cos_r, sin_r = math.cos(rot), math.sin(rot)
        for pad in comp.get("pads", []):
            lx = float(pad.get("x", 0))
            ly = float(pad.get("y", 0))
            px = cx + lx * cos_r - ly * sin_r
            py = cy + lx * sin_r + ly * cos_r
            sx = float(pad.get("size_x", pad.get("sizeX", 0.5)))
            sy = float(pad.get("size_y", pad.get("sizeY", 0.5)))
            if abs(rot) > 0.01:
                hw = max(abs(sx * cos_r), abs(sy * sin_r)) / 2
                hh = max(abs(sx * sin_r), abs(sy * cos_r)) / 2
            else:
                hw, hh = sx / 2, sy / 2
            _pad_hw_map[(round(px, 4), round(py, 4))] = (hw, hh)

    def _get_pad_hw(px: float, py: float) -> tuple[float, float]:
        return _pad_hw_map.get((round(px, 4), round(py, 4)), (0.25, 0.25))

    # ── Block component bodies ────────────────────────────────────────────────
    _comp_body_cells: dict[str, set[tuple[int, int]]] = {}
    for comp in board.get("components", []):
        cx  = float(comp.get("x", 0))
        cy  = float(comp.get("y", 0))
        rot = float(comp.get("rotation", 0)) * math.pi / 180
        cos_r, sin_r = math.cos(rot), math.sin(rot)
        comp_pads = comp.get("pads", [])
        if len(comp_pads) < 2:
            continue
        pad_positions: list[tuple[float, float, float, float]] = []
        comp_net_names: set[str] = set()
        for pad in comp_pads:
            lx = float(pad.get("x", 0))
            ly = float(pad.get("y", 0))
            wx = cx + lx * cos_r - ly * sin_r
            wy = cy + lx * sin_r + ly * cos_r
            sx = float(pad.get("size_x", pad.get("sizeX", 0.5)))
            sy = float(pad.get("size_y", pad.get("sizeY", 0.5)))
            if abs(rot) > 0.01:
                hw = max(abs(sx * cos_r), abs(sy * sin_r)) / 2
                hh = max(abs(sx * sin_r), abs(sy * cos_r)) / 2
            else:
                hw, hh = sx / 2, sy / 2
            pad_positions.append((wx, wy, hw, hh))
            net = (pad.get("net", "") or "").upper()
            if net:
                comp_net_names.add(net)
        min_x = min(wx - hw for wx, wy, hw, hh in pad_positions)
        max_x = max(wx + hw for wx, wy, hw, hh in pad_positions)
        min_y = min(wy - hh for wx, wy, hw, hh in pad_positions)
        max_y = max(wy + hh for wx, wy, hw, hh in pad_positions)
        exp = cu_clearance + _default_trace_w / 2
        min_x -= exp; max_x += exp; min_y -= exp; max_y += exp
        gx0 = int(math.floor(min_x / GRID))
        gx1 = int(math.ceil(max_x / GRID))
        gy0 = int(math.floor(min_y / GRID))
        gy1 = int(math.ceil(max_y / GRID))
        body_cells: set[tuple[int, int]] = set()
        for gx in range(gx0, gx1 + 1):
            for gy in range(gy0, gy1 + 1):
                body_cells.add((gx, gy))
        for net in comp_net_names:
            _comp_body_cells.setdefault(net, set()).update(body_cells)
        occupied |= body_cells

    # Block NC pads
    _nc_blocked_cells: set[tuple[int, int]] = set()
    for ncx, ncy in nc_pad_positions:
        hw, hh = _get_pad_hw(ncx, ncy)
        nc_cells = _pad_cells(ncx, ncy, hw, hh)
        _nc_blocked_cells |= nc_cells
    occupied |= _nc_blocked_cells

    # Populate NC cells per component
    for comp in board.get("components", []):
        cx  = float(comp.get("x", 0))
        cy  = float(comp.get("y", 0))
        rot = float(comp.get("rotation", 0)) * math.pi / 180
        cos_r, sin_r = math.cos(rot), math.sin(rot)
        comp_ref = comp.get("ref", comp.get("id", ""))
        for pad in comp.get("pads", []):
            pad_name = (pad.get("name", "") or "").upper().strip()
            net = (pad.get("net", "") or "").upper()
            if _is_nc(pad_name, net):
                lx = float(pad.get("x", 0))
                ly = float(pad.get("y", 0))
                px = cx + lx * cos_r - ly * sin_r
                py = cy + lx * sin_r + ly * cos_r
                hw, hh = _get_pad_hw(px, py)
                nc_cells = _pad_cells(px, py, hw, hh)
                _nc_cells_by_comp.setdefault(comp_ref, set()).update(nc_cells)

    _nc_unblock_for_net: dict[str, set[tuple[int, int]]] = {}
    for comp_ref, comp_net_set in _comp_nets.items():
        nc_cells_comp = _nc_cells_by_comp.get(comp_ref, set())
        if nc_cells_comp:
            for net_name_cn in comp_net_set:
                _nc_unblock_for_net.setdefault(net_name_cn, set()).update(nc_cells_comp)

    # Block all routable pads
    all_pad_cells: dict[str, set[tuple[int, int]]] = {}
    _via_size_mm = float(dr.get("viaSize", 1.0))
    _no_via_cells: set[tuple[int, int]] = set()

    def _pad_via_cells(px: float, py: float, half_w: float, half_h: float) -> set[tuple[int, int]]:
        rx = max(1, int(math.ceil(half_w / GRID)))
        ry = max(1, int(math.ceil(half_h / GRID)))
        gc_x = int(round(px / GRID))
        gc_y = int(round(py / GRID))
        cells: set[tuple[int, int]] = set()
        for ddx in range(-rx, rx + 1):
            for ddy in range(-ry, ry + 1):
                cells.add((gc_x + ddx, gc_y + ddy))
        return cells

    for net_name_p, pads_p in net_pads.items():
        cells: set[tuple[int, int]] = set()
        for pad_tuple in pads_p:
            px, py = pad_tuple[0], pad_tuple[1]
            hw, hh = _get_pad_hw(px, py)
            pad_cells = _pad_cells(px, py, hw, hh)
            cells |= pad_cells
            _no_via_cells |= _pad_via_cells(px, py, hw, hh)
        all_pad_cells[net_name_p] = cells
        occupied |= cells

    # ── Multi-layer A* with via support ───────────────────────────────────────
    DIRS = [(1, 0), (-1, 0), (0, 1), (0, -1)]
    LAYERS = ("F.Cu", "B.Cu")
    occupied_b: set[tuple[int, int]] = set()
    occupied_b |= _nc_blocked_cells
    occupied_b |= _no_via_cells
    _occupied_by_layer = [occupied, occupied_b]
    via_size_mm = float(dr.get("viaSize", 1.0))
    via_drill_mm = float(dr.get("viaDrill", 0.6))
    VIA_PENALTY = max(4, int(via_size_mm / GRID))
    allow_vias = bool(dr.get("allowVias", True))

    def _bfs(sx: float, sy: float, ex: float, ey: float,
             force_single_layer: bool = False,
             start_layer: int = 0,
             relaxed: bool = False,
             ) -> tuple[list[tuple[float, float, int]], bool]:
        sg = (int(round(sx / GRID)), int(round(sy / GRID)), start_layer)
        eg_xy = (int(round(ex / GRID)), int(round(ey / GRID)))
        if sg[:2] == eg_xy:
            return [(sx, sy, start_layer), (ex, ey, start_layer)], False

        def _h(gx: int, gy: int) -> int:
            return abs(gx - eg_xy[0]) + abs(gy - eg_xy[1])

        g_cost: dict[tuple[int, int, int], int] = {sg: 0}
        prev: dict[tuple[int, int, int], tuple[int, int, int]] = {}
        pq: list = [(_h(sg[0], sg[1]), 0, sg)]
        n_layers = 2 if (allow_vias and not force_single_layer) else 1
        _max_visits = grid_w * grid_h * n_layers * 2

        visited = 0
        while pq:
            f, g, cur = heapq.heappop(pq)
            visited += 1
            if visited > _max_visits:
                break
            if g > g_cost.get(cur, 10**9):
                continue
            if cur[0] == eg_xy[0] and cur[1] == eg_xy[1]:
                path: list[tuple[float, float, int]] = []
                node = cur
                while node in prev:
                    path.append((node[0] * GRID, node[1] * GRID, node[2]))
                    node = prev[node]
                path.append((sg[0] * GRID, sg[1] * GRID, sg[2]))
                path.reverse()
                if path:
                    path[0] = (sx, sy, path[0][2])
                    path.append((ex, ey, path[-1][2]))
                used_via = len(set(p[2] for p in path)) > 1
                return path, used_via

            layer = cur[2]
            occ = _occupied_by_layer[layer]

            for dx, dy in DIRS:
                nxt = (cur[0] + dx, cur[1] + dy, layer)
                if nxt[0] < 0 or nxt[1] < 0 or nxt[0] >= grid_w or nxt[1] >= grid_h:
                    continue
                if (nxt[0], nxt[1]) in occ:
                    continue
                ng = g + 1
                if ng < g_cost.get(nxt, 10**9):
                    g_cost[nxt] = ng
                    prev[nxt] = cur
                    heapq.heappush(pq, (ng + _h(nxt[0], nxt[1]), ng, nxt))

            if n_layers > 1:
                other = 1 - layer
                nxt_via = (cur[0], cur[1], other)
                if ((cur[0], cur[1]) not in _occupied_by_layer[other]
                        and (cur[0], cur[1]) not in _no_via_cells):
                    ng = g + VIA_PENALTY
                    if ng < g_cost.get(nxt_via, 10**9):
                        g_cost[nxt_via] = ng
                        prev[nxt_via] = cur
                        heapq.heappush(pq, (ng + _h(nxt_via[0], nxt_via[1]), ng, nxt_via))

        return [], False

    # ── Identify already-routed nets ──────────────────────────────────────────
    def _trace_connectivity(net_name_tc: str, pads_tc: list[tuple[float, float, int]]) -> bool:
        if len(pads_tc) < 2:
            return True
        snap_th = GRID * 2
        endpoints: list[tuple[float, float]] = []
        segs: list[tuple[float, float, float, float]] = []
        for _et in board.get("traces", []):
            _en = (_et.get("net", "") or "").upper()
            if _en != net_name_tc:
                continue
            for _seg in _et.get("segments", []):
                sx_t = float(_seg.get("start", {}).get("x", 0))
                sy_t = float(_seg.get("start", {}).get("y", 0))
                ex_t = float(_seg.get("end", {}).get("x", 0))
                ey_t = float(_seg.get("end", {}).get("y", 0))
                segs.append((sx_t, sy_t, ex_t, ey_t))
                endpoints.append((sx_t, sy_t))
                endpoints.append((ex_t, ey_t))
        if not segs:
            return False
        parent_tc: dict[int, int] = {}
        def _find_tc(a: int) -> int:
            while parent_tc.get(a, a) != a:
                parent_tc[a] = parent_tc.get(parent_tc[a], parent_tc[a])
                a = parent_tc[a]
            return a
        def _union_tc(a: int, b: int) -> None:
            ra, rb = _find_tc(a), _find_tc(b)
            if ra != rb:
                parent_tc[ra] = rb
        all_pts: list[tuple[float, float]] = [(p[0], p[1]) for p in pads_tc] + endpoints
        n_pads = len(pads_tc)
        for i in range(len(all_pts)):
            for j in range(i + 1, len(all_pts)):
                if abs(all_pts[i][0] - all_pts[j][0]) < snap_th and abs(all_pts[i][1] - all_pts[j][1]) < snap_th:
                    _union_tc(i, j)
        idx_base = n_pads
        for si, (sx_t, sy_t, ex_t, ey_t) in enumerate(segs):
            _union_tc(idx_base + si * 2, idx_base + si * 2 + 1)
        roots = set(_find_tc(i) for i in range(n_pads))
        return len(roots) == 1

    _existing_routed_nets: set[str] = set()

    new_traces: list[dict] = list(board.get("traces", []))
    all_vias: list[dict] = list(board.get("vias", []))

    # ── Route each UNROUTED net ───────────────────────────────────────────────
    routed = 0
    total  = 0
    skipped_existing = 0
    failed_nets: list[str] = []

    def _net_priority(name: str) -> int:
        upper = name.upper()
        power_keywords = ("VCC", "VDD", "VIN", "VBAT", "VBUS", "3V3", "5V",
                          "12V", "24V", "AVCC", "DVCC", "VPW", "VMOT", "VPWR")
        return 0 if any(kw in upper for kw in power_keywords) else 1

    for _check_net, _check_pads in net_pads.items():
        if len(_check_pads) >= 2 and _trace_connectivity(_check_net, _check_pads):
            _existing_routed_nets.add(_check_net)

    for net_name in sorted(net_pads.keys(), key=lambda n: (_net_priority(n), n)):
        if _autoroute_skip_net(net_name) or net_name in nc_only_nets:
            continue
        pads_xy = net_pads[net_name]
        if len(pads_xy) < 2:
            continue
        total += 1

        if net_name in _existing_routed_nets:
            skipped_existing += 1
            routed += 1
            continue

        width_mm = _autoroute_trace_width(net_name, dr)

        own_cells = all_pad_cells.get(net_name, set())
        own_body_cells = _comp_body_cells.get(net_name, set())
        own_nc_cells = _nc_unblock_for_net.get(net_name, set())
        occupied -= own_cells
        occupied -= own_body_cells
        occupied -= own_nc_cells
        occupied_b -= own_cells
        occupied_b -= own_body_cells
        occupied_b -= own_nc_cells

        remaining: list[tuple[float, float, int]] = list(pads_xy)
        connected: list[tuple[float, float, int]] = [remaining.pop(0)]
        per_layer_segs: dict[str, list[dict]] = {}
        vias_list: list[dict] = []
        all_routed = True
        _same_net_occ_f: set[tuple[int, int]] = set()
        _same_net_occ_b: set[tuple[int, int]] = set()

        _is_rf_net = net_name.upper().startswith("RF")

        while remaining:
            best_i    = 0
            best_d    = float("inf")
            best_src  = connected[0]
            for i, cand in enumerate(remaining):
                for src in connected:
                    d = abs(cand[0] - src[0]) + abs(cand[1] - src[1])
                    if d < best_d:
                        best_d   = d
                        best_i   = i
                        best_src = src

            dest = remaining.pop(best_i)
            connected.append(dest)

            if _same_net_occ_f:
                occupied -= _same_net_occ_f
            if _same_net_occ_b:
                occupied_b -= _same_net_occ_b

            src_layer = best_src[2] if len(best_src) > 2 else 0
            path, used_via = _bfs(best_src[0], best_src[1], dest[0], dest[1],
                                  force_single_layer=_is_rf_net,
                                  start_layer=src_layer)
            if not path:
                if _is_rf_net:
                    path, used_via = _bfs(best_src[0], best_src[1], dest[0], dest[1],
                                          force_single_layer=False,
                                          start_layer=src_layer)

            if _same_net_occ_f:
                occupied |= _same_net_occ_f
            if _same_net_occ_b:
                occupied_b |= _same_net_occ_b

            if not path:
                all_routed = False
                continue

            # Simplify path: merge collinear points
            if len(path) > 2:
                simplified: list[tuple[float, float, int]] = [path[0]]
                for k in range(1, len(path) - 1):
                    x_prev, y_prev, l_prev = simplified[-1]
                    x_cur, y_cur, l_cur = path[k]
                    x_nxt, y_nxt, l_nxt = path[k + 1]
                    if l_prev != l_cur or l_cur != l_nxt:
                        simplified.append(path[k])
                        continue
                    dx1 = x_cur - x_prev
                    dy1 = y_cur - y_prev
                    dx2 = x_nxt - x_cur
                    dy2 = y_nxt - y_cur
                    if abs(dx1 * dy2 - dy1 * dx2) < 1e-6:
                        continue
                    simplified.append(path[k])
                simplified.append(path[-1])
                path = simplified

            # Chamfer 90deg corners
            if len(path) >= 3:
                chamfered: list[tuple[float, float, int]] = [path[0]]
                chamfer_d = GRID
                for k in range(1, len(path) - 1):
                    x_p, y_p, l_p = chamfered[-1]
                    x_c, y_c, l_c = path[k]
                    x_n, y_n, l_n = path[k + 1]
                    if l_p != l_c or l_c != l_n:
                        chamfered.append(path[k])
                        continue
                    is_h1 = abs(y_c - y_p) < 0.001
                    is_v1 = abs(x_c - x_p) < 0.001
                    is_h2 = abs(y_n - y_c) < 0.001
                    is_v2 = abs(x_n - x_c) < 0.001
                    if (is_h1 and is_v2) or (is_v1 and is_h2):
                        seg1_len = math.hypot(x_c - x_p, y_c - y_p)
                        seg2_len = math.hypot(x_n - x_c, y_n - y_c)
                        cd = min(chamfer_d, seg1_len * 0.4, seg2_len * 0.4)
                        if cd > 0.01:
                            dx1 = (x_c - x_p) / max(seg1_len, 1e-9)
                            dy1 = (y_c - y_p) / max(seg1_len, 1e-9)
                            c1x = x_c - dx1 * cd
                            c1y = y_c - dy1 * cd
                            dx2 = (x_n - x_c) / max(seg2_len, 1e-9)
                            dy2 = (y_n - y_c) / max(seg2_len, 1e-9)
                            c2x = x_c + dx2 * cd
                            c2y = y_c + dy2 * cd
                            chamfered.append((round(c1x, 4), round(c1y, 4), l_c))
                            chamfered.append((round(c2x, 4), round(c2y, 4), l_c))
                            continue
                    chamfered.append(path[k])
                chamfered.append(path[-1])
                path = chamfered

            # Split path into per-layer segments and insert vias
            for j in range(len(path) - 1):
                x0, y0, l0 = path[j]
                x1, y1, l1 = path[j + 1]
                if l0 != l1:
                    vias_list.append({
                        "x": round(x0, 4), "y": round(y0, 4),
                        "size": via_size_mm, "drill": via_drill_mm,
                        "net": net_name,
                    })
                    via_r = max(1, int(math.ceil((via_size_mm / 2 + cu_clearance) / GRID)))
                    vg = (int(round(x0 / GRID)), int(round(y0 / GRID)))
                    for vdx in range(-via_r, via_r + 1):
                        for vdy in range(-via_r, via_r + 1):
                            _vc = (vg[0] + vdx, vg[1] + vdy)
                            occupied.add(_vc)
                            occupied_b.add(_vc)
                            _same_net_occ_f.add(_vc)
                            _same_net_occ_b.add(_vc)
                    continue
                if abs(x1 - x0) < 0.001 and abs(y1 - y0) < 0.001:
                    continue
                layer_name = LAYERS[l0]
                per_layer_segs.setdefault(layer_name, []).append({
                    "start": {"x": round(x0, 4), "y": round(y0, 4)},
                    "end":   {"x": round(x1, 4), "y": round(y1, 4)},
                })
                _seg_cells: set[tuple[int, int]] = set()
                _mark_segment(x0, y0, x1, y1, occ=_occupied_by_layer[l0])
                _mark_segment(x0, y0, x1, y1, occ=_seg_cells)
                if l0 == 0:
                    _same_net_occ_f |= _seg_cells
                else:
                    _same_net_occ_b |= _seg_cells

        # Re-block pads and body cells
        occupied |= own_cells
        occupied |= own_body_cells
        occupied |= own_nc_cells
        occupied_b |= own_cells
        occupied_b |= own_body_cells
        occupied_b |= own_nc_cells

        for layer_name, segs in per_layer_segs.items():
            if segs:
                new_traces.append({
                    "net":      net_name,
                    "layer":    layer_name,
                    "width":    width_mm,
                    "segments": segs,
                })
        all_vias.extend(vias_list)
        if per_layer_segs:
            routed += 1
        if not all_routed:
            failed_nets.append(net_name)

    # ── Post-route DRC ────────────────────────────────────────────────────────
    _all_pad_rects: list[tuple[float, float, float, float, str]] = []
    for comp in board.get("components", []):
        cx_c = float(comp.get("x", 0))
        cy_c = float(comp.get("y", 0))
        rot_c = float(comp.get("rotation", 0)) * math.pi / 180
        cos_c, sin_c = math.cos(rot_c), math.sin(rot_c)
        for pad in comp.get("pads", []):
            pnet = (pad.get("net", "") or "").upper()
            if not pnet:
                continue
            lx = float(pad.get("x", 0))
            ly = float(pad.get("y", 0))
            px_w = cx_c + lx * cos_c - ly * sin_c
            py_w = cy_c + lx * sin_c + ly * cos_c
            sx = float(pad.get("size_x", pad.get("sizeX", 0.5)))
            sy = float(pad.get("size_y", pad.get("sizeY", 0.5)))
            if abs(rot_c) > 0.01:
                hw_p = max(abs(sx * cos_c), abs(sy * sin_c)) / 2
                hh_p = max(abs(sx * sin_c), abs(sy * cos_c)) / 2
            else:
                hw_p, hh_p = sx / 2, sy / 2
            _all_pad_rects.append((px_w, py_w, hw_p, hh_p, pnet))

    def _seg_crosses_pad(x0: float, y0: float, x1: float, y1: float, tw: float,
                         pcx: float, pcy: float, phw: float, phh: float) -> bool:
        ehw = phw + tw / 2 + cu_clearance
        ehh = phh + tw / 2 + cu_clearance
        smin_x, smax_x = (min(x0, x1) - tw / 2, max(x0, x1) + tw / 2)
        smin_y, smax_y = (min(y0, y1) - tw / 2, max(y0, y1) + tw / 2)
        if smax_x < pcx - ehw or smin_x > pcx + ehw:
            return False
        if smax_y < pcy - ehh or smin_y > pcy + ehh:
            return False
        dx, dy = x1 - x0, y1 - y0
        seg_len2 = dx * dx + dy * dy
        if seg_len2 < 1e-12:
            return abs(x0 - pcx) < ehw and abs(y0 - pcy) < ehh
        t = max(0.0, min(1.0, ((pcx - x0) * dx + (pcy - y0) * dy) / seg_len2))
        closest_x = x0 + t * dx
        closest_y = y0 + t * dy
        return abs(closest_x - pcx) < ehw and abs(closest_y - pcy) < ehh

    violations: list[dict] = []
    _seen_violations: set[tuple[float, float]] = set()
    for trace in new_traces:
        tnet = (trace.get("net", "") or "").upper()
        tw = float(trace.get("width", _default_trace_w))
        for seg in trace.get("segments", []):
            sx0 = float(seg["start"]["x"])
            sy0 = float(seg["start"]["y"])
            sx1 = float(seg["end"]["x"])
            sy1 = float(seg["end"]["y"])
            for pcx_p, pcy_p, phw_p, phh_p, pnet in _all_pad_rects:
                if pnet == tnet:
                    continue
                if pnet == "NC" or pnet.startswith("NC_") or pnet.startswith("NC "):
                    continue
                if _seg_crosses_pad(sx0, sy0, sx1, sy1, tw, pcx_p, pcy_p, phw_p, phh_p):
                    vkey = (round(pcx_p, 2), round(pcy_p, 2))
                    if vkey not in _seen_violations:
                        _seen_violations.add(vkey)
                        violations.append({
                            "type": "NET_CONFLICT",
                            "x": round(pcx_p, 4),
                            "y": round(pcy_p, 4),
                            "trace_net": tnet,
                            "pad_net": pnet,
                            "message": f"Trace '{tnet}' crosses pad of net '{pnet}'",
                        })

    result = dict(board)
    for comp in result.get("components", []):
        for pad in comp.get("pads", []):
            if pad.get("net"):
                pad["net"] = pad["net"].upper()
    for net_obj in result.get("nets", []):
        if net_obj.get("name"):
            net_obj["name"] = net_obj["name"].upper()
    result["traces"] = new_traces
    result["vias"] = all_vias
    return {**result, "_autoroute": {
        "routed": routed, "total": total, "vias": len(all_vias),
        "violations": violations, "failed_nets": failed_nets,
        "kept_existing": skipped_existing,
    }}
