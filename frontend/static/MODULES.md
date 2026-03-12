# Schematic Designer — Module Map for Parallel Agents

This file is the definitive guide for parallel agent work. Each module has clear file boundaries.
**Two agents must never edit overlapping line ranges in the same file at the same time.**

Before starting work, find your module below. That is the only code you touch.
When you create an improvements ticket, always set `category` to the module name.

---

## Module Index

| Module | File(s) | Line Range | Category Tag |
|--------|---------|------------|--------------|
| [Library / Datasheets](#1-library--datasheets) | `server.js` + `index.html` | see below | `library` |
| [Footprint Editor](#2-footprint-editor) | `server.js` + `index.html` | see below | `footprint` |
| [Schematic Editor](#3-schematic-editor) | `index.html` | 4418–7565 | `schematic` |
| [PCB Editor](#4-pcb-editor) | `pcb.html` | all | `pcb` |
| [Layout Example Tab](#5-layout-example-tab) | `index.html` | 2009–2108 | `layout` |
| [Gen Tickets](#6-gen-tickets) | `server.js` + `index.html` | see below | `generate` |
| [Projects / Export](#7-projects--export) | `server.js` | 606–750 | `projects` |
| [Issue Tracker + Agents](#8-issue-tracker--agents) | `server.js` | 752–1087 | `other` |
| [Symbol Renderer](#9-symbol-renderer) | `index.html` | 2290–4417 | `schematic` |
| [App Shell / Nav / Init](#10-app-shell--nav--init) | `index.html` | 1–932 | `other` |

---

## 1. Library / Datasheets

**Purpose:** Upload PDFs, parse to profiles, display component library, pin editor, example circuit tab.

### server.js lines 35–231
```
35   // ── Upload PDF
111  // ── Get library index
116  // ── Create new component manually
150  // ── Get single profile
160  // ── Get raw text
167  // ── Save correction
181  // ── Save designator prefix
192  // ── Save symbol type
203  // ── Save modified pins (pin editor)
232  ← stop here (footprint starts)
```

### server.js: key endpoints
| Method | URL | Purpose |
|--------|-----|---------|
| POST | `/api/upload` | Upload + extract PDF text |
| GET | `/api/library` | All component profiles index |
| GET | `/api/library/:slug` | Single profile JSON |
| GET | `/api/library/:slug/raw` | Raw datasheet text |
| PUT | `/api/library/:slug/pins` | Save edited pin list |
| PUT | `/api/library/:slug/example_circuit` | Save example circuit |
| PUT | `/api/library/:slug/layout_example` | Save layout example |

### index.html lines 932–1804
```
932   // ── Library (loadLibrary, renderLibrary, selectComponent)
979   // ── Profile Render (renderProfile, all profile tabs)
1368  // ── Tab switching (switchProfileTab)
1398  // ── FootprintEditor — SVG viewer/editor   ← shared with Footprint module
```

### Profile JSON schema (canonical)
File: `library/[SLUG]/profile.json`
Reference: `DATASHEET_PARSING_GUIDE.md` for full schema.
Key fields: `part_number`, `pins[]`, `required_passives[]`, `example_circuit`, `layout_example`, `footprint`, `symbol_type` (always `"ic"`).

### Parse workflow (for agents doing ticket work)
1. `GET /api/library/:slug/raw` → read raw datasheet text
2. Extract pins, passives, application circuits
3. `PUT /api/library/:slug` body = updated profile JSON
4. Mark gen-ticket done: `PUT /api/gen-tickets/:id` `{"status":"done"}`

---

## 2. Footprint Editor

**Purpose:** Interactive SVG pad viewer, footprint generation from datasheet, footprint library CRUD.

### server.js lines 232–596
```
232  // ── Footprint API (GET/PUT/DELETE /api/footprints/:name)
267  // ── Rule-based footprint generator
366  // ── Footprint shape generators
597  ← stop here (PDF serve starts)
```

### server.js: key endpoints
| Method | URL | Purpose |
|--------|-----|---------|
| GET | `/api/footprints` | All footprints |
| GET | `/api/footprints/:name` | Single footprint JSON |
| PUT | `/api/footprints/:name` | Create/update footprint |
| DELETE | `/api/footprints/:name` | Delete footprint |
| PUT | `/api/library/:slug/footprint` | Assign footprint to component |

### index.html lines 1398–1804
```
1398  // ── FootprintEditor class (SVG pad viewer/editor)
1582  // ── Footprint Tool (fpGenerate, buildFootprintPrompt, initFootprintTab)
1805  ← stop here (Gen Tickets starts)
```

### Footprint JSON schema
```json
{
  "name": "QFN-16-3x3",
  "description": "...",
  "pads": [
    { "number": "1", "name": "PIN1", "x": 0, "y": 0,
      "type": "smd", "shape": "rect", "size_x": 0.6, "size_y": 1.5 }
  ],
  "courtyard": { "x": -2, "y": -2, "w": 4, "h": 4 }
}
```
All coordinates in mm, relative to component center (0,0).

---

## 3. Schematic Editor

**Purpose:** Canvas-based schematic drawing. Drag components, draw wires, net labels, save/load.

### index.html lines 4418–7565
```
4418  // ── SchematicEditor class
4449  //   Setup (constructor, opts)
4490  //   Project (load, save, clear)
4566  //   Coords (pan, zoom)
4585  //   Symbol defs (_def, _ports, _bbox, _icLayout)
4639  //   Hit tests
4677  //   Events (mouse, keyboard)
5063  //   Tools (select, wire, place)
5107  //   Component ops (add, move, delete)
5191  //   Port-to-port wire snap
```

### Key globals
- `editor` — main schematic instance (on `#schematic-canvas`)
- `appCircuitEditor` — embedded preview instance (on `#app-circuit-canvas`, `noEvents:true`)
- `profileCache` — `{ [slug]: profileObj }` — must be populated before placing IC components
- `SYMDEFS` — symbol geometry definitions (line 4313)
- Grid = 20px. All x/y must be multiples of 20.

### Port position formula
Apply component rotation to SYMDEFS port offsets:
- `r=0`: `(dx, dy)` as-is
- `r=1`: `(dy, -dx)`
- `r=2`: `(-dx, -dy)`
- `r=3`: `(-dy, dx)`

SYMDEFS port offsets:
- `resistor`: P1=(-30,0), P2=(+30,0)
- `capacitor`: P1=(0,-20), P2=(0,+20)
- `inductor`: P1=(-40,0), P2=(+40,0)
- `vcc`: port=(0,+20)
- `gnd`: port=(0,-20)

### Circuit JSON format
```json
{
  "components": [
    { "id": "u1", "slug": "SLUG", "symType": "ic", "designator": "U1",
      "value": "PartName", "x": 320, "y": 290, "rotation": 0 }
  ],
  "wires": [
    { "id": "w1", "points": [{"x": 100, "y": 240}, {"x": 200, "y": 240}] }
  ],
  "labels": [
    { "id": "lbl1", "name": "VCC", "x": 100, "y": 100, "rotation": 0 }
  ]
}
```

---

## 4. PCB Editor

**Purpose:** Standalone PCB layout editor. Separate page, embedded as iframe in Layout tab.

### File: `pcb.html` (all lines ~2156)
Key sections inside:
- `PCBEditor` class — main editor
- `SchematicImporter` — converts schematic JSON to PCB board
- `AutoRouter` — Lee BFS single-layer autorouter
- `GerberExporter` — RS-274X format output
- `KiCadExporter` — .kicad_pcb S-expression format
- Layer Manager, DRC checker

### Embedding protocol
- Embedded at: `/pcb.html?embedded=1` (loaded as iframe)
- postMessage IN: `{ type: 'loadBoard', board: boardJSON, hideBoardOutline: true }`
- postMessage OUT: `{ type: 'boardSaved', board: boardJSON }`
- `hideBoardOutline: true` → suppresses Edge.Cuts background (used in Layout Example tab)

### PCB board JSON schema
Full reference: `PCB_JSON_FORMAT.md`
```json
{
  "version": "1.0", "title": "...",
  "board": { "width": 25, "height": 20, "units": "mm" },
  "components": [
    { "id": "U1", "ref": "U1", "value": "...", "footprint": "QFN-16-3x3",
      "x": 12.5, "y": 10, "rotation": 0, "layer": "F",
      "pads": [{ "number": "1", "name": "PIN1", "x": 0, "y": 0,
                 "type": "smd", "shape": "rect",
                 "size_x": 0.3, "size_y": 0.7, "net": "VCC" }] }
  ],
  "nets": [{ "name": "VCC", "pads": ["U1.1"] }],
  "traces": [{ "net": "VCC", "layer": "F.Cu", "width": 0.2,
               "segments": [{ "start": {"x":0,"y":0}, "end": {"x":1,"y":0} }] }],
  "vias": []
}
```

---

## 5. Layout Example Tab

**Purpose:** Shows a PCB layout inside the component profile, loaded from `profile.layout_example`.

### index.html lines 2009–2108
```
2009  // ── Layout Example tab
2102  ← stop here (App Circuit Renderer starts)
```

### Key function: `renderLayoutExample(slug, profile)`
- If `profile.layout_example` exists → postMessages it to the iframe
- Otherwise → shows empty state with "Generate Layout" button
- Calls `gtCreateTicket('layout', ...)` when button pressed

### Save endpoint
`PUT /api/library/:slug/layout_example` — body is the full board JSON

---

## 6. Gen Tickets

**Purpose:** Tracks AI code-generation tasks (build footprint, example circuit, PCB layout). Separate from improvements.

### server.js lines 849–900
```
849  // ── Gen Tickets API (GET/POST/PUT/DELETE /api/gen-tickets)
901  ← stop here
```

### index.html lines 1805–2008
```
1805  // ── Generate Tickets (gtLoad, gtRender, gtCreateTicket, showGenToast)
1806  //   buildFootprintPrompt
1807  //   buildExampleRebuildPrompt
1808  //   buildLayoutPrompt
1809  //   buildRebuildPrompt (datasheet type)
2009  ← stop here
```

### Gen ticket JSON schema
```json
{
  "id": 10,
  "type": "footprint|example|layout|datasheet",
  "slug": "COMPONENT_SLUG",
  "title": "Library → Component → PartName → Tab Name",
  "prompt": "Full instructions for Claude Code to execute...",
  "status": "pending|done",
  "created_at": "ISO timestamp"
}
```

### Ticket lifecycle
1. Created by UI (user clicks Rebuild button on profile tab)
2. Prompt built by `buildXxxPrompt()` function — includes datasheet text inline
3. Prompt ends with: `WHEN DONE — PUT http://localhost:3030/api/gen-tickets/:id Body: {"status":"done"}`
4. Agent reads ticket from **Generate Tickets** tab, executes, marks done
5. Result is saved to `profile.example_circuit` or `profile.layout_example` or `library/[SLUG]/footprint` field

---

## 7. Projects / Export

**Purpose:** Save/load full schematic projects, export design bundles.

### server.js lines 606–750
```
606  // ── Projects API (GET/POST/PUT/DELETE /api/projects)
645  // ── Export design bundle
684  // ── Import design bundle
714  // ── PCB Boards API
752  ← stop here
```

### index.html lines 4139–4312
```
4139  // ── Projects & Tabs (loadProject, saveProject, renderProjectList)
4312  ← stop here
```

---

## 8. Issue Tracker + Agents

**Purpose:** Improvements/bug tracking (improvements.json), live agent registry (in-memory).

### server.js lines 752–1087
```
752   // ── Issues / Improvements API
821   // ── Lock / Unlock a ticket
849   // ← Gen Tickets starts (separate module)
901   // ← resume Issues region...
951   // ── SSE for live updates
1007  // ── Agent Registry (register, ping, unregister, list)
1087  ← end of file
```

### Issues API
| Method | URL | Purpose |
|--------|-----|---------|
| GET | `/api/issues` | All issues |
| POST | `/api/issues` | Create issue |
| PUT | `/api/issues/:id` | Update issue |
| POST | `/api/issues/:id/lock` | Lock ticket |
| POST | `/api/issues/:id/unlock` | Unlock ticket |

### Agents API
| Method | URL | Purpose |
|--------|-----|---------|
| POST | `/api/agents/register` | Register agent |
| POST | `/api/agents/:id/ping` | Heartbeat |
| DELETE | `/api/agents/:id` | Unregister |
| GET | `/api/agents` | List all agents |

---

## 9. Symbol Renderer

**Purpose:** Canvas 2D drawing routines for schematic symbols (resistor, capacitor, IC box, etc.).

### index.html lines 2290–4417
```
2290  // ── Symbol Renderer (drawSymbol dispatcher)
2343  // ── Resistor
2368  // ── Capacitor
2405  // ── Inductor
2434  // ── VCC power symbol
2459  // ── GND symbol
2481  // ── Amplifier triangle
2583  // ── Op-Amp triangle
2654  // ── Generic IC rectangle (_sIC)
2724  // ── Helpers
2758  // ── Symbol Editor Canvas
3020  // ── Pin list (HTML rows)
3109  // ── Symbol rendering (SVG-based editor)
3189  // ── App circuit using SchematicEditor
3220  // ── PDF panel
3243  // ── Pin Editor modal
3313  // ── Schematic Info Panel
3442  // ── Embedded editor info panel
3765  // ── Component Builder
3923  // ── Section switching
3989  // ── Schematic palette
4105  // ── PDF viewer
4139  ← Projects & Tabs starts
```

---

## 10. App Shell / Nav / Init

**Purpose:** HTML structure, CSS, top nav, section routing, SSE, startup init.

### index.html lines 1–931
```
1     HTML head, CSS styles
402   <!-- ── Top Navigation -->
845   // ── SSE live updates
854   // ── Designer mode toggle
858   // ── Init (applyDesignerMode, loadLibrary)
869   // ── Upload handlers
932   ← Library module starts
```

---

## Rules for Parallel Agents

### Before starting
1. Register yourself: `POST /api/agents/register`
2. Read open gen-tickets: `GET /api/gen-tickets?status=pending`
3. Read open improvements: `GET /api/issues`
4. Lock your ticket before writing any code

### Non-overlapping rule
If your ticket requires changing both `server.js` AND `index.html`, that is fine — but note it in your registration so other agents know you're touching both. Never edit the same line range as another active agent.

### What to avoid
- **Don't touch `CLAUDE.md`** while another agent is active (it's read by all agents)
- **Don't change `library/*/profile.json`** while another agent is working on the same slug
- **Don't restart the server** (port 3030) without pinging all other agents first

### Handoff convention
When your ticket's work produces data another agent needs:
1. Save it via API (`PUT /api/library/:slug/...`)
2. Write the output location in your ticket observations
3. Mark ticket done: `PUT /api/gen-tickets/:id {"status":"done"}`

The next agent reads from the API, not from your context.
