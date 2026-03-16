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
mkdir -p data/agents/summaries/import-export
cat > "data/agents/summaries/import-export/$(date -u +%Y-%m-%dT%H-%M-%S).md" << 'EOF'
# Title here
...
EOF
```

Only write a summary when you actually complete work that changes something. Skip it for read-only queries or if you did nothing.

## Committing and Pushing Changes
After completing any task that modifies files, always commit and push:
```bash
cd /app && git add -A && git commit -m "feat(agent): <short description of what was done>" && git push
```
Never skip this step — changes must be pushed so the user can see them on GitHub.
