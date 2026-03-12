"""
Placement Optimizer — post-LLM courtyard overlap checker using Polars.

Takes a list of placement dicts (each must have reference, x, y, footprint fields)
and checks for courtyard clearances based on estimated bounding boxes derived
from the footprint name.  Returns a violation report.

Usage:
    from agents.autoplace.placement_optimizer import check_courtyard_clearances
    result = check_courtyard_clearances(placements)
    # result = {"violations": [...], "is_valid": bool}
"""
from __future__ import annotations

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
