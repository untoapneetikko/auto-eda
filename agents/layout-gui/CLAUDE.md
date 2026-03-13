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
- `POST /api/pcb/import-schematic` — convert schematic project → initial PCB board layout
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

## PCB iframe URL modes
The PCB editor (`pcb.html`) has two embedded URL modes — do NOT confuse them:
| URL param | Used by | `.embedded` class | Board tabs | Left panel |
|---|---|---|---|---|
| `?app=1` | Main app PCB section (`pcb-frame`) | No | Visible | Visible |
| `?embedded=1` | Layout Example preview (`le-frame`) | Yes | Hidden | Hidden |

Never change `pcb-frame` back to `?embedded=1` — that hides the board tabs and left panel.

## Known Architecture: _doPCBImport re-entrancy
`_doPCBImport` (index.html) calls `switchSection('pcb')` which calls `_pcbAutoLoad`
which can call `_doPCBImport` again → infinite loop.
Guard: `_pcbImporting` boolean flag in index.html prevents re-entry.
`_pcbLoadedProjectId` is only set after `importProjectDone` is received — do not rely on it
being set during the import sequence.

## Known Architecture: import-schematic flow
1. User opens PCB layout → `openPCBLayout()` or `_pcbAutoLoad()` calls `_doPCBImport(projectId)`
2. `_doPCBImport` posts `importProject` message to `pcb-frame` iframe (retries up to 20×)
3. `pcb.html` handles `importProject`: fetches project JSON, calls `importSchematic(proj, netlist, bw, bh)`
4. `importSchematic` POSTs to `POST /api/pcb/import-schematic` with `{project, netlist, boardW, boardH}`
5. Backend converts schematic components → PCB components with footprints, returns board JSON
6. pcb.html loads board, saves it, sends `importProjectDone` back → `_doPCBImport` stops retrying

## Footprint selection logic (backend `_pick_footprint`)
- RESISTOR/CAPACITOR/DIODE/INDUCTOR/ZENER → 0402
- CAPACITOR_POL → CAP-POL-5mm
- BC547/BC557/AP2112 → SOT-23
- AMS1117-3.3 → SOT-223
- DRV8833 → TSSOP-16
- ICs by pin count: ≤8→DIP-8, ≤16→DIP-16, ≤20→DIP-20, ≤28→DIP-28, ≤40→DIP-40, else LQFP-64
- VCC/GND/PWR symTypes → skipped (no physical footprint)

## Footprint files location
`frontend/static/pcb/footprints/*.json` — 89 JSON files.
Each has: `name`, `pads[]` (with `number`, `x`, `y`, `type`, `shape`, `size_x`, `size_y`, optional `drill`), `courtyard`.

## Updating Your Own Code
The agent files are live-mounted from the host. If you need the latest code:
```bash
cd /app && git pull
```
No Docker restart needed — changes take effect immediately.
