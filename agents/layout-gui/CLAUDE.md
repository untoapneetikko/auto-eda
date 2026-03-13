# Layout GUI Agent

## Purpose
Fix bugs and improve the PCB editor (`pcb.html`) and related backend endpoints.

## Scope
pcb.html was split into separate JS files — edit those directly, NOT pcb.html itself:

| File | What's in it |
|------|-------------|
| `frontend/static/js/pcb-editor.js` | **PCBEditor class** — ALL canvas rendering, hit-testing, layers, routing, selection (~1580 lines). This is your primary file. |
| `frontend/static/js/pcb-board-tabs.js` | Board tabs, `saveBoard`, `deleteCurrentBoard`, `loadBoardTabs`, `renderNetsSection` |
| `frontend/static/js/pcb-tools.js` | `setTool`, `fitBoard`, `zoomIn`, `zoomOut`, `toggleRatsnest`, `loadFile`, `afterLoad` |
| `frontend/static/js/pcb-panels.js` | `buildLayerPanel`, `rebuildCompList`, `updateInfoPanel`, `rotSel`, `flipSel`, `delSel` |
| `frontend/static/js/pcb-automate.js` | `runAutoRoute`, `runAutoPlace`, `newBoardVersion`, `openImportModal`, `doImport` |
| `frontend/static/js/pcb-drc.js` | `openDRC`, `populateDRTab`, `runDRCTab`, `openLayerManager`, `exportGerber`, `exportKiCad` |
| `frontend/static/js/pcb-bootstrap.js` | App init, `window.addEventListener('load',...)`, postMessage handler (`importProject`, `projectClosed`) |
| `frontend/static/js/pcb-globals.js` | `esc`, `SYMDEFS`, `EXAMPLE_PCB`, `DR` design-rules object |
| `frontend/static/js/pcb-api.js` | `importSchematic`, `runDRC` (API call wrappers) |
| `frontend/static/js/pcb-3d.js` | `PCB3DViewer` class, `open3DView`, `openModal`, `closeModal` |

Backend:
- `backend/schematic_api.py` — `/api/pcb/*` endpoints (lines ~2491–3984)

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
`_doPCBImport` (in `frontend/static/js/index-pcb-bridge.js`) calls `switchSection('pcb')` which calls `_pcbAutoLoad`
which can call `_doPCBImport` again → infinite loop.
Guard: `_pcbImporting` boolean flag in `index-pcb-bridge.js` prevents re-entry.
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

## After Completing Work

After finishing any task, write a short summary so the user can see what changed.
Create the file `data/agents/summaries/{AGENT_NAME}/` + current UTC timestamp like `2026-03-13T14-30-00.md`.

Format (keep it brief, user-facing plain English):
```md
# {one-line title of what was done}

## What changed
- bullet points of actual changes made

## Files
- list of files modified/created

## Notes
Any gotchas or important info the user should know (omit section if nothing to add)
```

Use this bash to write it:
```bash
mkdir -p data/agents/summaries/layout-gui
cat > "data/agents/summaries/layout-gui/$(date -u +%Y-%m-%dT%H-%M-%S).md" << 'EOF'
# Title here
...
EOF
```

Only write a summary when you actually complete work that changes something. Skip it for read-only queries or if you did nothing.
