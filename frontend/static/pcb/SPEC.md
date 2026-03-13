# PCB Editor — Design Specification

This document covers everything needed to understand, build on, and extend the PCB editor.
It is written for the next agent or developer picking this up cold.

---

## Current State

`pcb.html` (at the project root) is a working but rough first-pass PCB editor. It already has:

| Capability | Status | Notes |
|---|---|---|
| Canvas rendering (Canvas 2D) | ✅ Done | Layers, pads, traces, vias, ratsnest, board outline |
| Schematic → PCB conversion | ✅ Done | `SchematicImporter.convert()` — reads a project JSON from `/api/projects/:id` |
| Layer system | ✅ Done | F.Cu, B.Cu, F.SilkS, B.SilkS, Edge.Cuts, Ratsnest |
| Component placement + drag | ✅ Done | Drag on canvas, right panel shows pad/net details |
| Manual trace routing | ✅ Done | Click-to-route with snap, layer-aware |
| Via placement | ✅ Done | Single via type, configurable size/drill |
| Copper zone drawing | ✅ Done | Polygon zone with visual fill |
| Auto-router | ✅ Done | Lee BFS, single-layer F.Cu, MST connection ordering |
| DRC | ✅ Done | Clearance, min trace width, unrouted nets, edge clearance |
| Gerber export | ✅ Done | F.Cu, B.Cu, F.SilkS, Edge.Cuts, Drill (Excellon) in ZIP |
| KiCad export | ✅ Done | `.kicad_pcb` format stub |
| Save/load native JSON | ✅ Done | Native PCB JSON format (see below) |
| Real footprint library | ❌ Missing | All footprints are hardcoded stubs (0402, SOT-23, etc.) |
| IC footprints from library | ❌ Missing | ICs get an empty `IC` footprint — pads are not placed |
| Schematic ↔ PCB sync | ❌ Missing | Changes in schematic don't propagate to open PCB |
| Back-annotation | ❌ Missing | No designator/net updates flowing back |
| Multi-layer routing | ❌ Missing | Auto-router is single-layer only |
| Teardrops / DFM | ❌ Missing | |
| Board outline editor | ❌ Missing | Board dimensions are set at import time only |

---

## File Layout (intended)

```
pcb/
  SPEC.md            ← this file
  footprints/        ← future: footprint library JSON files
    0402.json
    SOT-23.json
    TO-220.json
    QFN16_3x3.json
    DIP28.json
    ...

pcb.html             ← main PCB editor (currently at project root)
PCB_JSON_FORMAT.md   ← schematic→PCB export format docs (at project root)
```

`pcb.html` lives at the root so it is served at `http://localhost:3030/pcb.html`.
When it is mature enough it may be moved to `pcb/pcb.html` and served via an
explicit server route; for now keep it at the root.

---

## JSON Formats — Two Separate Things

There are **two different JSON formats** in play. Do not confuse them.

### 1. Schematic Export JSON (input to PCB editor, from schematic)

Produced by the schematic editor's **⬇ PCB JSON** button.
Also the format returned by `/api/projects/:id` when `SchematicImporter.convert()` is called.
Documented fully in `PCB_JSON_FORMAT.md` at the project root.

Key characteristics:
- `"format": "schematic-designer-pcb-v1"`
- Coordinates in **abstract schematic grid units** (multiples of 20, not mm)
- No footprint geometry — components have a `package` string only
- Nets pre-extracted with Union-Find
- Power symbols (vcc, gnd) present but have no physical footprint
- Pin numbers null for passives

```jsonc
{
  "format": "schematic-designer-pcb-v1",
  "components": [
    {
      "designator": "U1",
      "slug": "LM7805",
      "symbol_type": "ic",
      "package": "TO-220",      // ← use this to look up footprint
      "pins": [
        { "name": "INPUT", "number": 1, "type": "power", "net": "VIN" }
      ],
      "position": { "x": 300, "y": 200 },  // schematic coords, NOT mm
      "rotation_quarters": 0
    }
  ],
  "nets": [{ "name": "VIN", "pins": ["U1.INPUT", "VIN1.VCC"] }]
}
```

### 2. Native PCB JSON (the editor's own save format)

