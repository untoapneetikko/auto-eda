"""
kicad_sym_writer.py — Converts component.json symbol data into a KiCad 7 .kicad_sym S-expression file.
"""
from __future__ import annotations

from typing import Any


# KiCad pin type mapping from component schema types to KiCad electrical types
PIN_TYPE_MAP = {
    "power": "power_in",
    "input": "input",
    "output": "output",
    "bidirectional": "bidirectional",
    "passive": "passive",
    "nc": "no_connect",
}

# KiCad pin direction to rotation angle (degrees)
# In KiCad .kicad_sym: angle 0 = pin points RIGHT (i.e. it extends to the left, placed on left side)
# angle 180 = pin points LEFT (placed on right side)
# angle 90 = pin points DOWN (placed on top)
# angle 270 = pin points UP (placed on bottom)
DIRECTION_TO_ANGLE = {
    "left": 0,     # pin stub extends left → placed on left edge, input
    "right": 180,  # pin stub extends right → placed on right edge, output
    "top": 270,    # pin stub extends up → placed on top edge
    "bottom": 90,  # pin stub extends down → placed on bottom edge
}


def _escape(s: str) -> str:
    """Escape a string for KiCad S-expression (quote if it contains spaces or special chars)."""
    if not s:
        return '""'
    needs_quote = any(c in s for c in (' ', '\t', '\n', '"', '(', ')'))
    if needs_quote:
        escaped = s.replace('\\', '\\\\').replace('"', '\\"')
        return f'"{escaped}"'
    return s


def write_kicad_sym(component_json: dict[str, Any]) -> str:
    """
    Convert component.json (matching component_output.json schema) to a KiCad 7 .kicad_sym string.

    Args:
        component_json: dict with key "symbol" containing name, reference, pins, body, format

    Returns:
        String containing a complete, valid KiCad 7 symbol library S-expression.
    """
    sym = component_json["symbol"]
    name = sym["name"]
    reference = sym.get("reference", "U")
    body = sym["body"]
    pins = sym["pins"]

    half_w = body["width"] / 2.0
    half_h = body["height"] / 2.0

    lines: list[str] = []
    lines.append('(kicad_symbol_lib (version 20231120) (generator "pcb-ai")')
    lines.append(f'  (symbol {_escape(name)}')
    lines.append('    (pin_names (offset 1.016))')
    lines.append(f'    (property "Reference" {_escape(reference)} (at 0 {half_h + 2.54:.4f} 0)')
    lines.append('      (effects (font (size 1.27 1.27)))')
    lines.append('    )')
    lines.append(f'    (property "Value" {_escape(name)} (at 0 {-(half_h + 2.54):.4f} 0)')
    lines.append('      (effects (font (size 1.27 1.27)))')
    lines.append('    )')
    lines.append(f'    (property "Footprint" "" (at 0 0 0)')
    lines.append('      (effects (font (size 1.27 1.27)) (hide yes))')
    lines.append('    )')
    lines.append(f'    (property "Datasheet" "" (at 0 0 0)')
    lines.append('      (effects (font (size 1.27 1.27)) (hide yes))')
    lines.append('    )')

    # Body rectangle goes in sub-symbol _0_1
    lines.append(f'    (symbol "{name}_0_1"')
    lines.append(
        f'      (rectangle (start {-half_w:.4f} {-half_h:.4f}) (end {half_w:.4f} {half_h:.4f})'
        f' (stroke (width 0) (type default)) (fill (type background)))'
    )
    lines.append('    )')

    # Pins go in sub-symbol _1_1
    lines.append(f'    (symbol "{name}_1_1"')
    for pin in pins:
        pin_num = pin["number"]
        pin_name = pin["name"]
        direction = pin.get("direction", "left")
        pin_type = pin.get("type", "passive")
        px = pin.get("x", 0.0)
        py = pin.get("y", 0.0)

        kicad_type = PIN_TYPE_MAP.get(pin_type, "passive")
        angle = DIRECTION_TO_ANGLE.get(direction, 0)

        # Pin length in mm (100mil = 2.54mm)
        pin_length = 2.54

        lines.append(
            f'      (pin {kicad_type} line (at {px:.4f} {py:.4f} {angle})'
            f' (length {pin_length})'
        )
        lines.append(
            f'        (name {_escape(pin_name)} (effects (font (size 1.27 1.27))))'
        )
        lines.append(
            f'        (number "{pin_num}" (effects (font (size 1.27 1.27))))'
        )
        lines.append('      )')
    lines.append('    )')  # close _1_1

    lines.append('  )')   # close symbol
    lines.append(')')     # close kicad_symbol_lib

    return '\n'.join(lines) + '\n'
