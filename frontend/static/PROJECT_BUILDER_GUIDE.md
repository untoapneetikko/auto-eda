# Project Builder — Claude Code Instructions

When you receive a gen ticket with `type: "build-project"`, your job is to design and save a complete schematic project. This guide is everything you need.

---

## Overview

1. Read the user prompt from the gen ticket
2. Identify required components from the library
3. **Check for `example_circuit`** — if the main IC's profile has one, use it directly (see Step 2b)
4. Otherwise, **POST a netlist** to `/api/build-from-netlist` — the server handles all placement and wiring
5. Update the tracking issue: set observations to `PROJECT_ID:xxx`, status to `done`, then unlock it

**Do NOT compute coordinates or wire positions yourself.**

---

## Step 2b — Use the Example Circuit (PREFERRED for user-uploaded ICs)

If the main IC is from the user library, **always fetch its profile first** and check for `example_circuit`:

```bash
curl -s http://localhost:3030/api/library/PMA3_83LNW
```

If `example_circuit` exists (has `components` and `wires`), use it directly — it was hand-crafted from the datasheet and is always better than a generated circuit.

Strip internal fields and POST straight to `/api/projects`:

```bash
# Read profile, extract example_circuit, clean fields, POST as project
curl -s http://localhost:3030/api/library/SLUG | node -e "
const p=JSON.parse(require('fs').readFileSync(0,'utf8'));
const ec=p.example_circuit;
if (!ec) { console.log('NO_EXAMPLE'); process.exit(1); }
const keep=['id','slug','symType','designator','value','x','y','rotation'];
const components=ec.components.map(c=>{const o={};keep.forEach(k=>{if(c[k]!==undefined)o[k]=c[k];});return o;});
const wires=ec.wires.filter(w=>w.points.length>=2&&(w.points[0].x!==w.points[1].x||w.points[0].y!==w.points[1].y)).map(w=>({id:w.id,points:w.points.map(p=>({x:p.x,y:p.y}))}));
const labels=(ec.labels||[]).map(l=>({id:l.id,name:l.name,x:l.x,y:l.y,rotation:l.rotation||0}));
process.stdout.write(JSON.stringify({name:'PROJECT_NAME',components,wires,labels}));
" | curl -s -X POST http://localhost:3030/api/projects -H "Content-Type: application/json" -d @-
```

This gives a project ID immediately. Use it to complete the ticket.

---

## Step 1 — Read the Library

```bash
curl -s http://localhost:3030/api/library
```

Each entry has `slug`, `part_number`, `description`, `symbol_type`.

For user-uploaded ICs, fetch the profile to get pin names:

```bash
curl -s http://localhost:3030/api/library/PMA3_83LNW
```

---

## Step 2 — Choose Components

**Built-in slugs (always available):**

| slug | Description |
|------|-------------|
| RESISTOR | Generic resistor |
| CAPACITOR | Generic capacitor |
| CAPACITOR_POL | Polarised capacitor |
| INDUCTOR | Generic inductor |
| VCC | Power supply rail |
| GND | Ground |
| DIODE | Generic diode |
| LED | LED |
| LM7805 | 5V linear regulator |
| AMS1117-3.3 | 3.3V LDO regulator |
| NE555 | 555 timer |
| 2N2222 | NPN transistor |
| BC547 | NPN transistor |
| IRF540N | N-channel MOSFET |
| LM358 | Dual op-amp |
| L298N | Dual H-bridge motor driver |
| DRV8833 | 2-channel motor driver |
| ATMEGA328P | 8-bit AVR MCU |
| ESP32_WROOM_32 | WiFi+BT SoC module |

Any slug from the user's library can also be used.

---

## Step 3 — Build with `/api/build-from-netlist`

POST your component list and net connections. The server computes all positions, port coordinates, and wire routing automatically.

### Request format

```json
{
  "name": "Project Name",
  "components": [
    { "id": "u1",   "slug": "LM7805",    "value": "LM7805", "designator": "U1" },
    { "id": "c1",   "slug": "CAPACITOR", "value": "100nF",  "designator": "C1" },
    { "id": "c2",   "slug": "CAPACITOR", "value": "100nF",  "designator": "C2" },
    { "id": "vcc1", "slug": "VCC",       "value": "12V",    "designator": "VCC1" },
    { "id": "gnd1", "slug": "GND",       "designator": "GND1" },
    { "id": "gnd2", "slug": "GND",       "designator": "GND2" },
    { "id": "gnd3", "slug": "GND",       "designator": "GND3" }
  ],
  "nets": [
    { "name": "VIN",  "pins": ["u1.IN",  "c1.1", "vcc1.VCC"] },
    { "name": "GND",  "pins": ["u1.GND", "c1.2", "gnd1.GND"] },
    { "name": "VOUT", "pins": ["u1.OUT", "c2.1"] },
    { "name": "VOUT_GND", "pins": ["c2.2", "gnd2.GND"] }
  ]
}
```

