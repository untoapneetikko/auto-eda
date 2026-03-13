"""
kicad_pcb_writer.py — Converts placement/routing data to KiCad PCB S-expression format.

Produces a valid .kicad_pcb file with:
- Board outline on Edge.Cuts
- M3 mounting holes at corners (3.2mm drill, 3mm from edge)
- All footprints placed at their placement coordinates
- All routed traces and vias
- Silkscreen reference designators
- Fab layer component outlines
- Courtyard rectangles
"""

from __future__ import annotations

import datetime
import math
from typing import Any


# ---------------------------------------------------------------------------
# S-expression helpers
# ---------------------------------------------------------------------------

def _f(value: float) -> str:
    """Format a float for KiCad S-expressions (4 decimal places)."""
    return f"{value:.4f}"


def _xy(x: float, y: float) -> str:
    return f"(xy {_f(x)} {_f(y)})"


# ---------------------------------------------------------------------------
# Pad type helpers
# ---------------------------------------------------------------------------

def _pad_layers(pad_type: str, layer: str) -> str:
    """Return the layers string for a pad based on type and component layer."""
    cu = layer  # "F.Cu" or "B.Cu"
    side = "F" if cu == "F.Cu" else "B"
    if pad_type == "thru_hole":
        return '"*.Cu" "*.Mask"'
    else:
        # SMD
        return f'"{cu}" "{side}.Paste" "{side}.Mask"'


# ---------------------------------------------------------------------------
# Main writer
# ---------------------------------------------------------------------------

