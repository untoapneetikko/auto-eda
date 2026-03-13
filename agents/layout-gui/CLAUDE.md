# Layout GUI Agent

## Purpose
Fix bugs and improve the PCB editor (`pcb.html`) and related backend endpoints.

## Scope
- `frontend/static/pcb.html` — PCBEditor class and all PCB UI
  - Component placement, movement, rotation
  - Trace routing (manual and auto)
  - Via placement
  - Layer switching and visibility
  - Ratsnest display
  - DRC results overlay
  - Layer Manager modal
  - Selection and deletion
- `backend/schematic_api.py` — `/api/pcb/*` endpoints

## Common Tasks
- Ratsnest lines not updating after routing
- Component rotation not persisting
- Layer panel not showing correct active layer
- DRC markers in wrong position
- Via not connected to net
- Board outline rendering issues
- Pad selection not working for SMD pads
- Board ID not found when running autoplace/autoroute

## Key API Endpoints
- `POST /api/pcb/autoplace` — place components
- `POST /api/pcb/autoroute` — route traces
- `POST /api/pcb/drc` — run DRC
- `POST /api/pcb/compute-ratsnest` — compute unrouted connections
- `GET  /api/pcb/:id/export/gerber` — Gerber export
- `GET  /api/pcb/:id/export/kicad` — KiCad export

## Rules
- Board ID: always read from `editor?.board?.id || _activeBoardId` — never `editor._boardId`
- Grid: 0.25mm for routing, 1mm for component placement
- All canvas coordinates are in mm (not pixels)
- After editing pcb.html, hard-refresh the browser to test (Ctrl+Shift+R)

## Updating Your Own Code
The agent files are live-mounted from the host. If you need the latest code:
```bash
cd /app && git pull
```
No Docker restart needed — changes take effect immediately.
