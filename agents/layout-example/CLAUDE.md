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
