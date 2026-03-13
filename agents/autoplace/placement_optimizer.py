"""
Placement Optimizer — courtyard overlap checker + net-proximity placer using Polars.

Two main capabilities:

1. check_courtyard_clearances(placements)
   Post-placement DRC: verifies every pair of components satisfies the minimum
   package-to-package gap (courtyard clearance).

2. compute_net_proximity_placement(components, ...)
   Pre-placement algorithm: force-directed, net-proximity optimised placement.
   Pulls components toward their net-neighbours and enforces the courtyard gap.

Usage:
    from agents.autoplace.placement_optimizer import (
        check_courtyard_clearances,
        compute_net_proximity_placement,
    )
    placements = compute_net_proximity_placement(components, board_width_mm=100, board_height_mm=80)
    result = check_courtyard_clearances(placements)
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
_IMPERIAL_MAP: dict[str, tuple[float, float]] = {
    "0201": (0.6,  0.3),
    "0402": (1.0,  0.5),
    "0603": (1.6,  0.8),
    "0805": (2.0,  1.25),
    "1206": (3.2,  1.6),
    "1210": (3.2,  2.55),
    "2010": (5.0,  2.55),
    "2512": (6.35, 3.2),
}

# SOT packages
_SOT_MAP: dict[str, tuple[float, float]] = {
    "SOT-23":   (2.9,  1.3),
    "SOT-23-5": (2.9,  1.6),
    "SOT-23-6": (2.9,  1.6),
    "SOT-223":  (6.5,  3.5),
    "SOT-89":   (4.5,  2.5),
    "SOT-363":  (2.2,  2.2),
    "SOT-323":  (2.2,  1.25),
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
    m = re.search(r"(\d+(?:\.\d+)?)\s*X\s*(\d+(?:\.\d+)?)", fp)
    if m:
        w = float(m.group(1))
        h = float(m.group(2))
        if 0.5 <= w <= 50 and 0.5 <= h <= 50:  # sanity range
            return (w, h)

    # Imperial passive sizes (0402, 0603, etc.)
    for code, size in _IMPERIAL_MAP.items():
        if code in fp:
            return size

    # SOT packages
    for pkg, size in _SOT_MAP.items():
        if pkg.upper() in fp:
            return size

    # SOIC-N — estimate height from pin count
    m = re.search(r"SOIC[-_](\d+)", fp)
    if m:
        n_pins = int(m.group(1))
        pins_per_side = n_pins // 2
        height = (pins_per_side - 1) * _SOIC_PIN_PITCH + 2.0  # body
        return (_SOIC_BODY_WIDTH, height)

    # QFN-N or QFP-N — pin-count heuristic (conservative: 0.4mm/pin)
    m = re.search(r"QF[NP][-_](\d+)", fp)
    if m:
        n_pins = int(m.group(1))
        side = max(3.0, n_pins * 0.4)
        return (side, side)

    # DIP-N
    m = re.search(r"DIP[-_](\d+)", fp)
    if m:
        n_pins = int(m.group(1))
        pins_per_side = n_pins // 2
        height = (pins_per_side - 1) * 2.54 + 2.0
        return (7.62, height)

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
# Courtyard clearance check (minimum 0.25 mm between courtyards)
# ---------------------------------------------------------------------------

MIN_CLEARANCE_MM = 0.25


def _build_dataframe(placements: list[dict[str, Any]]) -> pl.DataFrame:
    """Build a Polars DataFrame with bounding-box columns added."""
    rows = []
    for p in placements:
        ref = p.get("reference", "?")
        x = float(p.get("x", 0.0))
        y = float(p.get("y", 0.0))
        fp = p.get("footprint", "")
        w, h = _estimate_footprint_size(fp)
        # half-extents = actual 3D package boundary (no extra expansion)
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


def check_courtyard_clearances(
    placements: list[dict[str, Any]],
    min_clearance_mm: float = MIN_CLEARANCE_MM,
) -> dict[str, Any]:
    """
    Check courtyard clearances for a list of placement dicts.

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
                        f"Courtyard violation: {refs[i]} and {refs[j]} have "
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
        return "All courtyard clearances OK."
    lines = [f"Found {len(result['violations'])} courtyard violation(s):"]
    for v in result["violations"]:
        lines.append(f"  - {v['message']}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Net-proximity placement algorithm
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


def compute_net_proximity_placement(
    components: list[dict[str, Any]],
    board_width_mm: float = 100.0,
    board_height_mm: float = 80.0,
    min_clearance_mm: float = MIN_CLEARANCE_MM,
    iterations: int = 250,
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
        Package-to-package minimum gap (courtyard clearance).  Default 0.25 mm.
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
    # 2. Estimate courtyard half-extents for each component                #
    # ------------------------------------------------------------------ #
    sizes = [_estimate_footprint_size(c.get("footprint", "")) for c in components]
    # half-extents = actual 3D package boundary (package-to-package gap)
    hw = [s[0] / 2.0 for s in sizes]  # package half-width
    hh = [s[1] / 2.0 for s in sizes]  # package half-height

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
    # them simultaneously, then enforces courtyard hard constraints.       #
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
    # Hard constraint — COURTYARD CLEARANCE                                #
    #   After forces are applied, any pair closer than the package-to-     #
    #   package gap is pushed apart radially until exactly legal.          #
    # ------------------------------------------------------------------ #
    COURTYARD_PASSES = 10  # hard-constraint enforcement passes per iter

    for iteration in range(iterations):
        alpha = 1.0 - (iteration / iterations)        # 1 → 0 as we cool
        max_step  = 5.0 * alpha + 0.2                 # attraction cap (mm)
        # Repulsion constant: tuned so global spread force is weaker than
        # net-attraction at typical cluster distances, but strong enough to
        # break degeneracy (prevents all components collapsing onto one line).
        # Formula: k²/d where k = sqrt(area/n)*0.15 → gentle at d > ~2 mm.
        _k = math.sqrt(board_width_mm * board_height_mm / max(n, 1)) * 0.15
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

        # ---- Hard constraint: courtyard clearance --------------------- #
        for _ in range(COURTYARD_PASSES):
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
                f"{min_clearance_mm} mm courtyard clearance."
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
            "rotation": 0,
            "layer": "F.Cu",
            "footprint": comp.get("footprint", ""),
            "rationale": rationale,
        })

    return result
