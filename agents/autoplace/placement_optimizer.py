"""
Placement Optimizer — silkscreen overlap checker + net-proximity placer using Polars.

Two main capabilities:

1. check_package_gaps(placements)
   Post-placement DRC: verifies every pair of components satisfies the minimum
   silkscreen-to-silkscreen gap (body-edge to body-edge clearance).

2. compute_net_proximity_placement(components, ...)
   Pre-placement algorithm: force-directed, net-proximity optimised placement.
   Pulls components toward their net-neighbours and enforces the silkscreen gap.

Usage:
    from agents.autoplace.placement_optimizer import (
        check_package_gaps,
        compute_net_proximity_placement,
    )
    placements = compute_net_proximity_placement(components, board_width_mm=100, board_height_mm=80)
    result = check_package_gaps(placements)
    # result = {"violations": [...], "is_valid": bool}
"""
from __future__ import annotations

import math
import random
import re
from typing import Any

import polars as pl


# ---------------------------------------------------------------------------
# Footprint size estimation
# ---------------------------------------------------------------------------

# Patterns like "0402", "0603", "0805", "1206" → width_mm, height_mm
# These are pad-inclusive bounding box sizes (pads + 0.4 mm visual margin),
# matching what the PCB editor draws as the silkscreen outline.
# Bare chip body would be smaller; using pad-inclusive ensures the optimizer
# enforces clearance based on the full visible silkscreen, not just the chip body.
_IMPERIAL_MAP: dict[str, tuple[float, float]] = {
    "0201": (1.4,  0.9),
    "0402": (2.5,  1.5),
    "0603": (3.5,  1.8),
    "0805": (4.0,  2.2),
    "1206": (5.2,  2.4),
    "1210": (5.2,  3.4),
    "2010": (7.0,  3.4),
    "2512": (8.5,  4.2),
}

# SOT packages — pad-inclusive silkscreen bounding box
_SOT_MAP: dict[str, tuple[float, float]] = {
    "SOT-23":   (3.7,  2.2),
    "SOT-23-5": (3.7,  2.6),
    "SOT-23-6": (3.7,  2.6),
    "SOT-223":  (8.0,  5.0),
    "SOT-89":   (6.0,  4.0),
    "SOT-363":  (3.0,  3.0),
    "SOT-323":  (3.0,  2.2),
}

# SOIC packages — look for "SOIC-N" where N is pin count
_SOIC_PIN_PITCH = 1.27  # mm between pins
_SOIC_BODY_WIDTH = 6.0  # mm


def _estimate_footprint_size(footprint: str) -> tuple[float, float]:
    """Return (width_mm, height_mm) estimate for a given footprint string.

    Priority:
    1. Explicit body-size suffix: "QFN-12-3x3" → 3×3 mm
    2. Known package table (imperial passives, SOT, etc.)
    3. Pin-count heuristic as a last resort
    """
    fp = footprint.upper().strip()

    # ── 1. Explicit WxH body-size anywhere in the string ──────────────
    # Matches patterns like "3X3", "3.0X3.0", "4X4", "3.9X4.9"
    # Add 0.8 mm (0.4 mm each side) as courtyard/pad margin so the optimizer
    # uses the full visible silkscreen outline, not just the bare chip body.
    m = re.search(r"(\d+(?:\.\d+)?)\s*X\s*(\d+(?:\.\d+)?)", fp)
    if m:
        w = float(m.group(1)) + 0.8
        h = float(m.group(2)) + 0.8
        if 0.5 <= w <= 55 and 0.5 <= h <= 55:  # sanity range
            return (w, h)

    # Imperial passive sizes (0402, 0603, etc.)
    for code, size in _IMPERIAL_MAP.items():
        if code in fp:
            return size

    # SOT packages
    for pkg, size in _SOT_MAP.items():
        if pkg.upper() in fp:
            return size

    # SOIC-N — estimate height from pin count; add 0.8 mm pad/courtyard margin
    m = re.search(r"SOIC[-_](\d+)", fp)
    if m:
        n_pins = int(m.group(1))
        pins_per_side = n_pins // 2
        height = (pins_per_side - 1) * _SOIC_PIN_PITCH + 2.0 + 0.8  # body + margin
        return (_SOIC_BODY_WIDTH + 1.6, height)  # width: leads extend ~0.8 mm each side

    # QFN-N or QFP-N — pin-count heuristic; add 0.8 mm courtyard margin
    m = re.search(r"QF[NP][-_](\d+)", fp)
    if m:
        n_pins = int(m.group(1))
        side = max(3.0, n_pins * 0.4) + 0.8
        return (side, side)

    # DIP-N — add lead/courtyard margin
    m = re.search(r"DIP[-_](\d+)", fp)
    if m:
        n_pins = int(m.group(1))
        pins_per_side = n_pins // 2
        height = (pins_per_side - 1) * 2.54 + 2.0 + 0.8
        return (9.0, height)  # DIP leads extend well beyond body

    # TO-220, TO-92 etc
    if "TO-220" in fp:
        return (10.0, 14.5)
    if "TO-92" in fp:
        return (5.0, 5.5)
    if "TO-263" in fp or "D2PAK" in fp:
        return (10.4, 9.0)

    # Connector — use a generous default
    if any(k in fp for k in ("CONN", "USB", "JACK", "HDR", "HEADER")):
        return (10.0, 5.0)

    # Generic fallback
    return (5.0, 5.0)


# ---------------------------------------------------------------------------
# Package gap check (minimum clearance between silkscreen outlines)
# ---------------------------------------------------------------------------

# Minimum gap between component silkscreen outlines (body-edge to body-edge).
# Silkscreen = the printed package outline on the PCB.  Components must not be
# placed so close that their silkscreen lines overlap or merge.
MIN_CLEARANCE_MM = 1.0  # silkscreen-to-silkscreen gap (body edge to body edge)


def _build_dataframe(placements: list[dict[str, Any]]) -> pl.DataFrame:
    """Build a Polars DataFrame with bounding-box columns added."""
    rows = []
    for p in placements:
        ref = p.get("reference", "?")
        x = float(p.get("x", 0.0))
        y = float(p.get("y", 0.0))
        fp = p.get("footprint", "")
        w, h = _estimate_footprint_size(fp)
        # half-extents = silkscreen boundary = package body / 2
        # Silkscreen is drawn at the package body outline; MIN_CLEARANCE_MM is
        # enforced between these outlines so silkscreen lines never overlap.
        half_w = w / 2.0
        half_h = h / 2.0
        rows.append({
            "reference": ref,
            "x": x,
            "y": y,
            "footprint": fp,
            "half_w": half_w,
            "half_h": half_h,
            "cx_min": x - half_w,
            "cx_max": x + half_w,
            "cy_min": y - half_h,
            "cy_max": y + half_h,
        })
    return pl.DataFrame(rows)


