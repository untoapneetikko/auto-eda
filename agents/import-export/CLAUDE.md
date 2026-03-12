# Import / Export Agent

## Purpose
Fix bugs in all import and export functionality: Gerber, KiCad, BOM, and schematic import into PCB editor.

## Scope
- `backend/schematic_api.py` — export endpoints: `/api/pcb/:id/export/gerber`, `/api/pcb/:id/export/kicad`, `/api/projects/:id/bom`
- `frontend/static/pcb.html` — Import Schematic button and SchematicImporter logic
- `frontend/static/index.html` — any export/download triggers in the schematic editor

## Common Tasks
- Gerber files missing copper layer or drill file
- KiCad .kicad_pcb file not opening in KiCad
- BOM export missing components or wrong quantities
- Import Schematic fails to find project or crashes
- Pad coordinates wrong in export (coordinate system mismatch)
- Net names not matching between schematic and PCB export

## Key API Endpoints
- `GET  /api/pcb/:id/export/gerber` — download Gerber zip
- `GET  /api/pcb/:id/export/kicad` — download .kicad_pcb
- `GET  /api/projects/:id/bom` — get Bill of Materials
- `GET  /api/projects` — list projects for import

## Rules
- Gerber format: RS-274X, layers: F_Cu.gtl, B_Cu.gbl, F_SilkS.gto, F_Mask.gts, Edge_Cuts.gm1, drill.drl
- KiCad format: S-expression (.kicad_pcb), KiCad 6+ compatible
- BOM columns: ref, value, footprint, quantity, description
- Coordinate origin: bottom-left corner of board, Y-axis flipped for Gerber (Y increases downward)
- All dimensions in mm in KiCad format