Used internally by the PCB editor. This is what `editor.load()` consumes,
what `exportJSON()` saves, and what `SchematicImporter.convert()` produces.
All coordinates are in **millimeters**.

```jsonc
{
  "version": "1.0",
  "title": "My Board",
  "board": { "width": 50, "height": 35, "units": "mm" },

  "components": [
    {
      "id": "U1",           // internal id (can match designator)
      "ref": "U1",          // reference designator shown on silkscreen
      "value": "LM7805",    // value shown on silkscreen
      "footprint": "TO-220",// footprint name (used for rendering + export)
      "x": 25.0,            // board X position in mm
      "y": 17.5,            // board Y position in mm
      "rotation": 0,        // degrees clockwise
      "layer": "F",         // "F" = front, "B" = back

      "pads": [
        {
          "number": "1",        // pad number (string)
          "name": "INPUT",      // pin name
          "x": -2.54,           // pad X offset from component origin (mm)
          "y": 0,               // pad Y offset from component origin (mm)
          "type": "thru_hole",  // "thru_hole" | "smd" | "np_thru_hole"
          "shape": "rect",      // "rect" | "circle" | "oval" | "roundrect"
          "size_x": 1.8,        // pad width (mm)
          "size_y": 1.8,        // pad height (mm)
          "drill": 1.0,         // drill diameter (mm, thru_hole only)
          "net": "VIN"          // net name (null if unconnected)
        }
      ]
    }
  ],

  "nets": [
    {
      "name": "VIN",
      "pads": ["U1.1", "C1.1", "J1.1"]  // "REF.padnumber"
    }
  ],

  "traces": [
    {
      "net": "VIN",
      "layer": "F.Cu",           // "F.Cu" | "B.Cu"
      "width": 0.25,             // trace width (mm)
      "segments": [
        { "x1": 15, "y1": 17.5, "x2": 25, "y2": 17.5 }  // mm
      ]
    }
  ],

  "vias": [
    {
      "x": 20, "y": 17.5,        // mm
      "size": 1.0,               // via annular ring outer diameter (mm)
      "drill": 0.6,              // drill diameter (mm)
      "net": "VIN"
    }
  ],

  "zones": [
    {
      "net": "GND",
      "layer": "F.Cu",
      "points": [{ "x": 0, "y": 0 }, { "x": 50, "y": 0 }, ...]  // mm
    }
  ]
}
```

---

## Schematic → PCB Conversion (`SchematicImporter.convert`)

Located in `pcb.html` around line 778. Here is exactly what it does:

1. **Read raw project JSON** (`/api/projects/:id`) — the schematic's internal format
   (components + wires, NOT the exported PCB JSON v1).

2. **Compute world-space port positions** for every component pin, accounting for
   rotation, using `SYMDEFS` and `_icLayout`-style offsets (embedded in the importer).

3. **Union-Find netlist** — same algorithm as the schematic's own net extraction:
   - Each wire polyline point is a node
   - Points within `SNAP=5` units of a component port are merged
   - All wire points on one polyline are merged

4. **Name nets** — VCC ports → `"VCC"`, GND ports → `"GND"`, others → `NET_<des>_<pin>`.
   ⚠️ Does not yet use net labels. Labels are in `project.labels` but the importer ignores them.

5. **Assign footprints** — `SchematicImporter.footprint(symType, comp)`:
   - passives (resistor, cap, inductor, diode, led) → hardcoded 0402 SMD
   - transistors (npn/pnp, nmos/pmos) → SOT-23
   - opamp → SO-8
   - `ic` type → empty `{fp:'IC', pads:[]}` ← **ICs get no pads at all**

6. **Scale to board mm** — schematic coords are normalized `[0..1]` then mapped to
   board dimensions with 12% margin. `SCALE ≈ boardW / schematicWidth`.

7. **Return native PCB JSON** ready for `editor.load()`.

### Critical gaps to fix

| Gap | Location | Fix |
|---|---|---|
| ICs have empty pads | `footprint()` default case | Load `library/<slug>/profile.json` and generate pads from `pins[]` array using package pitch data |
| Net labels ignored | `convert()` line ~830 | Read `project.labels`, union all wire nodes touching a label position, then merge nets by label name |
| Footprint geometry hardcoded | `footprint()` | Replace with a footprint library lookup |
| VCC net naming uses value | Line ~846 | Should read `comp.value` (e.g. `"5V"`, `"3V3"`) not just `"VCC"` |