def _aabb_overlap(
    ax_min: float, ax_max: float, ay_min: float, ay_max: float,
    bx_min: float, bx_max: float, by_min: float, by_max: float,
    clearance: float,
) -> bool:
    """Return True if the two axis-aligned bounding boxes violate clearance."""
    gap_x = max(ax_min - bx_max, bx_min - ax_max)
    gap_y = max(ay_min - by_max, by_min - ay_max)
    # Effective gap is the larger of the two (if negative → overlap)
    effective_gap = max(gap_x, gap_y)
    return effective_gap < clearance


def check_package_gaps(
    placements: list[dict[str, Any]],
    min_clearance_mm: float = MIN_CLEARANCE_MM,
) -> dict[str, Any]:
    """
    Check package gaps for a list of placement dicts.

    Each placement dict must contain:
        reference (str), x (float), y (float), footprint (str)

    Returns:
        {
            "violations": [
                {
                    "component_a": str,
                    "component_b": str,
                    "gap_mm": float,
                    "message": str,
                }
            ],
            "is_valid": bool
        }
    """
    if not placements:
        return {"violations": [], "is_valid": True}

    df = _build_dataframe(placements)

    violations: list[dict[str, Any]] = []

    # Convert to plain Python lists for pairwise iteration — Polars is used for
    # the DataFrame construction / any future aggregation.
    refs   = df["reference"].to_list()
    cx_min = df["cx_min"].to_list()
    cx_max = df["cx_max"].to_list()
    cy_min = df["cy_min"].to_list()
    cy_max = df["cy_max"].to_list()

    for i in range(len(refs)):
        for j in range(i + 1, len(refs)):
            if _aabb_overlap(
                cx_min[i], cx_max[i], cy_min[i], cy_max[i],
                cx_min[j], cx_max[j], cy_min[j], cy_max[j],
                min_clearance_mm,
            ):
                gap_x = max(cx_min[i] - cx_max[j], cx_min[j] - cx_max[i])
                gap_y = max(cy_min[i] - cy_max[j], cy_min[j] - cy_max[i])
                gap = max(gap_x, gap_y)
                violations.append({
                    "component_a": refs[i],
                    "component_b": refs[j],
                    "gap_mm": round(gap, 4),
                    "message": (
                        f"Silkscreen overlap: {refs[i]} and {refs[j]} have "
                        f"gap {gap:.3f}mm (min {min_clearance_mm}mm)"
                    ),
                })

    return {
        "violations": violations,
        "is_valid": len(violations) == 0,
    }


