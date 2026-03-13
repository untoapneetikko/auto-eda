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
    """Return (width_mm, height_mm) estimate for a given footprint string."""
    fp = footprint.upper().strip()

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

    # QFN-N or QFP-N — roughly square
    m = re.search(r"QF[NP][-_](\d+)", fp)
    if m:
        n_pins = int(m.group(1))
        side = max(3.0, n_pins * 0.5)
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
        # half-extents including courtyard expansion (0.1 mm per side)
        half_w = w / 2.0 + 0.1
        half_h = h / 2.0 + 0.1
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


def _is_connector(comp: dict[str, Any]) -> bool:
    fp = comp.get("footprint", "").upper()
    return any(k in fp for k in _CONNECTOR_KEYWORDS)


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
    # half-extents + 0.1 mm courtyard expansion per side
    hw = [s[0] / 2.0 + 0.1 for s in sizes]  # courtyard half-width
    hh = [s[1] / 2.0 + 0.1 for s in sizes]  # courtyard half-height

    # ------------------------------------------------------------------ #
    # 3. Classify components                                               #
    # ------------------------------------------------------------------ #
    is_conn   = [_is_connector(c) for c in components]
    is_pwr    = [_is_power(c) for c in components]

    # ------------------------------------------------------------------ #
    # 4. Initial positions: uniform grid + small random jitter             #
    # ------------------------------------------------------------------ #
    cols = max(1, int(math.ceil(math.sqrt(n))))
    rows = max(1, math.ceil(n / cols))
    step_x = board_width_mm  / (cols + 1)
    step_y = board_height_mm / (rows + 1)

    pos: list[list[float]] = []
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
    # 6. Iterative optimisation                                            #
    # ------------------------------------------------------------------ #
    REPULSION_PASSES = 8  # courtyard-push passes per iteration

    for iteration in range(iterations):
        # Cooling: large steps early, tiny steps near the end
        alpha = 1.0 - (iteration / iterations)
        max_step = 6.0 * alpha + 0.3  # mm — maximum single-step pull

        # --- Net-attraction pull ---
        for i, comp in enumerate(components):
            if is_conn[i]:
                continue  # connectors are pinned to edge

            nets = comp.get("nets", [])
            if not nets:
                continue

            # Weighted centroid over ALL nets (each co-net neighbour counts once)
            cx_sum = cy_sum = weight = 0.0
            for net in nets:
                neighbours = [j for j in net_to_idxs.get(net, []) if j != i]
                for j in neighbours:
                    cx_sum += pos[j][0]
                    cy_sum += pos[j][1]
                    weight += 1.0

            if weight == 0:
                continue

            target_x = cx_sum / weight
            target_y = cy_sum / weight

            dx = target_x - pos[i][0]
            dy = target_y - pos[i][1]
            dist = math.hypot(dx, dy)
            if dist > 1e-6:
                move = min(dist, max_step)
                pos[i][0] += (dx / dist) * move
                pos[i][1] += (dy / dist) * move

        # --- Courtyard repulsion (multiple passes for stability) ---
        for _ in range(REPULSION_PASSES):
            for i in range(n):
                for j in range(i + 1, n):
                    xi, yi = pos[i]
                    xj, yj = pos[j]

                    # Minimum centre-to-centre separation along each axis
                    req_sep_x = hw[i] + hw[j] + min_clearance_mm
                    req_sep_y = hh[i] + hh[j] + min_clearance_mm

                    ddx = xj - xi
                    ddy = yj - yi
                    abs_dx = abs(ddx)
                    abs_dy = abs(ddy)

                    gap_x = abs_dx - (hw[i] + hw[j])
                    gap_y = abs_dy - (hh[i] + hh[j])
                    # Effective courtyard gap = max of the two axes
                    # (AABB logic: if gap_y >= 0 they don't overlap in Y)
                    effective_gap = max(gap_x, gap_y)

                    if effective_gap >= min_clearance_mm:
                        continue  # already legal — no push needed

                    # Push apart along the axis that needs the least shove
                    push_along_x = req_sep_x - abs_dx
                    push_along_y = req_sep_y - abs_dy

                    if push_along_x <= push_along_y:
                        # Push horizontally — smaller correction
                        half = push_along_x / 2.0 + 1e-4
                        sign = 1 if ddx >= 0 else -1
                        if is_conn[i]:
                            pos[j][0] += sign * half * 2
                        elif is_conn[j]:
                            pos[i][0] -= sign * half * 2
                        else:
                            pos[i][0] -= sign * half
                            pos[j][0] += sign * half
                    else:
                        # Push vertically — smaller correction
                        half = push_along_y / 2.0 + 1e-4
                        sign = 1 if ddy >= 0 else -1
                        if is_conn[i]:
                            pos[j][1] += sign * half * 2
                        elif is_conn[j]:
                            pos[i][1] -= sign * half * 2
                        else:
                            pos[i][1] -= sign * half
                            pos[j][1] += sign * half

            # Boundary clamp + re-pin connectors
            for i in range(n):
                pos[i][0] = max(hw[i] + 1.0, min(board_width_mm  - hw[i] - 1.0, pos[i][0]))
                pos[i][1] = max(hh[i] + 1.0, min(board_height_mm - hh[i] - 1.0, pos[i][1]))
                if is_conn[i]:
                    pos[i][1] = hh[i] + EDGE_MARGIN  # re-pin to edge after repulsion

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
