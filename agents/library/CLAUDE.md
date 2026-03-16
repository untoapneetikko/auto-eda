# Library Agent

## Purpose
Manage the component library. Fix bugs in component profiles, version history, and save/load workflows.

## Scope
- `frontend/static/library/` — all component profile directories
- `backend/schematic_api.py` — `/api/library/*` endpoints (lines 1–1516, before the pipeline agents section)
- **Frontend JS files** (index.html was split — edit these, NOT index.html directly):
  - `frontend/static/js/index-library.js` — profile viewer, upload, `renderLibrary`, `selectPart`, `loadProfile`, `renderProfile`, `switchProfileTab`, `loadActiveVersionBadge`
  - `frontend/static/js/index-footprint.js` — `FootprintEditor` class, `initFootprintTab`, `loadFootprint`, `fpAddPad`, `fpRenderPadList`
  - `frontend/static/js/index-agent-history.js` — `ahLoad`, `_ahRenderTree`, `historyNewVersion`, `_loadHistoryList`, version history panel
  - `frontend/static/js/index-globals.js` — global vars (`library`, `selectedSlug`, `profileCache`), SSE wiring

## Common Tasks
- Fix component profile JSON (pins, passives, example_circuit, footprint)
- Debug version history: `openHistoryPanel`, `historySave`, `historyActivate`, `historySaveActive`, `_loadHistoryList`
- Fix component save not persisting (check PUT /api/library/:slug/pins and snapshot endpoints)
- Add missing pins or correct pin types/names
- Preserve `human_corrections` arrays — never overwrite them

## Key API Endpoints
- `GET  /api/library` — list all components
- `GET  /api/library/:slug/profile` — read profile
- `PUT  /api/library/:slug/pins` — save pin changes
- `GET  /api/library/:slug/history` — list snapshots
- `POST /api/library/:slug/history/save` — create snapshot
- `POST /api/library/:slug/history/:id/activate` — restore snapshot

## Rules
- Always read the profile before editing it
- Preserve `human_corrections` — they are manually verified data
- After fixing a bug, test by calling the relevant API endpoint
- `symbol_type` must always be `"ic"` in profiles

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
mkdir -p data/agents/summaries/library
cat > "data/agents/summaries/library/$(date -u +%Y-%m-%dT%H-%M-%S).md" << 'EOF'
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