def summarize_violations(result: dict[str, Any]) -> str:
    """Return a human-readable summary suitable for feeding back to the LLM."""
    if result["is_valid"]:
        return "All package gaps OK."
    lines = [f"Found {len(result['violations'])} silkscreen overlap(s):"]
    for v in result["violations"]:
        lines.append(f"  - {v['message']}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Greedy pad-based Tetris placement (primary algorithm)
# ---------------------------------------------------------------------------

_CONNECTOR_KEYWORDS = ("CONN", "USB", "JACK", "HDR", "HEADER", "TERMINAL")
_POWER_KEYWORDS = ("PWR", "VCC", "VDD", "GND", "POWER", "REGUL", "AMS1117", "AP2112", "LDO")
_BYPASS_CAP_REFS = ("C",)  # reference prefixes that are likely decoupling caps


_IC_FP_KEYWORDS = ("QFN", "QFP", "TQFP", "SOIC", "SOP", "SSOP", "DIP", "BGA", "LGA", "WLCSP", "TO-220", "TO-263", "D2PAK")
_IC_REF_PREFIXES = ("U", "IC", "MCU", "DSP", "FPGA")


def _is_connector(comp: dict[str, Any]) -> bool:
    fp = comp.get("footprint", "").upper()
    return any(k in fp for k in _CONNECTOR_KEYWORDS)


def _is_ic(comp: dict[str, Any]) -> bool:
    ref = comp.get("reference", "").upper()
    fp  = comp.get("footprint", "").upper()
    return (
        any(ref.startswith(p) for p in _IC_REF_PREFIXES)
        or any(k in fp for k in _IC_FP_KEYWORDS)
    )


def _is_power(comp: dict[str, Any]) -> bool:
    val = comp.get("value", "").upper()
    ref = comp.get("reference", "").upper()
    fp = comp.get("footprint", "").upper()
    return any(k in val or k in fp for k in _POWER_KEYWORDS) or ref.startswith("PWR") or ref.startswith("VCC") or ref.startswith("VDD") or ref.startswith("GND")


# ---------------------------------------------------------------------------
# Pad geometry helpers — a Pad is (local_x, local_y, half_w, half_h)
# relative to the component centre, already rotated.
# ---------------------------------------------------------------------------

_Pad = tuple[float, float, float, float]


def _extract_pads(comp: dict[str, Any]) -> list[_Pad]:
    """Extract pad rectangles from component data, rotated to match placement.

    If the component carries real pad geometry (from the board JSON) we use it
    directly.  Otherwise we create a single fallback pad covering the estimated
    footprint size so the algorithm degrades to bounding-box behaviour.
    """
    raw_pads = comp.get("pads", [])
    rotation = comp.get("rotation", 0) % 360

    if raw_pads:
        result: list[_Pad] = []
        for p in raw_pads:
            lx = float(p.get("x", 0))
            ly = float(p.get("y", 0))
            phw = float(p.get("size_x", 0.5)) / 2.0
            phh = float(p.get("size_y", 0.5)) / 2.0
            if rotation == 90:
                lx, ly = -ly, lx
                phw, phh = phh, phw
            elif rotation == 180:
                lx, ly = -lx, -ly
            elif rotation == 270:
                lx, ly = ly, -lx
                phw, phh = phh, phw
            result.append((lx, ly, phw, phh))
        return result

    # Fallback: single pad covering the estimated footprint body
    w, h = _estimate_footprint_size(comp.get("footprint", ""))
    phw, phh = w / 2.0, h / 2.0
    if rotation in (90, 270):
        phw, phh = phh, phw
    return [(0.0, 0.0, phw, phh)]


def _pad_envelope(pads: list[_Pad]) -> tuple[float, float]:
    """Bounding-box half-extents of all pads (for broad-phase rejection)."""
    if not pads:
        return (2.5, 2.5)
    max_x = max(abs(lx) + hw for lx, _, hw, _ in pads)
    max_y = max(abs(ly) + hh for _, ly, _, hh in pads)
    return (max_x, max_y)


def compute_greedy_placement(
    components: list[dict[str, Any]],
    board_width_mm: float = 100.0,
    board_height_mm: float = 80.0,
    min_clearance_mm: float = MIN_CLEARANCE_MM,
    seed: int = 42,
) -> list[dict[str, Any]]:
    """Pad-based Tetris placement algorithm.

    The collision shape is the actual copper pads, not a bounding box.
    Components slide toward their net group (same net = same colour) and
    lock in the moment their pads just clear + AUTO PLACE GAP.
    Groups of components sharing nets move together.

    The board size is used as a *soft* guide (centre placement) but never
    limits where components land — tight packing near net neighbours is
    always preferred over fitting inside the board outline.

    The autoplacer may rotate components (0/90/180/270°) to find the
    tightest fit against their net group.
    """
    n = len(components)
    if n == 0:
        return []

    gap = min_clearance_mm

    # ── Per-component mutable rotation + pad geometry ──────────────────
    rotations: list[int] = [c.get("rotation", 0) for c in components]

    def _rebuild_pads(i: int) -> None:
        """Recompute pads & envelope for component i at its current rotation."""
        c = dict(components[i])
        c["rotation"] = rotations[i]
        comp_pads[i] = _extract_pads(c)
        e = _pad_envelope(comp_pads[i])
        env_hw[i] = e[0]
        env_hh[i] = e[1]

    comp_pads: list[list[_Pad]] = [[] for _ in range(n)]
    env_hw: list[float] = [0.0] * n
    env_hh: list[float] = [0.0] * n
    for i in range(n):
        _rebuild_pads(i)

    # ── Net membership ─────────────────────────────────────────────────
    # GND / NC / power nets are deprioritised: they connect almost every
    # component and would pull everything into one lump if weighted equally.
    _LOW_PRIORITY_NETS = re.compile(
        r"^(GND|AGND|DGND|PGND|GND_.*|NC|N/C|NOCONNECT|NO_CONNECT"
        r"|VCC|VDD|VIN|VBAT|VBUS|3V3|5V|12V|24V|AVCC|DVCC"
        r"|PWR_FLAG|VPWR|VMOT|VPW)$",
        re.IGNORECASE,
    )
    _LOW_WEIGHT = 0.1  # signal nets get weight 1.0, GND/power get 0.1
    _RF_WEIGHT  = 3.0  # RF nets get extra pull — short traces are critical

    net_members: dict[str, set[int]] = {}
    net_weight: dict[str, float] = {}
    for i, comp in enumerate(components):
        for net in comp.get("nets", []):
            if net:
                net_members.setdefault(net, set()).add(i)
                if net not in net_weight:
                    if _LOW_PRIORITY_NETS.match(net):
                        net_weight[net] = _LOW_WEIGHT
                    elif net.upper().startswith("RF"):
                        net_weight[net] = _RF_WEIGHT
                    else:
                        net_weight[net] = 1.0

    def _shared(i: int, j: int) -> int:
        """Weighted count of shared nets (signal nets >> GND/power)."""
        total = 0.0
        for net_name, m in net_members.items():
            if i in m and j in m:
                total += net_weight.get(net_name, 1.0)
        # Return as int-compatible for sorting, but scaled up to preserve ordering
        return int(total * 10)

    # ── Pad-level net index: which pad of component i connects to net N? ──
    # comp_pad_nets[i][net_name] → list of pad indices into comp_pads[i]
    # We also store the raw pad data per-component to re-index after rotation.
    raw_pad_data: list[list[dict[str, Any]]] = [c.get("pads", []) for c in components]

    def _build_pad_net_index(i: int) -> dict[str, list[int]]:
        """Map net_name → list of pad indices in comp_pads[i]."""
        result: dict[str, list[int]] = {}
        raw = raw_pad_data[i]
        for pi, p in enumerate(raw):
            net = p.get("net", "")
            if net:
                result.setdefault(net, []).append(pi)
        return result

    comp_pad_nets: list[dict[str, list[int]]] = [_build_pad_net_index(i) for i in range(n)]

    def _net_anchor(j: int, shared_nets_with_i: list[str]) -> tuple[float, float]:
        """World position of j's connecting pad(s) for shared nets with i.

        Instead of using j's component centre, compute the centroid of j's
        pads that actually participate in the shared nets.  Falls back to
        j's centre if no pad-level net info is available.
        """
        pad_positions: list[tuple[float, float]] = []
        for net_name in shared_nets_with_i:
            pad_indices = comp_pad_nets[j].get(net_name, [])
            for pi in pad_indices:
                if pi < len(comp_pads[j]):
                    lx, ly, _, _ = comp_pads[j][pi]
                    pad_positions.append((pos[j][0] + lx, pos[j][1] + ly))
        if not pad_positions:
            return (pos[j][0], pos[j][1])
        cx = sum(p[0] for p in pad_positions) / len(pad_positions)
        cy = sum(p[1] for p in pad_positions) / len(pad_positions)
        return (cx, cy)

    def _shared_nets(i: int, j: int) -> list[str]:
        """Return shared net names, signal nets first (GND/power last)."""
        shared = [net for net, members in net_members.items() if i in members and j in members]
        # Sort: signal nets first (weight 1.0), GND/power last (weight 0.1)
        shared.sort(key=lambda n: -net_weight.get(n, 1.0))
        return shared

    pos: list[list[float]] = [[0.0, 0.0] for _ in range(n)]

    # ── Collision detection (pad-level) ────────────────────────────────
    def _collides(i: int, j: int) -> bool:
        """True if any copper pad of i overlaps any pad of j (+ gap)."""
        xi, yi = pos[i]
        xj, yj = pos[j]
        # Broad phase: envelope AABB
        if abs(xi - xj) - (env_hw[i] + env_hw[j]) >= gap:
            return False
        if abs(yi - yj) - (env_hh[i] + env_hh[j]) >= gap:
            return False
        # Narrow phase: pad vs pad
        for (lxi, lyi, hwi, hhi) in comp_pads[i]:
            pix, piy = xi + lxi, yi + lyi
            for (lxj, lyj, hwj, hhj) in comp_pads[j]:
                if (abs(pix - xj - lxj) < hwi + hwj + gap and
                        abs(piy - yj - lyj) < hhi + hhj + gap):
                    return True
        return False

    # ── Exact Tetris snap positions ────────────────────────────────────
    def _snap_positions(i: int, j: int) -> list[tuple[float, float]]:
        """Candidate positions where i locks adjacent to j with zero gap waste.

        Generates both centre-aligned and pin-aligned candidates.
        Pin-aligned candidates place i so that its connecting pad is
        directly adjacent to j's connecting pad on their shared net.
        """
        cx_j, cy_j = pos[j]
        pads_i, pads_j = comp_pads[i], comp_pads[j]
        results: list[tuple[float, float]] = []

        def _cardinal_snaps(anchor_x: float, anchor_y: float) -> list[tuple[float, float]]:
            """Compute 4 cardinal snaps for i relative to an anchor point."""
            out: list[tuple[float, float]] = []

            # RIGHT of j (cx_i > anchor_x, aligned at anchor_y)
            min_cx = -math.inf
            constrained = False
            for (lxi, lyi, hwi, hhi) in pads_i:
                for (lxj, lyj, hwj, hhj) in pads_j:
                    world_lyj = cy_j + lyj
                    world_lyi = anchor_y + lyi
                    if abs(world_lyi - world_lyj) < hhi + hhj + gap:
                        constrained = True
                        req = cx_j + lxj - lxi + hwi + hwj + gap
                        if req > min_cx:
                            min_cx = req
            if constrained:
                out.append((min_cx, anchor_y))

            # LEFT of j
            max_cx = math.inf
            constrained = False
            for (lxi, lyi, hwi, hhi) in pads_i:
                for (lxj, lyj, hwj, hhj) in pads_j:
                    world_lyj = cy_j + lyj
                    world_lyi = anchor_y + lyi
                    if abs(world_lyi - world_lyj) < hhi + hhj + gap:
                        constrained = True
                        req = cx_j + lxj - lxi - hwi - hwj - gap
                        if req < max_cx:
                            max_cx = req
            if constrained:
                out.append((max_cx, anchor_y))

            # BELOW j (cy_i > anchor_y, aligned at anchor_x)
            min_cy = -math.inf
            constrained = False
            for (lxi, lyi, hwi, hhi) in pads_i:
                for (lxj, lyj, hwj, hhj) in pads_j:
                    world_lxj = cx_j + lxj
                    world_lxi = anchor_x + lxi
                    if abs(world_lxi - world_lxj) < hwi + hwj + gap:
                        constrained = True
                        req = cy_j + lyj - lyi + hhi + hhj + gap
                        if req > min_cy:
                            min_cy = req
            if constrained:
                out.append((anchor_x, min_cy))

            # ABOVE j
            max_cy = math.inf
            constrained = False
            for (lxi, lyi, hwi, hhi) in pads_i:
                for (lxj, lyj, hwj, hhj) in pads_j:
                    world_lxj = cx_j + lxj
                    world_lxi = anchor_x + lxi
                    if abs(world_lxi - world_lxj) < hwi + hwj + gap:
                        constrained = True
                        req = cy_j + lyj - lyi - hhi - hhj - gap
                        if req < max_cy:
                            max_cy = req
            if constrained:
                out.append((anchor_x, max_cy))

            return out

        # Centre-aligned snaps (original behaviour)
        results.extend(_cardinal_snaps(cx_j, cy_j))

        # Pin-aligned snaps: for each shared net, generate candidates
        # where i's connecting pad aligns with j's connecting pad.
        shared = _shared_nets(i, j)
        seen_offsets: set[tuple[float, float]] = set()
        for net_name in shared:
            j_pad_indices = comp_pad_nets[j].get(net_name, [])
            i_pad_indices = comp_pad_nets[i].get(net_name, [])
            for jpi in j_pad_indices:
                if jpi >= len(pads_j):
                    continue
                j_lx, j_ly, _, _ = pads_j[jpi]
                j_world_x = cx_j + j_lx
                j_world_y = cy_j + j_ly
                for ipi in i_pad_indices:
                    if ipi >= len(pads_i):
                        continue
                    i_lx, i_ly, _, _ = pads_i[ipi]
                    # Position i so pad ipi lands at j's pad jpi location
                    anchor_x = j_world_x - i_lx
                    anchor_y = j_world_y - i_ly
                    key = (round(anchor_x, 3), round(anchor_y, 3))
                    if key not in seen_offsets:
                        seen_offsets.add(key)
                        results.extend(_cardinal_snaps(anchor_x, anchor_y))

        # DIAGONAL candidates (conservative: envelope-based)
        dx = env_hw[i] + env_hw[j] + gap
        dy = env_hh[i] + env_hh[j] + gap
        results += [
            (cx_j + dx, cy_j + dy), (cx_j + dx, cy_j - dy),
            (cx_j - dx, cy_j + dy), (cx_j - dx, cy_j - dy),
        ]
        return results

    # ── Utility ────────────────────────────────────────────────────────
    rng = random.Random(seed)
    cx_board = board_width_mm / 2.0
    cy_board = board_height_mm / 2.0

    # No hard clamp — board size is a soft guide.  Components can extend
    # past the board edge to keep net groups tight.

    # Sort by: connectors first → then RF-net components → then by envelope area descending.
    # RF-connected components are placed early so they cluster tightly around
    # the anchor IC before other components can occupy nearby positions.
    _rf_nets = {net for net, w in net_weight.items() if w >= _RF_WEIGHT}
    def _has_rf(i: int) -> bool:
        return any(net in _rf_nets for net in components[i].get("nets", []))
    order = sorted(
        range(n),
        key=lambda i: (
            0 if _is_connector(components[i]) else 1,    # connectors first
            0 if _has_rf(i) else 1,                       # RF-connected next
            -(env_hw[i] * env_hh[i]),                     # then by size descending
        ),
    )

    placed: list[int] = []
    placed_set: set[int] = set()

    # ── Place first (largest non-connector) at board centre ───────────
    first = next((o for o in order if not _is_connector(components[o])), order[0])
    pos[first] = [cx_board, cy_board]
    placed.append(first)
    placed_set.add(first)

    # ── Try all 4 rotations for component i, pick tightest fit ────────
    def _best_rotation_candidates(idx: int, anchor_pool: list[int],
                                  net_neighbors: dict[int, int],
                                  net_neighbors_nets: dict[int, list[str]]) -> tuple[list[tuple[float, float]], int]:
        """Try rotations 0/90/180/270°.  Return (best candidates, best rotation)."""
        orig_rot = rotations[idx]
        best_rot = orig_rot
        best_cands: list[tuple[float, float]] = []
        best_net_dist = math.inf

        for rot in (0, 90, 180, 270):
            rotations[idx] = rot
            _rebuild_pads(idx)
            cands: list[tuple[float, float]] = []
            for j in anchor_pool:
                cands.extend(_snap_positions(idx, j))
            if not cands:
                continue
            # Evaluate: pick the candidate closest to net group centroid
            # Use PIN-LEVEL anchors — the centroid of connecting pads, not
            # component centres.
            if net_neighbors:
                anchors: list[tuple[float, float, float]] = []  # (ax, ay, weight)
                for j, w in net_neighbors.items():
                    shared = net_neighbors_nets.get(j, [])
                    ax, ay = _net_anchor(j, shared)
                    anchors.append((ax, ay, float(w)))
                total_w = sum(a[2] for a in anchors)
                gcx = sum(a[0] * a[2] for a in anchors) / total_w
                gcy = sum(a[1] * a[2] for a in anchors) / total_w
                min_d = min(math.hypot(cx - gcx, cy - gcy) for cx, cy in cands)
            else:
                min_d = min(math.hypot(cx - cx_board, cy - cy_board) for cx, cy in cands)
            if min_d < best_net_dist:
                best_net_dist = min_d
                best_cands = cands
                best_rot = rot

        # Restore the winning rotation
        rotations[idx] = best_rot
        _rebuild_pads(idx)
        return best_cands, best_rot

    # ── Place each remaining component ────────────────────────────────
    for idx in order:
        if idx in placed_set:
            continue

        # Connectors → pin to top edge, spread horizontally
        if _is_connector(components[idx]):
            pos[idx] = [cx_board, env_hh[idx] + 1.0]
            for _ in range(60):
                moved = False
                for j in placed:
                    if _is_connector(components[j]) and _collides(idx, j):
                        pos[idx][0] += env_hw[idx] + env_hw[j] + gap
                        moved = True
                if not moved:
                    break
            placed.append(idx)
            placed_set.add(idx)
            continue

        # ── Find net neighbours ("same colour" in Tetris) ────────────
        net_neighbors: dict[int, int] = {}
        net_neighbors_nets: dict[int, list[str]] = {}  # j → shared net names
        for j in placed:
            s = _shared(idx, j)
            if s > 0:
                net_neighbors[j] = s
                net_neighbors_nets[j] = _shared_nets(idx, j)

        anchor_pool = list(net_neighbors.keys()) if net_neighbors else placed[:1]

        # ── Try all rotations, pick tightest ──────────────────────────
        candidates, _best_rot = _best_rotation_candidates(idx, anchor_pool, net_neighbors, net_neighbors_nets)

        if not candidates:
            cx_p = sum(pos[j][0] for j in placed) / len(placed)
            cy_p = sum(pos[j][1] for j in placed) / len(placed)
            candidates = [(cx_p + env_hw[idx] + env_hw[placed[0]] + gap, cy_p)]

        # ── Score: fewest pad collisions, then closest to net pins ─────
        best_pos = None
        best_score: tuple[float, float] = (math.inf, math.inf)

        for cx_c, cy_c in candidates:
            pos[idx] = [cx_c, cy_c]

            violations = sum(1 for j in placed if _collides(idx, j))
            # Distance to pin-level anchors (connecting pads), not centres
            net_dist = 0.0
            if net_neighbors:
                for j, w in net_neighbors.items():
                    shared = net_neighbors_nets.get(j, [])
                    ax, ay = _net_anchor(j, shared)
                    net_dist += math.hypot(pos[idx][0] - ax, pos[idx][1] - ay) * w

            score = (violations, net_dist)
            if score < best_score:
                best_score = score
                best_pos = [pos[idx][0], pos[idx][1]]

        pos[idx] = best_pos or [candidates[0][0], candidates[0][1]]

        # ── Legalise: re-snap to clear worst violator ────────────────
        for _leg in range(80):
            worst_j = None
            worst_sev = 0
            for j in placed:
                if not _collides(idx, j):
                    continue
                sev = 0
                xi, yi = pos[idx]
                xj, yj = pos[j]
                for (lxi, lyi, hwi, hhi) in comp_pads[idx]:
                    for (lxj, lyj, hwj, hhj) in comp_pads[j]:
                        if (abs(xi + lxi - xj - lxj) < hwi + hwj + gap and
                                abs(yi + lyi - yj - lyj) < hhi + hhj + gap):
                            sev += 1
                if sev > worst_sev:
                    worst_sev = sev
                    worst_j = j
            if worst_j is None:
                break

            snaps = _snap_positions(idx, worst_j)
            if not snaps:
                ddx = pos[idx][0] - pos[worst_j][0]
                ddy = pos[idx][1] - pos[worst_j][1]
                if abs(ddx) < 1e-6 and abs(ddy) < 1e-6:
                    ddx, ddy = rng.choice([-1, 1]), 0.0
                px = (env_hw[idx] + env_hw[worst_j] + gap) - abs(ddx)
                py = (env_hh[idx] + env_hh[worst_j] + gap) - abs(ddy)
                if px <= py:
                    pos[idx][0] += math.copysign(max(px, 0) + 1e-3, ddx)
                else:
                    pos[idx][1] += math.copysign(max(py, 0) + 1e-3, ddy)
                continue

            cur_x, cur_y = pos[idx]
            cur_violations = sum(1 for k in placed if _collides(idx, k))
            best_snap = None
            best_snap_score: tuple[float, float] = (cur_violations, 0.0)
            for sx, sy in snaps:
                pos[idx] = [sx, sy]
                v = sum(1 for k in placed if _collides(idx, k))
                d = math.hypot(pos[idx][0] - cur_x, pos[idx][1] - cur_y)
                if (v, d) < best_snap_score:
                    best_snap_score = (v, d)
                    best_snap = [pos[idx][0], pos[idx][1]]
            if best_snap:
                pos[idx] = best_snap
            else:
                pos[idx] = [cur_x, cur_y]

        placed.append(idx)
        placed_set.add(idx)

    # ── Global final pass: clear ALL remaining pad collisions ─────────
    for _gpass in range(60):
        changed = False
        for i in range(n):
            for j in range(i + 1, n):
                if not _collides(i, j):
                    continue
                changed = True
                ddx = pos[i][0] - pos[j][0]
                ddy = pos[i][1] - pos[j][1]
                if abs(ddx) < 1e-6 and abs(ddy) < 1e-6:
                    ddx = rng.choice([-1.0, 1.0])
                    ddy = 0.0
                push_x = (env_hw[i] + env_hw[j] + gap) - abs(ddx)
                push_y = (env_hh[i] + env_hh[j] + gap) - abs(ddy)
                EPS = 2e-3
                if 0 < push_x <= push_y:
                    half = push_x / 2.0 + EPS
                    sign = 1.0 if ddx >= 0 else -1.0
                    if _is_connector(components[i]):
                        pos[j][0] -= sign * half * 2
                    elif _is_connector(components[j]):
                        pos[i][0] += sign * half * 2
                    else:
                        pos[i][0] += sign * half
                        pos[j][0] -= sign * half
                elif push_y > 0:
                    half = push_y / 2.0 + EPS
                    sign = 1.0 if ddy >= 0 else -1.0
                    if _is_connector(components[i]):
                        pos[j][1] -= sign * half * 2
                    elif _is_connector(components[j]):
                        pos[i][1] += sign * half * 2
                    else:
                        pos[i][1] += sign * half
                        pos[j][1] -= sign * half
                else:
                    ex = env_hw[i] + env_hw[j] + gap
                    ey = env_hh[i] + env_hh[j] + gap
                    if abs(ddx) <= abs(ddy):
                        sign = 1.0 if ddx >= 0 else -1.0
                        pos[i][0] += sign * (ex / 2 + EPS)
                        pos[j][0] -= sign * (ex / 2 + EPS)
                    else:
                        sign = 1.0 if ddy >= 0 else -1.0
                        pos[i][1] += sign * (ey / 2 + EPS)
                        pos[j][1] -= sign * (ey / 2 + EPS)
        if not changed:
            break

    # ── Net-pull compaction: slide each component toward net neighbours ──
    # After push-apart, components may have drifted far from their net group.
    # For each component, compute the pin-level centroid of its net neighbours
    # and try to move it closer without causing collisions.
    for _pull_pass in range(40):
        any_moved = False
        pull_order = list(range(n))
        rng.shuffle(pull_order)
        for i in pull_order:
            if _is_connector(components[i]):
                continue
            # Compute target: weighted centroid of net-neighbour connecting pads
            # Signal nets pull much harder than GND/power nets
            anchors: list[tuple[float, float, float]] = []
            for j in range(n):
                if j == i:
                    continue
                shared = _shared_nets(i, j)
                if shared:
                    ax, ay = _net_anchor(j, shared)
                    w = sum(net_weight.get(sn, 1.0) for sn in shared)
                    anchors.append((ax, ay, w))
            if not anchors:
                continue
            total_w = sum(a[2] for a in anchors)
            tx = sum(a[0] * a[2] for a in anchors) / total_w
            ty = sum(a[1] * a[2] for a in anchors) / total_w
            # Direction vector toward target
            dx_pull = tx - pos[i][0]
            dy_pull = ty - pos[i][1]
            dist = math.hypot(dx_pull, dy_pull)
            if dist < 0.1:
                continue
            # Try stepping toward target (binary search for max step)
            for frac in (1.0, 0.5, 0.25, 0.12):
                nx = pos[i][0] + dx_pull * frac
                ny = pos[i][1] + dy_pull * frac
                old_x, old_y = pos[i][0], pos[i][1]
                pos[i] = [nx, ny]
                if not any(_collides(i, j) for j in range(n) if j != i):
                    any_moved = True
                    break
                pos[i] = [old_x, old_y]
        if not any_moved:
            break

    # ── Re-centre cluster on the board ────────────────────────────────
    # The algorithm ignores board boundaries for tight packing.  Now shift
    # the entire cluster so its centroid sits at the board centre.
    avg_x = sum(p[0] for p in pos) / n
    avg_y = sum(p[1] for p in pos) / n
    dx_shift = cx_board - avg_x
    dy_shift = cy_board - avg_y
    for i in range(n):
        pos[i][0] += dx_shift
        pos[i][1] += dy_shift

    # ── Build output ──────────────────────────────────────────────────────
    result: list[dict[str, Any]] = []
    for i, comp in enumerate(components):
        # Find best net neighbour for rationale
        best_j, best_s = None, -1
        for j in range(n):
            if j == i:
                continue
            s = _shared(i, j)
            if s > best_s:
                best_s, best_j = s, j
        neighbour_ref = components[best_j]["reference"] if best_j is not None else "board"
        rationale = (
            f"{comp.get('reference','?')} placed adjacent to {neighbour_ref} "
            f"({best_s} shared net{'s' if best_s != 1 else ''}); "
            f"gap={min_clearance_mm}mm."
        )
        result.append({
            "reference": comp.get("reference", "?"),
            "x": round(pos[i][0], 3),
            "y": round(pos[i][1], 3),
            "rotation": rotations[i],
            "layer": comp.get("layer", "F.Cu"),
            "footprint": comp.get("footprint", ""),
            "rationale": rationale,
        })
    return result


def compute_net_proximity_placement(
    components: list[dict[str, Any]],
    board_width_mm: float = 100.0,
    board_height_mm: float = 80.0,
    min_clearance_mm: float = MIN_CLEARANCE_MM,
    iterations: int = 400,
    seed: int = 42,
    schematic_hints: dict[str, tuple[float, float]] | None = None,
    hint_weight: float = 0.4,
) -> list[dict[str, Any]]:
    """
    Compute PCB component placements optimised for net-proximity.

    Algorithm
    ---------
    1. **Initial grid**: spread components evenly across the board.
    2. **Connector pinning**: connectors are pinned to the top board edge and
       held there throughout — they do not participate in attraction pulls.
    3. **Net-attraction** (iterative, with cooling):
       For every non-connector component, compute the weighted centroid of all
       co-net components across every net it participates in.  Pull the
       component toward that centroid by a damped step that shrinks as
       iterations progress.
    4. **Courtyard repulsion** (multiple micro-passes per iteration):
       For every pair of components that violates the package-to-package gap,
       push them apart symmetrically until they are exactly at the legal
       minimum distance.  Connectors are only pushed horizontally (they stay
       on the edge).
    5. **Board-boundary clamping**: all centroids are kept inside the board
       with a 1 mm margin.

    Parameters
    ----------
    components:
        List of component dicts from ``get_placement_context()`` —
        each must have ``reference``, ``footprint``, and ``nets`` (list[str]).
    board_width_mm, board_height_mm:
        PCB board dimensions in mm.
    min_clearance_mm:
        Package-to-package minimum gap (package gap).  Default 1.0 mm.
    iterations:
        Number of attraction+repulsion cycles.  More iterations → tighter
        packing but longer runtime.
    seed:
        RNG seed for reproducible jitter in the initial grid.
    schematic_hints:
        Optional dict mapping reference → (x_mm, y_mm) of pre-normalised
        schematic positions in board-mm space.  When provided these are used
        as initial positions (instead of a uniform grid), so the final layout
        mirrors the schematic topology.  Connectors are still pinned to the
        board edge regardless of their hint.
    hint_weight:
        Multiplier (0..1) for the hint-gravity force.  Default 0.4 is gentle
        (for normalised schematic hints).  Use 0.8–1.0 for existing board
        positions that should be preserved as closely as possible.

    Returns
    -------
    List of placement dicts — one per component — ready for ``apply_placement()``:
        [{reference, x, y, rotation, layer, footprint, rationale}, ...]
    """
    n = len(components)
    if n == 0:
        return []

    rng = random.Random(seed)

    # ------------------------------------------------------------------ #
    # 1. Build net → component-index map                                   #
    # ------------------------------------------------------------------ #
    net_to_idxs: dict[str, list[int]] = {}
    for i, comp in enumerate(components):
        for net in comp.get("nets", []):
            net_to_idxs.setdefault(net, []).append(i)

    # ------------------------------------------------------------------ #
    # 2. Estimate silkscreen half-extents for each component               #
    #    Silkscreen = package body outline.  min_clearance_mm is the gap  #
    #    that must be kept between any two silkscreen outlines.            #
    # ------------------------------------------------------------------ #
    sizes = [_estimate_footprint_size(c.get("footprint", "")) for c in components]
    # half-extents = silkscreen boundary = package body / 2
    hw = [s[0] / 2.0 for s in sizes]  # silkscreen half-width
    hh = [s[1] / 2.0 for s in sizes]  # silkscreen half-height

    # ------------------------------------------------------------------ #
    # 3. Classify components                                               #
    # ------------------------------------------------------------------ #
    is_conn = [_is_connector(c) for c in components]
    is_ic   = [_is_ic(c) for c in components]
    is_pwr  = [_is_power(c) for c in components]

    # ------------------------------------------------------------------ #
    # 4. Initial positions                                                  #
    #    If schematic_hints supplied: use normalised schematic coords.     #
    #    Otherwise fall back to a uniform grid with small random jitter.   #
    # ------------------------------------------------------------------ #
    bx_center = board_width_mm  / 2.0
    by_center = board_height_mm / 2.0

    pos: list[list[float]] = []

    if schematic_hints:
        # Place each component at its hint, clamped to board bounds.
        for comp in components:
            ref = comp.get("reference", "?")
            if ref in schematic_hints:
                hx, hy = schematic_hints[ref]
            else:
                # No hint: place at board centre with small jitter
                hx = bx_center + rng.uniform(-2, 2)
                hy = by_center + rng.uniform(-2, 2)
            pos.append([float(hx), float(hy)])
    else:
        cols = max(1, int(math.ceil(math.sqrt(n))))
        rows = max(1, math.ceil(n / cols))
        step_x = board_width_mm  / (cols + 1)
        step_y = board_height_mm / (rows + 1)
        for i in range(n):
            col = i % cols
            row = i // cols
            x = step_x * (col + 1) + rng.uniform(-0.5, 0.5)
            y = step_y * (row + 1) + rng.uniform(-0.5, 0.5)
            pos.append([x, y])

    # ------------------------------------------------------------------ #
    # 5. Pin connectors to the top board edge                              #
    # ------------------------------------------------------------------ #
    EDGE_MARGIN = 2.0  # mm from board edge
    for i in range(n):
        if is_conn[i]:
            pos[i][1] = hh[i] + EDGE_MARGIN

    # ------------------------------------------------------------------ #
    # 6. Iterative optimisation  (Fruchterman-Reingold style)              #
    #                                                                      #
    # Each iteration accumulates two forces per component, then applies    #
    # them simultaneously, then enforces silkscreen hard constraints.      #
    #                                                                      #
    # Force A — NET ATTRACTION (spring toward co-net centroid)             #
    #   Pulls each component toward the centroid of every component it     #
    #   shares a net with.  Weighted by number of shared connections.      #
    #   Step capped by cooling schedule to avoid oscillation.              #
    #                                                                      #
    # Force B — GLOBAL RADIAL REPULSION (1/r²)                            #
    #   Applied between EVERY pair of components regardless of nets.       #
    #   This is what prevents all components collapsing onto a single      #
    #   line — the radial direction spreads them in 2D naturally.          #
    #   Strength decreases with the cooling schedule.                      #
    #                                                                      #
    # Hard constraint — SILKSCREEN CLEARANCE                               #
    #   After forces are applied, any pair closer than min_clearance_mm    #
    #   (silkscreen-to-silkscreen) is pushed apart until exactly legal.    #
    # ------------------------------------------------------------------ #
    SILKSCREEN_PASSES = 20  # hard-constraint enforcement passes per iter

    for iteration in range(iterations):
        alpha = 1.0 - (iteration / iterations)        # 1 → 0 as we cool
        max_step  = 5.0 * alpha + 0.2                 # attraction cap (mm)
        # Repulsion constant: kept very small — its only job is to break
        # exact symmetry/degeneracy (prevent two components landing on the
        # same point).  Package gap enforcement (the hard constraint
        # below) handles all actual spacing; a large repulsion constant is
        # the primary cause of components ending up far apart.
        # Formula: k²/d where k = sqrt(area/n)*0.03 → negligible at d > ~2 mm.
        _k = math.sqrt(board_width_mm * board_height_mm / max(n, 1)) * 0.03
        k_repulse = _k * _k * alpha

        forces = [[0.0, 0.0] for _ in range(n)]

        # ---- Force A: net-attraction ---------------------------------- #
        # When hint_weight is high (e.g. 0.85 for board-position anchors),
        # net-attraction is scaled down by (1 - hint_weight) so hints
        # dominate.  hint_weight=0 → full net force, hint_weight=1 → no net.
        net_scale = 1.0 - (hint_weight if schematic_hints else 0.0)
        for i, comp in enumerate(components):
            if is_conn[i]:
                continue
            nets = comp.get("nets", [])
            if not nets:
                continue

            cx_sum = cy_sum = weight = 0.0
            for net in nets:
                for j in net_to_idxs.get(net, []):
                    if j == i:
                        continue
                    cx_sum += pos[j][0]
                    cy_sum += pos[j][1]
                    weight += 1.0

            if weight == 0:
                continue

            dx = (cx_sum / weight) - pos[i][0]
            dy = (cy_sum / weight) - pos[i][1]
            dist = math.hypot(dx, dy)
            if dist > 1e-6:
                pull = min(dist, max_step) * net_scale
                forces[i][0] += (dx / dist) * pull
                forces[i][1] += (dy / dist) * pull

        # ---- Force A2: IC centre-bias --------------------------------- #
        # ICs are pulled toward the board centre so they sit in the middle
        # surrounded by their passives.  Strength is fixed (not just a fraction
        # of the cooling max_step) so it stays effective as the algorithm cools.
        ic_pull_strength = min(board_width_mm, board_height_mm) * 0.08
        for i in range(n):
            if is_conn[i] or not is_ic[i]:
                continue
            dx = bx_center - pos[i][0]
            dy = by_center - pos[i][1]
            dist = math.hypot(dx, dy)
            if dist > 1e-6:
                pull = min(dist, ic_pull_strength)
                forces[i][0] += (dx / dist) * pull
                forces[i][1] += (dy / dist) * pull

        # ---- Force A3: schematic hint gravity ------------------------- #
        # Each component that has a schematic hint gets a continuous pull
        # toward that hint position throughout the algorithm.  This biases
        # the final layout to mirror the schematic topology.  hint_weight
        # controls the strength: 0.4 for schematic hints, 0.8+ for board
        # position anchors.  The force does NOT fully decay with cooling —
        # a floor of 30 % of hint_weight is maintained so positions stay
        # anchored even at late iterations.
        if schematic_hints:
            effective_hw = hint_weight * max(alpha, 0.3)
            hint_strength = max_step * effective_hw
            for i, comp in enumerate(components):
                if is_conn[i]:
                    continue
                ref = comp.get("reference", "")
                if ref not in schematic_hints:
                    continue
                hx, hy = schematic_hints[ref]
                dx = hx - pos[i][0]
                dy = hy - pos[i][1]
                dist = math.hypot(dx, dy)
                if dist > 1e-6:
                    pull = min(dist, hint_strength)
                    forces[i][0] += (dx / dist) * pull
                    forces[i][1] += (dy / dist) * pull

        # ---- Force B: global radial repulsion ------------------------- #
        for i in range(n):
            for j in range(i + 1, n):
                ddx = pos[i][0] - pos[j][0]
                ddy = pos[i][1] - pos[j][1]
                dist = math.hypot(ddx, ddy)

                if dist < 1e-4:
                    # Degenerate — add random nudge to break symmetry
                    angle = rng.uniform(0, 2 * math.pi)
                    ddx, ddy = math.cos(angle), math.sin(angle)
                    dist = 1e-4

                # Repulsion force magnitude (Fruchterman-Reingold: k²/d)
                rep = min(k_repulse / dist, max_step)
                nx, ny = ddx / dist, ddy / dist
                forces[i][0] += nx * rep
                forces[i][1] += ny * rep
                forces[j][0] -= nx * rep
                forces[j][1] -= ny * rep

        # ---- Apply accumulated forces --------------------------------- #
        for i in range(n):
            if is_conn[i]:
                continue
            fx, fy = forces[i]
            fmag = math.hypot(fx, fy)
            if fmag > max_step:
                fx = (fx / fmag) * max_step
                fy = (fy / fmag) * max_step
            pos[i][0] += fx
            pos[i][1] += fy

        # ---- Hard constraint: package gap --------------------- #
        for _ in range(SILKSCREEN_PASSES):
            for i in range(n):
                for j in range(i + 1, n):
                    ddx = pos[j][0] - pos[i][0]
                    ddy = pos[j][1] - pos[i][1]
                    dist = math.hypot(ddx, ddy)

                    if dist < 1e-4:
                        angle = rng.uniform(0, 2 * math.pi)
                        ddx, ddy = math.cos(angle), math.sin(angle)
                        dist = 1e-4

                    # AABB minimum centre-to-centre distance in direction (ddx,ddy)
                    nx, ny = ddx / dist, ddy / dist
                    # Conservative bound: use the larger half-extent on each axis
                    if abs(nx) > 1e-9:
                        t_x = (hw[i] + hw[j] + min_clearance_mm) / abs(nx)
                    else:
                        t_x = float("inf")
                    if abs(ny) > 1e-9:
                        t_y = (hh[i] + hh[j] + min_clearance_mm) / abs(ny)
                    else:
                        t_y = float("inf")
                    min_dist = min(t_x, t_y)  # AABB touch distance in this dir

                    if dist >= min_dist:
                        continue  # already clear

                    push = (min_dist - dist) / 2.0 + 1e-4
                    if is_conn[i]:
                        pos[j][0] += nx * push * 2
                        pos[j][1] += ny * push * 2
                    elif is_conn[j]:
                        pos[i][0] -= nx * push * 2
                        pos[i][1] -= ny * push * 2
                    else:
                        pos[i][0] -= nx * push
                        pos[i][1] -= ny * push
                        pos[j][0] += nx * push
                        pos[j][1] += ny * push

        # ---- Boundary clamp + re-pin connectors ----------------------- #
        for i in range(n):
            pos[i][0] = max(hw[i] + 1.0, min(board_width_mm  - hw[i] - 1.0, pos[i][0]))
            pos[i][1] = max(hh[i] + 1.0, min(board_height_mm - hh[i] - 1.0, pos[i][1]))
            if is_conn[i]:
                pos[i][1] = hh[i] + EDGE_MARGIN

    # ------------------------------------------------------------------ #
    # 7. Final strict-clearance enforcement pass                           #
    #    Run extra repulsion sweeps (no attraction) to guarantee every     #
    #    pair satisfies exactly >= min_clearance_mm before we output.      #
    # ------------------------------------------------------------------ #
    FINAL_PASSES = 30
    for _ in range(FINAL_PASSES):
        changed = False
        for i in range(n):
            for j in range(i + 1, n):
                xi, yi = pos[i]
                xj, yj = pos[j]
                req_sep_x = hw[i] + hw[j] + min_clearance_mm
                req_sep_y = hh[i] + hh[j] + min_clearance_mm
                ddx = xj - xi
                ddy = yj - yi
                gap_x = abs(ddx) - (hw[i] + hw[j])
                gap_y = abs(ddy) - (hh[i] + hh[j])
                effective_gap = max(gap_x, gap_y)
                if effective_gap >= min_clearance_mm:
                    continue
                changed = True
                push_along_x = req_sep_x - abs(ddx)
                push_along_y = req_sep_y - abs(ddy)
                # Use a 2 µm safety epsilon to defeat floating-point drift
                _EPS = 2e-3
                if push_along_x <= push_along_y:
                    half = max(push_along_x, 0.0) / 2.0 + _EPS
                    sign = 1 if ddx >= 0 else -1
                    if is_conn[i]:
                        pos[j][0] += sign * half * 2
                    elif is_conn[j]:
                        pos[i][0] -= sign * half * 2
                    else:
                        pos[i][0] -= sign * half
                        pos[j][0] += sign * half
                else:
                    half = max(push_along_y, 0.0) / 2.0 + _EPS
                    sign = 1 if ddy >= 0 else -1
                    if is_conn[i]:
                        pos[j][1] += sign * half * 2
                    elif is_conn[j]:
                        pos[i][1] -= sign * half * 2
                    else:
                        pos[i][1] -= sign * half
                        pos[j][1] += sign * half
        # Clamp + re-pin after each final pass
        for i in range(n):
            pos[i][0] = max(hw[i] + 1.0, min(board_width_mm  - hw[i] - 1.0, pos[i][0]))
            pos[i][1] = max(hh[i] + 1.0, min(board_height_mm - hh[i] - 1.0, pos[i][1]))
            if is_conn[i]:
                pos[i][1] = hh[i] + EDGE_MARGIN
        if not changed:
            break  # converged early

    # ------------------------------------------------------------------ #
    # 8. Build output list with per-component rationale                    #
    # ------------------------------------------------------------------ #
    result: list[dict[str, Any]] = []
    for i, comp in enumerate(components):
        ref = comp.get("reference", "?")
        nets = comp.get("nets", [])

        # Find the dominant net (most co-net neighbours) for the rationale
        dominant_net: str | None = None
        dominant_neighbours: list[str] = []
        best_count = 0
        for net in nets:
            neighbours = [components[j]["reference"]
                          for j in net_to_idxs.get(net, []) if j != i]
            if len(neighbours) > best_count:
                best_count = len(neighbours)
                dominant_net = net
                dominant_neighbours = neighbours

        if is_conn[i]:
            rationale = (
                f"{ref} is a connector — pinned to board top edge "
                f"(y={pos[i][1]:.2f} mm) per placement rule #1."
            )
        elif dominant_net and dominant_neighbours:
            nbr_str = ", ".join(dominant_neighbours[:4])
            if len(dominant_neighbours) > 4:
                nbr_str += f" (+{len(dominant_neighbours)-4} more)"
            rationale = (
                f"Net-proximity pull toward {nbr_str} via dominant net "
                f"'{dominant_net}' ({best_count} shared connection"
                f"{'s' if best_count != 1 else ''}). "
                f"Final position ({pos[i][0]:.2f}, {pos[i][1]:.2f}) mm satisfies "
                f"{min_clearance_mm} mm silkscreen-to-silkscreen clearance."
            )
        else:
            rationale = (
                f"{ref} has no net connections; placed at "
                f"({pos[i][0]:.2f}, {pos[i][1]:.2f}) mm in available space."
            )

        result.append({
            "reference": ref,
            "x": round(pos[i][0], 3),
            "y": round(pos[i][1], 3),
            "rotation": comp.get("rotation", 0),   # from schematic; 0 if not specified
            "layer": "F.Cu",
            "footprint": comp.get("footprint", ""),
            "rationale": rationale,
        })

    return result