---

## Footprint Problem — The Main Blocker

The biggest gap is footprint geometry. Every component needs:
- Pad positions (x, y offsets from center)
- Pad size and shape
- Drill size (for through-hole)
- Courtyard / silkscreen outline dimensions

The current code has hardcoded stubs for 5 passive types. Real ICs (ATmega, ESP32, etc.)
are placed with **zero pads**, making routing impossible.

### Footprint data needed per component

From the library profile's `package_types[0]` field (e.g. `"TO-220"`, `"28-DIP"`,
`"16-Pad 3x3mm QFN"`) we need to know pad positions.

### Recommended approach: footprint JSON library

Create `pcb/footprints/<name>.json` files. Example:

```jsonc
// pcb/footprints/TO-220.json
{
  "name": "TO-220",
  "description": "TO-220 3-pin through-hole, 2.54mm pitch",
  "pads": [
    { "number": "1", "x": -2.54, "y": 0, "type": "thru_hole", "shape": "rect",   "size_x": 1.8, "size_y": 1.8, "drill": 1.0 },
    { "number": "2", "x":  0,    "y": 0, "type": "thru_hole", "shape": "circle", "size_x": 1.8, "size_y": 1.8, "drill": 1.0 },
    { "number": "3", "x":  2.54, "y": 0, "type": "thru_hole", "shape": "circle", "size_x": 1.8, "size_y": 1.8, "drill": 1.0 }
  ],
  "courtyard": { "w": 10.0, "h": 8.0 },
  "silkscreen": { "w": 8.0,  "h": 6.5 }
}
```

Then in `SchematicImporter.footprint()`:
```js
const fp = await fetch(`/pcb/footprints/${packageName}.json`).then(r => r.json());
```

### Footprints needed for the existing library

| Package | Parts that use it |
|---|---|
| 0402 | RESISTOR, CAPACITOR, INDUCTOR |
| 0805 | CAPACITOR (larger values) |
| SOT-23 | BC547 (NPN), IRF540N (NMOS) |
| TO-220 | LM7805, IRF540N |
| TO-92 | BC547 |
| DIP-28 | ATMEGA328P |
| TQFP-32 | ATMEGA328P (alt package) |
| QFN-38 | ESP32-WROOM-32 (module footprint) |
| SOIC-8 | LM358, NE555, AMS1117-3.3 |
| TO-252 (DPAK) | AMS1117-3.3 (alt) |
| SO-8 | LM358 (SOIC variant) |
| SOIC-16 | DRV8833 |
| MultiWatt-15 | L298N |
| SOT-23-5 | LM358 (tiny variant) |
| MCLP-3x3 (QFN-8) | PMA3_83LNW, QPA9510 (RF amps) |

---

## Design Rules

Stored in global `DR` object in `pcb.html`:

```js
let DR = {
  minTraceWidth: 0.15,  // mm — DRC error if trace narrower
  traceWidth:    0.25,  // mm — default route width
  clearance:     0.20,  // mm — copper-to-copper clearance
  viaSize:       1.0,   // mm — via annular ring outer diameter
  viaDrill:      0.6,   // mm — via drill diameter
  edgeClearance: 0.5,   // mm — copper to board edge
  copperWeight:  1.0    // oz — used in Gerber aperture comments
};
```

These are editable via the Layer Manager modal. DRC runs them against:
- All trace widths
- All pad-to-pad clearances
- Unrouted net count
- Trace proximity to board edge

---

## Layers

| Layer key | Display color | Purpose |
|---|---|---|
| `Edge.Cuts` | `#ffee00` | Board outline polygon |
| `F.Cu` | `#cc6633` | Front copper (routing, pads) |
| `B.Cu` | `#4466ee` | Back copper |
| `F.SilkS` | `#cccccc` | Front silkscreen (ref des, outlines) |
| `B.SilkS` | `#777777` | Back silkscreen |
| `Ratsnest` | `#337733` | Unrouted connection lines (virtual) |

