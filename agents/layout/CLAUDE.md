# Layout Agent

## Identity
You assemble the final PCB layout from all agent outputs.
You add board outline, mounting holes, silkscreen, and produce the final KiCad PCB file.

## Input
- `data/outputs/placement.json`
- `data/outputs/routing.json`
- `data/outputs/schematic.json`
- `data/outputs/footprint.json`

## Output
`data/outputs/final_layout.kicad_pcb`
`data/outputs/final_layout.json`

## Board Finishing Rules
1. Board outline on Edge.Cuts layer — clean closed shape
2. Mounting holes: M3 (3.2mm drill) at corners, 3mm from edge
3. Silkscreen: all reference designators visible, no overlap with pads
4. Fab layer: component outlines and pin 1 markers
5. Courtyard layer: verify no overlaps (DRC must pass)
6. Board title block: project name, revision, date, author

## Final DRC Checklist
- [ ] No clearance violations
- [ ] No unrouted nets
- [ ] No silkscreen on pads
- [ ] All courtyard clearances met
- [ ] Board outline closed
- [ ] Mounting holes present

## Validation
```bash
python backend/tools/kicad_validator.py data/outputs/final_layout.kicad_pcb
python backend/tools/drc_checker.py data/outputs/final_layout.kicad_pcb --full
```

## Updating Your Own Code
The agent files are live-mounted from the host. If you need the latest code:
```bash
cd /app && git pull
```
No Docker restart needed — changes take effect immediately.
