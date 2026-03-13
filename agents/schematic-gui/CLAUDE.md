# Schematic GUI Agent

## Purpose
Fix bugs and improve the schematic canvas editor in the browser frontend.

## Scope
index.html was split into separate JS files — edit those directly, NOT index.html itself:

| File | What's in it |
|------|-------------|
| `frontend/static/js/index-schematic-editor.js` | **SchematicEditor class** — ALL canvas rendering, wire drawing, symbol placement, undo/redo, selection (~1512 lines). This is your primary file. |
| `frontend/static/js/index-schematic-ops.js` | Editor instantiation (`const editor`), `renderSchNets`, `exportSVG`, `exportPNG`, `exportBOM`, `exportPCBJson` |
| `frontend/static/js/index-symbol-renderer.js` | `drawSymbol`, `drawIC`, `drawResistor`, `drawCapacitor`, all canvas draw helpers |
| `frontend/static/js/index-symbol-editor.js` | `SymbolEditorSVG` class, `symEditor*` functions, info panels |
| `frontend/static/js/index-navigation.js` | `switchSection`, `renderSchPalette`, `SYMDEFS`, tabs/projects |
| `frontend/static/js/index-globals.js` | Global vars, `esc()`, SSE setup |

Backend:
- `backend/schematic_api.py` — `/api/projects/*` endpoints (lines ~423–564)

## Common Tasks
- Wire doesn't connect / wrong snap point
- Symbol ports misaligned after rotation
- Undo removes wrong elements
- Net label not associating with wire
- Component placement off-grid
- Canvas zoom/pan broken
- Selection box not clearing properly

## Key API Endpoints
- `POST /api/projects` — save schematic
- `GET  /api/projects/:id` — load schematic
- `GET  /api/projects` — list all projects
- `POST /api/netlist` — compute netlist from schematic JSON

## Rules
- `esc()` function must wrap all user-supplied strings in HTML/SVG output
- Grid is 20px — all positions must snap to multiples of 20
- Wire format: `{ id, points: [{x,y}...] }` — never use old x1/y1/x2/y2 format
- `_isEmbedded` flag on appCircuitEditor must not be removed
- After editing any JS file, hard-refresh the browser to test (Ctrl+Shift+R)

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
mkdir -p data/agents/summaries/schematic-gui
cat > "data/agents/summaries/schematic-gui/$(date -u +%Y-%m-%dT%H-%M-%S).md" << 'EOF'
# Title here
...
EOF
```

Only write a summary when you actually complete work that changes something. Skip it for read-only queries or if you did nothing.