Traces and vias are assigned to a named layer (`"F.Cu"` or `"B.Cu"`).
Pads on through-hole components appear on both copper layers.
SMD pads appear on the component's `layer` side only.

---

## Auto-Router Notes

Current implementation (`AutoRouter` class):
- **Algorithm**: Lee wave propagation (BFS) on a 0.25mm grid
- **Single layer**: only routes on `F.Cu`
- **MST ordering**: routes shortest connections first (Prim's MST)
- **Obstacle marking**: existing traces + pads + clearance margin are blocked cells
- **No rip-up and retry**: if a path fails it stays unrouted
- **Performance**: ~10ms per connection for a 50×35mm board; 20 connections ≈ 200ms

Improvements needed:
- Two-layer routing (flip to B.Cu and place via when F.Cu blocked)
- Push-and-shove (rip up lower-priority traces to make room)
- 45° diagonal routing option
- Connection priority (power traces wider and first)

---

## Coordinate Systems

```
Schematic units (abstract):
  Origin = canvas top-left
  +X right, +Y down
  Grid = 20 units
  SNAP = 10 units (half-grid)
  Component position = multiples of 20

PCB mm coordinates:
  Origin = board top-left corner
  +X right, +Y down (same orientation as schematic)
  Units = millimeters
  Grid = configurable (default 0.5mm)

Conversion (SchematicImporter):
  normalize = (comp.x - minX) / (maxX - minX)   → [0..1]
  pcb.x = margin + normalize * usableWidth         → mm
  Scale factor ≈ boardWidth / schematicSpan / 20  ≈ 0.1 mm/unit
```

---

## Server Integration

`server.js` has no PCB-specific routes. The PCB editor:
- Reads projects via `GET /api/projects` and `GET /api/projects/:id`
- Reads component library profiles via `GET /api/library/:slug`
- Has no server-side save (saves locally via browser download only)

Future server routes to add:
```
GET  /api/pcb-boards          → list saved PCB boards
POST /api/pcb-boards          → save a PCB board JSON
GET  /api/pcb-boards/:id      → load a PCB board JSON
GET  /pcb/footprints/:name    → serve footprint JSON files
GET  /pcb.html                → PCB editor (already works)
```

---

## How to Navigate to the PCB Editor

Currently there is no link from the schematic editor (`index.html`) to `pcb.html`.
The PCB editor header has a `← Schematic` link pointing to `/`.

To add a navigation button in the schematic, add a toolbar button in `index.html`:
```html
<a href="/pcb.html" target="_blank" class="tbtn">🔲 PCB</a>
```

---

## Known Issues in Current `pcb.html`

1. **IC footprints missing** — ICs placed with zero pads. Visible as empty components.
2. **Net labels ignored** — `project.labels` not read by importer; nets split incorrectly.
3. **VCC net always named "VCC"** — does not use comp.value (`"5V"`, `"3V3"`).
4. **No board outline editor** — board size only settable at import time.
5. **Auto-router single-layer** — cannot route dense boards.
6. **KiCad export is a stub** — generates `.kicad_pcb` with components but no proper KiCad footprint refs.
7. **Zones not filled** — copper zones are drawn as outlines, not flood-filled.
8. **No component rotation on canvas** — all components placed at 0° regardless of schematic rotation.
9. **Silkscreen generated from courtyard** — not from actual silkscreen layer data.
10. **No persistence** — no server save; user must manually download JSON and re-import.

---

## Recommended Build Order

If starting from scratch or significantly reworking `pcb.html`:

1. **Footprint library** — create `pcb/footprints/*.json` for all packages in the component library
2. **IC footprint loading** — fetch profile → match package → apply real pad geometry
3. **Net label support** — read `project.labels` in importer, merge nets by name
4. **VCC value propagation** — use `comp.value` for power net names
5. **Server persistence** — add `/api/pcb-boards` routes to `server.js`
6. **Navigation link** — add PCB button to schematic toolbar
7. **Board outline editor** — drag-to-resize or numeric board dimension inputs
8. **Two-layer auto-router** — add B.Cu routing + via insertion
9. **Zone fill** — implement copper pour flood fill algorithm
10. **Component rotation** — respect `rotation_quarters` from schematic during placement
