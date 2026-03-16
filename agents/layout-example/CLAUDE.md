# Layout Example Agent

## Purpose
Generate a PCB layout example for a component's typical application circuit. This is the PCB counterpart to the Example Schematic — it places and routes the reference design so engineers have a known-good board layout to start from.

## Input
- `frontend/static/library/:slug/profile.json` — must have `example_circuit` populated first
- Run example-schematic agent first if `example_circuit` is missing

## Output
- `frontend/static/library/:slug/layout_example.json` — PCB layout JSON
- Schema matches the PCB board format: `{board, components[], nets[], traces[], vias[]}`

## Workflow
1. Read `profile.json` — extract `example_circuit` netlist
2. Call `POST /api/netlist` with the schematic components/wires to extract nets
3. Call `POST /api/pcb/autoplace` with the netlist to get initial placement
4. Call `POST /api/pcb/autoroute` with the placement to get traces
5. Write the combined result to `library/:slug/layout_example.json`
6. Set `profile.json` field `has_layout_example: true`

## Key API Endpoints
- `POST /api/netlist` — extract netlist from schematic
- `POST /api/pcb/autoplace` — place components
- `POST /api/pcb/autoroute` — route traces
- `PUT  /api/library/:slug/profile` — update profile fields

## Rules
- Board size: default 50mm × 50mm unless component footprint suggests otherwise
- Use footprint data from `profile.json` if available
- Snap all placements to 0.25mm grid
- Write compact JSON (no pretty-print indent > 2)

## Updating Your Own Code
The agent files are live-mounted from the host. If you need the latest code:
```bash
cd /app && git pull
```
No Docker restart needed — changes take effect immediately.

## Testing Components — Skip Cache

Before writing ANY output file or summary, check `data/config/testing_components.json`.
If the component slug being processed appears in `skip_cache`, you must:
- Skip writing `library/:slug/layout_example.json`
- Skip updating `profile.json` with `has_layout_example: true`
- Skip writing any summary file under `data/agents/summaries/`
- Skip the git commit/push step
- Still complete the work and respond to the user normally

```bash
python3 -c "
import json, sys
cfg = json.load(open('data/config/testing_components.json'))
if sys.argv[1] in cfg.get('skip_cache', []): print('SKIP_CACHE')
" <component_slug>
```

If the output is `SKIP_CACHE`, omit all file writes and commits for this run.

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
mkdir -p data/agents/summaries/layout-example
cat > "data/agents/summaries/layout-example/$(date -u +%Y-%m-%dT%H-%M-%S).md" << 'EOF'
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
