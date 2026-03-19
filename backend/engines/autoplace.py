"""Autoplace engine — force-directed net-proximity component placement.

Pure algorithm: takes board dict + directory paths, returns updated board dict.
No Redis, no endpoints, no filesystem writes.
"""
from __future__ import annotations

import json
import math
import os
import re
from collections import Counter
from pathlib import Path
from typing import Any


def _load_schematic_hints(
    project_id: str,
    board_width_mm: float,
    board_height_mm: float,
    *,
    projects_dir: Path,
) -> dict[str, tuple[float, float]]:
    """Load schematic component positions and normalise to board-mm space."""
    proj_path = projects_dir / f"{project_id}.json"
    if not proj_path.exists():
        return {}
    try:
        proj = json.loads(proj_path.read_text("utf-8"))
    except Exception:
        return {}

    SKIP_TYPES = {"vcc", "gnd", "pwr", "power"}
    raw: list[tuple[str, float, float]] = []
    for c in proj.get("components", []):
        sym_type = c.get("symType", "").lower()
        if sym_type in SKIP_TYPES:
            continue
        designator = c.get("designator", "")
        sx = c.get("x")
        sy = c.get("y")
        if designator and sx is not None and sy is not None:
            raw.append((designator, float(sx), float(sy)))

    if not raw:
        return {}

    xs = [r[1] for r in raw]
    ys = [r[2] for r in raw]
    cx = sum(xs) / len(xs)
    cy = sum(ys) / len(ys)

    max_extent = max(
        max(abs(x - cx) for x in xs) or 1.0,
        max(abs(y - cy) for y in ys) or 1.0,
    )

    usable_half_x = (board_width_mm  / 2.0) * 0.70
    usable_half_y = (board_height_mm / 2.0) * 0.70
    scale = min(usable_half_x, usable_half_y) / max_extent

    bx_center = board_width_mm  / 2.0
    by_center = board_height_mm / 2.0

    hints: dict[str, tuple[float, float]] = {}
    for designator, sx, sy in raw:
        hx = bx_center + (sx - cx) * scale
        hy = by_center + (sy - cy) * scale
        hints[designator] = (hx, hy)

    return hints


def _positions_are_spread(components: list[dict]) -> bool:
    """Return True if components have meaningfully different positions."""
    if len(components) < 2:
        return True
    xs = [c.get("x", 0) for c in components]
    ys = [c.get("y", 0) for c in components]
    spread = max(max(xs) - min(xs), max(ys) - min(ys))
    if spread <= 2.0:
        return False
    positions = [(round(c.get("x", 0), 1), round(c.get("y", 0), 1)) for c in components]
    most_common_count = Counter(positions).most_common(1)[0][1]
    if most_common_count / len(components) > 0.4:
        return False
    return True


