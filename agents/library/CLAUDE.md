# Library Agent

## Purpose
Manage the component library. Fix bugs in component profiles, version history, and save/load workflows.

## Scope
- `frontend/static/library/` — all component profile directories
- `backend/schematic_api.py` — `/api/library/*` endpoints (history, save, activate, snapshots)
- `frontend/static/index.html` — library UI: profile viewer, pin editor, version control panel (lines 932–1804)

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