### Pin reference syntax

| Format | Meaning | Example |
|--------|---------|---------|
| `compId.PIN_NAME` | IC pin by name | `u1.IN`, `u1.GND`, `u1.OUT` |
| `compId.N` | IC pin by number (1-based) | `u1.1`, `u1.2` |
| `compId.P1` / `compId.P2` | Passive port key | `c1.P1`, `r1.P2` |
| `compId.1` / `compId.2` | Passive pin by number | `c1.1` = P1, `c1.2` = P2 |
| `vcc1.VCC` | VCC port | `vcc1.VCC` |
| `gnd1.GND` | GND port | `gnd1.GND` |

For user-uploaded ICs, use exact pin names from the profile (e.g. `u1.RF-IN`, `u1.RF-OUT/DC-IN`).

### Send the request

```bash
curl -s -X POST http://localhost:3030/api/build-from-netlist \
  -H "Content-Type: application/json" \
  -d '{...}'
```

Response: `{ "id": "mml...", "project": {...} }` — use the `id`.

---

## Step 4 — Worked Example: 5V Linear Regulator

LM7805 pins: `IN` (pin 1), `GND` (pin 2), `OUT` (pin 3).

```bash
curl -s -X POST http://localhost:3030/api/build-from-netlist \
  -H "Content-Type: application/json" \
  -d '{
  "name": "5V Linear Regulator",
  "components": [
    {"id":"u1",   "slug":"LM7805",    "value":"LM7805", "designator":"U1"},
    {"id":"c1",   "slug":"CAPACITOR", "value":"100nF",  "designator":"C1"},
    {"id":"c2",   "slug":"CAPACITOR", "value":"100nF",  "designator":"C2"},
    {"id":"vcc1", "slug":"VCC",       "value":"12V",    "designator":"VCC1"},
    {"id":"gnd1", "slug":"GND",       "designator":"GND1"},
    {"id":"gnd2", "slug":"GND",       "designator":"GND2"},
    {"id":"gnd3", "slug":"GND",       "designator":"GND3"}
  ],
  "nets": [
    {"name":"VIN",      "pins":["u1.IN",  "c1.1",  "vcc1.VCC"]},
    {"name":"GND",      "pins":["u1.GND", "c1.2",  "gnd1.GND"]},
    {"name":"VOUT",     "pins":["u1.OUT", "c2.1"]},
    {"name":"VOUT_GND", "pins":["c2.2",   "gnd2.GND"]}
  ]
}'
```

---

## Step 5 — Update the Tracking Issue

```bash
# Mark done with project ID in observations
curl -s -X PUT http://localhost:3030/api/issues/ISSUE_ID \
  -H "Content-Type: application/json" \
  -d '{"status":"done","observations":"PROJECT_ID:THE_PROJECT_ID"}'

# Unlock
curl -s -X POST http://localhost:3030/api/issues/ISSUE_ID/unlock
```

The frontend polls the issue and when it sees `observations` containing `PROJECT_ID:xxx`, it offers the user a link to open the project.

---

## Tips

- **One GND symbol per isolated ground node** — if C1 bottom and C2 bottom are both on the same GND net, one GND symbol is enough. Use separate GND symbols only when they connect to different net branches.
- **Always include VCC and GND** — never leave power pins floating.
- **Bypass caps belong on every power pin** — at least one cap between supply and GND.
- **For RF circuits** (amplifiers, LNAs): include input/output coupling caps and bias inductors per the datasheet application circuit.
- **For user-uploaded ICs** — fetch the profile first to get exact pin names, then use those names in your nets.
- **Example circuit = PCB module** — if you used `example_circuit` to build the schematic, note it in observations so the PCB stage knows to treat that sub-circuit as a locked placement module, not scatter its components freely.
- **Be conservative** — a clean 5-component circuit beats an ambitious broken one.