def run_autoplace(
    board: dict,
    min_clearance_mm: float = 1.0,
    *,
    projects_dir: Path | None = None,
    output_dir: Path | None = None,
) -> dict:
    """Force-directed net-proximity autoplacer.

    Parameters
    ----------
    board : board JSON dict
    min_clearance_mm : minimum component clearance
    projects_dir : path to projects directory (for schematic hints)
    output_dir : path to outputs directory (for example_schematic.json rotations)
    """
    from agents.autoplace.placement_optimizer import compute_greedy_placement

    bw = float(board.get("board", {}).get("width", 100))
    bh = float(board.get("board", {}).get("height", 100))

    # ── Filter: skip pure power/GND symbols ──────────────────────────────────
    _SKIP_REF_PREFIXES = ("GND", "VDD", "VCC", "PWR", "AGND", "DGND", "PGND")
    _SKIP_VALS  = {"GND", "VDD", "VCC", "PWR_FLAG"}
    all_components = [dict(c) for c in board.get("components", [])]
    components = [
        c for c in all_components
        if c.get("footprint", "").strip()
        and not any(c.get("ref", "").upper().startswith(p) for p in _SKIP_REF_PREFIXES)
        and c.get("value", "").upper() not in _SKIP_VALS
    ]
    if not components:
        return {**board}

    # ── Separate grouped components from free components ─────────────────────
    grouped: dict[str, list[dict]] = {}
    free_components: list[dict] = []
    for c in components:
        gid = c.get("groupId", "")
        if gid:
            grouped.setdefault(gid, []).append(c)
        else:
            free_components.append(c)

    group_anchors: dict[str, dict] = {}
    group_offsets: dict[str, list[tuple]] = {}
    for gid, members in grouped.items():
        xs = [m["x"] for m in members]
        ys = [m["y"] for m in members]
        cx = sum(xs) / len(xs)
        cy = sum(ys) / len(ys)
        group_nets: list[str] = []
        for m in members:
            for p in m.get("pads", []):
                n = (p.get("net", "") or "").upper()
                if n and n not in group_nets:
                    group_nets.append(n)
        group_offsets[gid] = [
            (m["ref"], m["x"] - cx, m["y"] - cy, m.get("rotation", 0))
            for m in members
        ]
        half_w = max((max(xs) - min(xs)) / 2 + 2, 3)
        half_h = max((max(ys) - min(ys)) / 2 + 2, 3)
        virtual = {
            "ref": f"__group_{gid}",
            "id":  f"__group_{gid}",
            "value": f"group:{gid}",
            "footprint": f"GROUP_{len(members)}",
            "x": cx, "y": cy,
            "rotation": 0,
            "layer": "F",
            "pads": [
                {"number": "1", "x": -half_w, "y": 0, "type": "smd",
                 "shape": "rect", "size_x": half_w * 2, "size_y": half_h * 2,
                 "net": group_nets[0] if group_nets else ""},
            ],
        }
        group_anchors[gid] = virtual
        free_components.append(virtual)

    components = free_components

    # ── Build comp_ref -> list[net_name] ─────────────────────────────────────
    nets = board.get("nets", [])
    comp_nets: dict[str, list[str]] = {c["ref"]: [] for c in components}
    for net in nets:
        net_name = (net.get("name", "") or "").upper()
        if not net_name:
            continue
        for pad_ref in net.get("pads", []):
            ref = pad_ref.split(".")[0]
            if ref in comp_nets and net_name not in comp_nets[ref]:
                comp_nets[ref].append(net_name)

    for comp in components:
        ref = comp.get("ref", "")
        for pad in comp.get("pads", []):
            net_name = (pad.get("net", "") or "").upper()
            if net_name and ref in comp_nets and net_name not in comp_nets[ref]:
                comp_nets[ref].append(net_name)

    for gid, members in grouped.items():
        anchor_ref = f"__group_{gid}"
        if anchor_ref not in comp_nets:
            comp_nets[anchor_ref] = []
        for m in members:
            for p in m.get("pads", []):
                n = (p.get("net", "") or "").upper()
                if n and n not in comp_nets[anchor_ref]:
                    comp_nets[anchor_ref].append(n)

    # ── Load schematic hints ─────────────────────────────────────────────────
    schematic_hints: dict[str, tuple[float, float]] = {}
    project_id = board.get("projectId", "")
    if project_id and projects_dir is not None:
        schematic_hints = _load_schematic_hints(project_id, bw, bh, projects_dir=projects_dir)

    using_board_positions = False
    if not schematic_hints and _positions_are_spread(components):
        for comp in components:
            ref = comp.get("ref", comp.get("id", ""))
            cx = comp.get("x")
            cy = comp.get("y")
            if ref and cx is not None and cy is not None:
                schematic_hints[ref] = (float(cx), float(cy))
        using_board_positions = bool(schematic_hints)

    # ── Save original rotations ──────────────────────────────────────────────
    orig_rotations: dict[str, int] = {}
    for comp in all_components:
        ref = comp.get("ref", comp.get("id", ""))
        orig_rotations[ref] = comp.get("rotation", 0)

    # ── Load PCB rotations from example_schematic.json ───────────────────────
    pcb_rotations: dict[str, int] = {}
    if output_dir is not None:
        _example_sch_path = output_dir / "example_schematic.json"
        if _example_sch_path.exists():
            try:
                _example_sch = json.loads(_example_sch_path.read_text("utf-8"))
                for _ec in _example_sch.get("components", []):
                    _ref = _ec.get("reference", "")
                    if _ref and "rotation" in _ec:
                        pcb_rotations[_ref] = int(_ec["rotation"])
            except Exception:
                pass

    # ── Fill in missing pad-level net fields ─────────────────────────────────
    _pad_ref_to_net: dict[str, str] = {}
    for net in nets:
        net_name = (net.get("name", "") or "").upper()
        if not net_name:
            continue
        for pad_ref in net.get("pads", []):
            _pad_ref_to_net[pad_ref.upper()] = net_name
    for comp in components:
        ref = comp.get("ref", "")
        for pad in comp.get("pads", []):
            if not (pad.get("net", "") or "").strip():
                pad_num = pad.get("number", pad.get("name", ""))
                pad_key = f"{ref}.{pad_num}".upper()
                if pad_key in _pad_ref_to_net:
                    pad["net"] = _pad_ref_to_net[pad_key]

    # ── Build optimizer-format component list ────────────────────────────────
    opt_components = [
        {
            "reference": c.get("ref", c.get("id", "?")),
            "value":     c.get("value", ""),
            "footprint": c.get("footprint", ""),
            "rotation":  pcb_rotations.get(
                             c.get("ref", c.get("id", "")),
                             orig_rotations.get(c.get("ref", c.get("id", "")), 0)
                         ),
            "nets":      comp_nets.get(c.get("ref", c.get("id", "")), []),
            "pads":      c.get("pads", []),
        }
        for c in components
    ]

    # Fuzzy fallback for group-suffixed refs
    if schematic_hints:
        suffix_pat = re.compile(r'_g\d+$|_inst\d+$|_ch\d+$')
        for comp in opt_components:
            ref = comp["reference"]
            if ref in schematic_hints:
                continue
            base = suffix_pat.sub("", ref)
            if base != ref and base in schematic_hints:
                bx_base, by_base = schematic_hints[base]
                n_dup = sum(1 for k in schematic_hints
                            if suffix_pat.sub("", k) == base)
                schematic_hints[ref] = (bx_base + (n_dup % 3) * 4.0,
                                        by_base + (n_dup // 3) * 4.0)

    # ── Determine hint_weight ────────────────────────────────────────────────
    has_nets = any(net.get("name") for net in nets)
    if using_board_positions:
        hw = 0.85
    elif not has_nets and schematic_hints:
        hw = 0.75
    elif schematic_hints:
        hw = 0.4
    else:
        hw = 0.0

    # ── Run placement ────────────────────────────────────────────────────────
    placements = compute_greedy_placement(
        components=opt_components,
        board_width_mm=bw,
        board_height_mm=bh,
        min_clearance_mm=min_clearance_mm,
    )

    # ── Write x/y and rotation back ──────────────────────────────────────────
    placement_by_ref = {p["reference"]: p for p in placements}
    for comp in components:
        ref = comp.get("ref", comp.get("id", ""))
        if ref.startswith("__group_"):
            continue
        if ref in placement_by_ref:
            comp["x"] = placement_by_ref[ref]["x"]
            comp["y"] = placement_by_ref[ref]["y"]
            comp["rotation"] = pcb_rotations.get(
                ref,
                placement_by_ref[ref].get("rotation", orig_rotations.get(ref, 0))
            )

    # ── Expand virtual group anchors ─────────────────────────────────────────
    for gid, offsets in group_offsets.items():
        anchor_ref = f"__group_{gid}"
        if anchor_ref not in placement_by_ref:
            continue
        ax = placement_by_ref[anchor_ref]["x"]
        ay = placement_by_ref[anchor_ref]["y"]
        for member_ref, dx, dy, rot in offsets:
            for m in grouped.get(gid, []):
                if m["ref"] == member_ref:
                    m["x"] = round(ax + dx, 2)
                    m["y"] = round(ay + dy, 2)
                    m["rotation"] = rot
                    break

    # Merge all components
    placed_map = {c["ref"]: c for c in components if not c.get("ref", "").startswith("__group_")}
    for members in grouped.values():
        for m in members:
            placed_map[m["ref"]] = m
    merged = []
    for orig_comp in all_components:
        ref = orig_comp.get("ref", "")
        merged.append(placed_map.get(ref, orig_comp))

    result = dict(board)
    result["components"] = merged
    return result