class KiCadPCBWriter:
    """
    Assembles a complete .kicad_pcb file from placement, routing, schematic,
    and footprint data.
    """

    LAYER_DEFS = [
        (0,  "F.Cu",      "signal"),
        (31, "B.Cu",      "signal"),
        (44, "B.CrtYd",   "user"),
        (45, "F.CrtYd",   "user"),
        (46, "B.Fab",     "user"),
        (47, "F.Fab",     "user"),
        (48, "B.SilkS",   "user"),
        (49, "F.SilkS",   "user"),
        (50, "Edge.Cuts", "user"),
    ]

    # Mounting hole parameters (M3)
    MHOLE_DRILL_MM   = 3.2
    MHOLE_PAD_MM     = 6.0   # annular ring outer diameter (no copper — NPTH)
    MHOLE_MARGIN_MM  = 3.0   # distance from board edge

    def __init__(
        self,
        placement_data: dict[str, Any],
        routing_data:   dict[str, Any],
        schematic_data: dict[str, Any],
        footprint_data: dict[str, Any],
        project_name:   str = "PCB-AI Project",
    ):
        self.placement  = placement_data
        self.routing    = routing_data
        self.schematic  = schematic_data
        self.footprint  = footprint_data
        self.project    = project_name

        board = placement_data.get("board", {})
        self.board_w = float(board.get("width_mm",  100.0))
        self.board_h = float(board.get("height_mm",  80.0))

        # Build a quick lookup: reference → placement record
        self.placement_map: dict[str, dict] = {
            p["reference"]: p
            for p in placement_data.get("placements", [])
        }

        # Build net index: name → integer id (1-based; 0 = unconnected)
        all_nets = schematic_data.get("nets", [])
        self.net_index: dict[str, int] = {}
        for i, net in enumerate(all_nets, start=1):
            name = net.get("name", f"Net-{i}")
            self.net_index[name] = i

        # Component connection map: reference → {pin_number: net_name}
        self.comp_connections: dict[str, dict[int, str]] = {}
        for comp in schematic_data.get("components", []):
            ref = comp["reference"]
            self.comp_connections[ref] = {
                int(c["pin"]): c["net"]
                for c in comp.get("connections", [])
            }

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def write(self) -> str:
        """Return the full .kicad_pcb content as a string."""
        lines: list[str] = []

        lines.append('(kicad_pcb (version 20231120) (generator "pcb-ai")')
        lines.append("")

        # General
        lines.append(f'  (general (thickness 1.6))')
        lines.append("")

        # Paper / title block
        now = datetime.date.today().isoformat()
        lines.append('  (paper "A4")')
        lines.append(
            f'  (title_block (title "{self.project}") (rev "1.0") (date "{now}")'
            f' (company "PCB-AI") (comment 1 "Auto-generated"))'
        )
        lines.append("")

        # Layers
        lines.append("  (layers")
        for num, name, ltype in self.LAYER_DEFS:
            lines.append(f'    ({num} "{name}" {ltype})')
        lines.append("  )")
        lines.append("")

        # Setup block (design rules)
        lines.extend(self._setup_block())
        lines.append("")

        # Nets
        lines.append('  (net 0 "")')
        for name, idx in sorted(self.net_index.items(), key=lambda kv: kv[1]):
            lines.append(f'  (net {idx} "{name}")')
        lines.append("")

        # Board outline (Edge.Cuts)
        lines.extend(self._board_outline())
        lines.append("")

        # Mounting holes
        lines.extend(self._mounting_holes())
        lines.append("")

        # Footprints
        for fp_block in self._footprints():
            lines.extend(fp_block)
            lines.append("")

        # Traces
        lines.extend(self._traces())
        lines.append("")

        # Vias
        lines.extend(self._vias())

        lines.append(")")  # close kicad_pcb
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Setup / design rules
    # ------------------------------------------------------------------

    def _setup_block(self) -> list[str]:
        return [
            "  (setup",
            "    (pad_to_mask_clearance 0.05)",
            "    (pcbplotparams",
            "      (layerselection 0x00010fc_ffffffff)",
            "      (outputdirectory \"./\")",
            "      (disableapertmacros false)",
            "      (usegerberextensions false)",
            "      (usegerberattributes true)",
            "      (usegerberadvancedattributes true)",
            "      (creategerberjobfile true)",
            "      (svgprecision 6)",
            "      (plotframeref false)",
            "      (viasonmask false)",
            "      (mode 1)",
            "      (useauxorigin false)",
            "      (hpglpennumber 1)",
            "      (hpglpenspeed 20)",
            "      (hpglpendiameter 15.000000)",
            "      (dxfpolygonmode true)",
            "      (dxfimperialunits true)",
            "      (dxfusepcbnewfont true)",
            "      (psnegative false)",
            "      (psa4output false)",
            "      (plotreference true)",
            "      (plotvalue true)",
            "      (plotinvisibletext false)",
            "      (sketchpadsonfab false)",
            "      (subtractmaskfromsilk true)",
            "      (outputformat 1)",
            "      (mirror false)",
            "      (drillshape 1)",
            "      (scaleselection 1)",
            "      (outputdirectory \"./\")",
            "    )",
            "  )",
        ]

    # ------------------------------------------------------------------
    # Board outline
    # ------------------------------------------------------------------

    def _board_outline(self) -> list[str]:
        w, h = self.board_w, self.board_h
        stroke = '(stroke (width 0.05) (type default))'
        return [
            f'  (gr_rect (start 0 0) (end {_f(w)} {_f(h)}) (layer "Edge.Cuts") {stroke})',
        ]

    # ------------------------------------------------------------------
    # Mounting holes — M3 at four corners
    # ------------------------------------------------------------------

    def _mounting_holes(self) -> list[str]:
        m  = self.MHOLE_MARGIN_MM
        w  = self.board_w
        h  = self.board_h
        d  = self.MHOLE_DRILL_MM
        pd = self.MHOLE_PAD_MM

        corners = [
            (m,     m,     "MH1"),
            (w - m, m,     "MH2"),
            (w - m, h - m, "MH3"),
            (m,     h - m, "MH4"),
        ]

        blocks: list[str] = []
        for x, y, ref in corners:
            blocks += [
                f'  (footprint "MountingHole_3.2mm_M3" (layer "F.Cu") (at {_f(x)} {_f(y)})',
                f'    (property "Reference" "{ref}" (at 0 -4) (layer "F.SilkS") (hide yes))',
                f'    (property "Value" "MountingHole_M3" (at 0 4) (layer "F.Fab") (hide yes))',
                # Courtyard circle
                f'    (fp_circle (center 0 0) (end {_f(pd/2)} 0) (layer "F.CrtYd")'
                f' (stroke (width 0.05) (type solid)))',
                # Fab circle
                f'    (fp_circle (center 0 0) (end {_f(pd/2)} 0) (layer "F.Fab")'
                f' (stroke (width 0.05) (type solid)))',
                # NPTH drill pad (no copper net)
                f'    (pad "" np_thru_hole circle (at 0 0) (size {_f(d)} {_f(d)})'
                f' (drill {_f(d)}) (layers "*.Cu" "*.Mask"))',
                f'  )',
            ]
        return blocks

    # ------------------------------------------------------------------
    # Footprints
    # ------------------------------------------------------------------

    def _footprints(self) -> list[list[str]]:
        """
        For every component in the schematic, emit a footprint block using:
        - pad geometry from footprint_data (the last-parsed component footprint)
        - position/rotation from placement_data
        - net assignments from schematic component connections
        """
        fp_pads   = self.footprint.get("pads", [])
        fp_name   = self.footprint.get("name", "Unknown")
        courtyard = self.footprint.get("courtyard", {})
        cy_x  = float(courtyard.get("x",  0))
        cy_y  = float(courtyard.get("y",  0))
        cy_w  = float(courtyard.get("width",  2.0))
        cy_h  = float(courtyard.get("height", 2.0))

        results: list[list[str]] = []

        for comp in self.schematic.get("components", []):
            ref       = comp["reference"]
            value     = comp.get("value", "")
            footprint = comp.get("footprint", fp_name)
            conn_map  = self.comp_connections.get(ref, {})

            # Placement
            place = self.placement_map.get(ref)
            if place:
                px  = float(place["x"])
                py  = float(place["y"])
                rot = float(place.get("rotation", 0))
                layer = place.get("layer", "F.Cu")
            else:
                px, py, rot, layer = 0.0, 0.0, 0.0, "F.Cu"

            side = "F" if layer == "F.Cu" else "B"
            silk_layer  = f"{side}.SilkS"
            fab_layer   = f"{side}.Fab"
            crtyd_layer = f"{side}.CrtYd"

            block: list[str] = [
                f'  (footprint "{footprint}" (layer "{layer}") '
                f'(at {_f(px)} {_f(py)} {_f(rot)})',
                f'    (property "Reference" "{ref}" (at 0 -3) (layer "{silk_layer}"))',
                f'    (property "Value" "{value}" (at 0 3) (layer "{fab_layer}"))',
            ]

            # Courtyard rectangle (relative to footprint center)
            block.append(
                f'    (fp_rect '
                f'(start {_f(cy_x)} {_f(cy_y)}) '
                f'(end {_f(cy_x + cy_w)} {_f(cy_y + cy_h)}) '
                f'(layer "{crtyd_layer}") '
                f'(stroke (width 0.05) (type solid)))'
            )
            # Fab outline
            block.append(
                f'    (fp_rect '
                f'(start {_f(cy_x + 0.1)} {_f(cy_y + 0.1)}) '
                f'(end {_f(cy_x + cy_w - 0.1)} {_f(cy_y + cy_h - 0.1)}) '
                f'(layer "{fab_layer}") '
                f'(stroke (width 0.05) (type solid)))'
            )
            # Pin 1 marker (small triangle on Fab)
            if fp_pads:
                first = fp_pads[0]
                p1x = float(first.get("x", 0))
                p1y = float(first.get("y", 0))
                block.append(
                    f'    (fp_circle (center {_f(p1x)} {_f(p1y)}) '
                    f'(end {_f(p1x + 0.3)} {_f(p1y)}) '
                    f'(layer "{fab_layer}") (stroke (width 0.1) (type solid)))'
                )

            # Pads
            for pad_def in fp_pads:
                pad_num   = pad_def.get("number", 1)
                pad_type  = pad_def.get("type", "smd")
                pad_shape = pad_def.get("shape", "rect")
                pad_x     = float(pad_def.get("x", 0))
                pad_y     = float(pad_def.get("y", 0))
                pad_w     = float(pad_def.get("width",  1.5))
                pad_h     = float(pad_def.get("height", 0.5))
                drill     = float(pad_def.get("drill", 0))

                # Determine net for this pad
                net_name = conn_map.get(int(pad_num), "")
                net_id   = self.net_index.get(net_name, 0)
                net_str  = f'(net {net_id} "{net_name}")' if net_name else ""

                if pad_type == "thru_hole":
                    pad_type_kicad = "thru_hole"
                    layers_str = '"*.Cu" "*.Mask"'
                    drill_str  = f'(drill {_f(drill)})'
                else:
                    pad_type_kicad = "smd"
                    layers_str = _pad_layers(pad_type, layer)
                    drill_str  = ""

                line = (
                    f'    (pad "{pad_num}" {pad_type_kicad} {pad_shape} '
                    f'(at {_f(pad_x)} {_f(pad_y)}) '
                    f'(size {_f(pad_w)} {_f(pad_h)}) '
                    f'(layers {layers_str})'
                )
                if drill_str:
                    line += f' {drill_str}'
                if net_str:
                    line += f' {net_str}'
                line += ")"
                block.append(line)

            block.append("  )")
            results.append(block)

        return results

    # ------------------------------------------------------------------
    # Traces
    # ------------------------------------------------------------------

    def _traces(self) -> list[str]:
        lines: list[str] = []
        for trace in self.routing.get("traces", []):
            net_name = trace.get("net", "")
            net_id   = self.net_index.get(net_name, 0)
            layer    = trace.get("layer", "F.Cu")
            width    = float(trace.get("width_mm", 0.2))
            path     = trace.get("path", [])

            for i in range(len(path) - 1):
                x1 = float(path[i]["x"])
                y1 = float(path[i]["y"])
                x2 = float(path[i + 1]["x"])
                y2 = float(path[i + 1]["y"])
                lines.append(
                    f'  (segment (start {_f(x1)} {_f(y1)}) (end {_f(x2)} {_f(y2)}) '
                    f'(width {_f(width)}) (layer "{layer}") (net {net_id}))'
                )
        return lines

    # ------------------------------------------------------------------
    # Vias
    # ------------------------------------------------------------------

    def _vias(self) -> list[str]:
        lines: list[str] = []
        for via in self.routing.get("vias", []):
            net_name = via.get("net", "")
            net_id   = self.net_index.get(net_name, 0)
            x        = float(via.get("x", 0))
            y        = float(via.get("y", 0))
            drill    = float(via.get("drill_mm", 0.3))
            size     = drill + 0.4  # standard annular ring
            lines.append(
                f'  (via (at {_f(x)} {_f(y)}) (size {_f(size)}) (drill {_f(drill)}) '
                f'(layers "F.Cu" "B.Cu") (net {net_id}))'
            )
        return lines
