# PCB JSON Format — Schematic Designer Export

This document describes the JSON file produced by the **⬇ PCB JSON** button in the
Schematic tab of the Schematic Designer tool. A PCB editor agent reads this file to
place components on a board and route nets.

---

## How the File Is Generated

The schematic editor stores a canvas with placed components and drawn wires.
When the user clicks **⬇ PCB JSON**, the exporter:

1. Iterates every placed component and collects its pin positions (world coordinates).
2. Runs a **Union-Find netlist extraction** over all wire segments and pin positions:
   - Every wire is a polyline of `{x, y}` points; all points on one wire are in the same net.
   - Two wires sharing a point (T-junction, crossing with dot) are merged into the same net.
   - A component pin is added to a net when its world-coordinate position matches a wire point within ±10 grid units (tolerance = `grid_units / 2`).
3. Names each net:
   - If the net contains a **VCC power symbol**, the net name is the symbol's `value` field (e.g. `"5V"`, `"3V3"`). Falls back to `"VCC"` if value is blank.
   - If the net contains a **GND power symbol**, the net name is `"GND"`.
   - If a pin name matches a known power name (`VCC`, `VDD`, `3V3`, `5V`, `12V`, `VIN`, `VOUT`, `PWR`, `POWER`, `AVCC`, `DVCC`, `VBAT`, `VBUS`, or starts with `VCC`/`VDD`/`GND`), that name is used.
   - Otherwise: `NET_1`, `NET_2`, … (sequential, arbitrary order).
4. Collects **unconnected pins**: any pin on a non-power/non-GND component that has no net.
5. Downloads `<project_name>_pcb.json`.

---

## Top-Level Structure

```json
{
  "format":          "schematic-designer-pcb-v1",
  "project_name":    "LM7805 Regulator",
  "generated_at":    "2026-03-08T21:00:00.000Z",
  "grid_units":      20,
  "components":      [ ...component objects... ],
  "nets":            [ ...net objects... ],
  "unconnected_pins": [ "R1.P2", "U1.NC" ],
  "stats": {
    "component_count": 6,
    "net_count":        4,
    "unconnected_count": 1
  }
}
```

| Field              | Type     | Description |
|--------------------|----------|-------------|
| `format`           | string   | Always `"schematic-designer-pcb-v1"`. Bump minor version for backwards-compatible additions, major for breaking changes. |
| `project_name`     | string   | The schematic project name as shown in the editor toolbar. |
| `generated_at`     | string   | ISO 8601 timestamp of export. |
| `grid_units`       | number   | Canvas grid size in abstract schematic units (currently `20`). Component `position` coordinates are multiples of this value. **Not millimeters.** |
| `components`       | array    | One object per placed component (including VCC/GND power symbols). |
| `nets`             | array    | One object per electrical net. Every connected set of pins appears exactly once. |
| `unconnected_pins` | string[] | Pins that have no connected wire. Format: `"DESIGNATOR.PIN_NAME"`. Empty array `[]` means fully connected. |
| `stats`            | object   | Convenience counts — do not use for logic, derive from arrays instead. |

---

## Component Object

```json
{
  "designator":       "U1",
  "slug":             "LM7805",
  "symbol_type":      "ic",
  "value":            "LM7805",
  "part_number":      "LM7805",
  "package":          "TO-220",
  "description":      "5V 1A positive linear voltage regulator",
  "pins": [
    {
      "name":   "INPUT",
      "number": 1,
      "type":   "power",
      "net":    "VIN"
    },
    {
      "name":   "GND",
      "number": 2,
      "type":   "gnd",
      "net":    "GND"
    },
    {
      "name":   "OUTPUT",
      "number": 3,
      "type":   "power_out",
      "net":    "NET_1"
    }
  ],
  "position":          { "x": 300, "y": 200 },
  "rotation_quarters": 0
}
```

### Component Fields

| Field              | Type          | Description |
|--------------------|---------------|-------------|
| `designator`       | string        | Reference designator: `R1`, `C3`, `U1`, `VDD`, `GND1`, etc. Unique within the schematic. |
| `slug`             | string        | Library key used to look up this part in `datasheets/<slug>/profile.json`. Matches the folder name under `datasheets/`. |
| `symbol_type`      | string        | Schematic symbol type — see **Symbol Types** table below. |
| `value`            | string        | Component value as shown on the schematic: `"10k"`, `"100nF"`, `"LM7805"`, `"5V"`, `""`. May be empty. |
| `part_number`      | string        | Exact part number from the library profile. Falls back to `slug` if no profile loaded. |
| `package`          | string\|null  | First entry from the library profile's `package_types` array, e.g. `"TO-220"`, `"16-Pad 3x3mm QFN"`. `null` if no profile or no package listed (passives, power symbols). |
| `description`      | string\|null  | One-sentence description from the library profile. `null` if unavailable. |
| `pins`             | array         | One entry per pin/port — see **Pin Object** below. Order matches the schematic symbol's port order (not necessarily pin number order). |
| `position`         | object        | `{ x, y }` — center of the component in schematic canvas coordinates. **Abstract units, not millimeters.** To convert to a relative layout, subtract the minimum x/y across all components. |
| `rotation_quarters`| number        | `0`=0°, `1`=90°CW, `2`=180°, `3`=270°CW. Integer 0–3. |

### Symbol Types

| `symbol_type`   | Description                                    | Typical pin names |
|-----------------|------------------------------------------------|-------------------|
| `resistor`      | Resistor                                       | `P1`, `P2` |
| `capacitor`     | Non-polarized capacitor                        | `P1`, `P2` |
| `capacitor_pol` | Polarized capacitor (electrolytic, tantalum)   | `+`, `-` |
| `inductor`      | Inductor / ferrite bead                        | `P1`, `P2` |
| `vcc`           | Power supply symbol (no physical footprint)    | `VCC` |
| `gnd`           | Ground symbol (no physical footprint)          | `GND` |
| `diode`         | Diode                                          | `A` (anode), `K` (cathode) |
| `led`           | LED                                            | `A`, `K` |
| `npn`           | NPN BJT transistor                             | `B`, `C`, `E` |
| `pnp`           | PNP BJT transistor                             | `B`, `E`, `C` |
| `nmos`          | N-channel MOSFET                               | `G`, `D`, `S` |
| `pmos`          | P-channel MOSFET                               | `G`, `D`, `S` |
| `amplifier`     | RF/general amplifier (triangle symbol)         | `IN`, `OUT`, `GND` |
| `opamp`         | Operational amplifier                          | `+`, `-`, `OUT` |
| `ic`            | Generic IC (DIP-style rectangle, from library) | Named per profile pins |

**Power symbols (`vcc`, `gnd`) have no physical PCB footprint.** They are net-naming
artifacts. Skip them when placing components on the PCB. Their pin connections appear
in `nets` under the correct net name.

---

## Pin Object

```json
{
  "name":   "VCC1",
  "number": 1,
  "type":   "power",
  "net":    "3V3"
}
```

| Field    | Type          | Description |
|----------|---------------|-------------|
| `name`   | string        | Pin name as defined in the library profile or schematic symbol. For passives with no profile: `P1`, `P2` (resistor, inductor), `+`/`-` (polarized cap). For passives without polarity: `P1`, `P2`. For ICs: the exact name from the datasheet pin table. |
| `number` | number\|null  | Physical pin number from the library profile. `null` for components without a loaded profile (seed passives, power symbols). **Use this to map to a footprint pad.** |
| `type`   | string\|null  | Electrical type from the library profile. One of: `power`, `gnd`, `input`, `output`, `passive`, `bidirectional`. `null` if no profile loaded. |
| `net`    | string\|null  | Net name this pin belongs to. `null` means the pin is unconnected (also listed in `unconnected_pins`). |

---

## Net Object

```json
{
  "name": "GND",
  "pins": [
    "U1.GND",
    "C1.P2",
    "C2.-",
    "GND1.GND",
    "GND2.GND"
  ]
}
```

| Field  | Type     | Description |
|--------|----------|-------------|
| `name` | string   | Net name. Power nets: `GND`, `VCC`, `5V`, `3V3`, `VIN`, etc. Signal nets: `NET_1`, `NET_2`, … |
| `pins` | string[] | All pins on this net. Format: `"DESIGNATOR.PIN_NAME"`. Power symbols (`GND1.GND`, `VDD.VCC`) are included — filter them out when building ratsnest by checking `symbol_type === 'vcc'` or `'gnd'` on the component. |

**Net names are not guaranteed unique** if the user placed two VCC symbols with different
values (e.g. `"5V"` and `"3V3"`) on the same wire. In practice the netlist extractor
names them from the first power symbol found. Treat net names as opaque identifiers
for routing purposes.

---

## Coordinate System

```
Origin (0,0) = top-left of schematic canvas at default zoom/pan.
+X = right
+Y = down   (screen coordinates — Y increases downward)
Grid = 20 units (grid_units field)
All component positions are snapped to the grid.
```

**Component positions are schematic layout hints, not PCB placement coordinates.**
The PCB editor should use them as a rough starting arrangement and auto-place/route
from there. Relative spatial relationships (which components are near each other)
are preserved and meaningful — components connected by short wires in the schematic
are intentionally close together.

To normalize coordinates to start at (0, 0):
```js
const minX = Math.min(...components.map(c => c.position.x));
const minY = Math.min(...components.map(c => c.position.y));
components.forEach(c => {
  c.position.x -= minX;
  c.position.y -= minY;
});
```

---

## Worked Example

Schematic: LM7805 regulator with input/output caps.

```
VIN ──[C1]──┬──[L7805 INPUT]──[LM7805]──[OUTPUT]──┬──[C2]── 5V
             │                                      │
            GND                                    GND
```

```json
{
  "format": "schematic-designer-pcb-v1",
  "project_name": "LM7805 Regulator",
  "generated_at": "2026-03-08T21:00:00.000Z",
  "grid_units": 20,
  "components": [
    {
      "designator": "VIN1",
      "slug": "VCC",
      "symbol_type": "vcc",
      "value": "VIN",
      "part_number": "VCC",
      "package": null,
      "description": "Power supply symbol — marks a named voltage rail on the schematic",
      "pins": [{ "name": "VCC", "number": null, "type": null, "net": "VIN" }],
      "position": { "x": 100, "y": 100 },
      "rotation_quarters": 0
    },
    {
      "designator": "C1",
      "slug": "CAPACITOR",
      "symbol_type": "capacitor",
      "value": "0.33µF",
      "part_number": "C",
      "package": null,
      "description": null,
      "pins": [
        { "name": "P1", "number": null, "type": null, "net": "VIN" },
        { "name": "P2", "number": null, "type": null, "net": "GND" }
      ],
      "position": { "x": 160, "y": 200 },
      "rotation_quarters": 0
    },
    {
      "designator": "U1",
      "slug": "LM7805",
      "symbol_type": "ic",
      "value": "LM7805",
      "part_number": "LM7805",
      "package": "TO-220",
      "description": "5V 1A positive linear voltage regulator — fixed output, requires input ≥7V",
      "pins": [
        { "name": "INPUT",  "number": 1, "type": "power",     "net": "VIN"  },
        { "name": "GND",    "number": 2, "type": "gnd",       "net": "GND"  },
        { "name": "OUTPUT", "number": 3, "type": "power_out", "net": "5V"   }
      ],
      "position": { "x": 300, "y": 200 },
      "rotation_quarters": 0
    },
    {
      "designator": "C2",
      "slug": "CAPACITOR",
      "symbol_type": "capacitor",
      "value": "0.1µF",
      "part_number": "C",
      "package": null,
      "description": null,
      "pins": [
        { "name": "P1", "number": null, "type": null, "net": "5V"  },
        { "name": "P2", "number": null, "type": null, "net": "GND" }
      ],
      "position": { "x": 440, "y": 200 },
      "rotation_quarters": 0
    },
    {
      "designator": "VCC1",
      "slug": "VCC",
      "symbol_type": "vcc",
      "value": "5V",
      "part_number": "VCC",
      "package": null,
      "description": "Power supply symbol — marks a named voltage rail on the schematic",
      "pins": [{ "name": "VCC", "number": null, "type": null, "net": "5V" }],
      "position": { "x": 500, "y": 100 },
      "rotation_quarters": 0
    },
    {
      "designator": "GND1",
      "slug": "GND",
      "symbol_type": "gnd",
      "value": "",
      "part_number": "GND",
      "package": null,
      "description": "Ground symbol",
      "pins": [{ "name": "GND", "number": null, "type": null, "net": "GND" }],
      "position": { "x": 160, "y": 300 },
      "rotation_quarters": 0
    },
    {
      "designator": "GND2",
      "slug": "GND",
      "symbol_type": "gnd",
      "value": "",
      "part_number": "GND",
      "package": null,
      "description": "Ground symbol",
      "pins": [{ "name": "GND", "number": null, "type": null, "net": "GND" }],
      "position": { "x": 440, "y": 300 },
      "rotation_quarters": 0
    }
  ],
  "nets": [
    {
      "name": "VIN",
      "pins": ["VIN1.VCC", "C1.P1", "U1.INPUT"]
    },
    {
      "name": "GND",
      "pins": ["C1.P2", "U1.GND", "C2.P2", "GND1.GND", "GND2.GND"]
    },
    {
      "name": "5V",
      "pins": ["U1.OUTPUT", "C2.P1", "VCC1.VCC"]
    }
  ],
  "unconnected_pins": [],
  "stats": {
    "component_count": 7,
    "net_count": 3,
    "unconnected_count": 0
  }
}
```

---

## Import Algorithm for PCB Editor

### Step 1 — Filter placeable components

```js
const placeable = data.components.filter(
  c => c.symbol_type !== 'vcc' && c.symbol_type !== 'gnd'
);
```

Power and ground symbols are net annotations only. They have no PCB footprint.

### Step 2 — Build the net → pin lookup

```js
const netPins = {};  // netName → ["DESIG.PINNAME", ...]
for (const net of data.nets) {
  netPins[net.name] = net.pins.filter(ref => {
    const des = ref.split('.')[0];
    const comp = data.components.find(c => c.designator === des);
    return comp && comp.symbol_type !== 'vcc' && comp.symbol_type !== 'gnd';
  });
}
```

### Step 3 — Assign footprints

Use `package` (primary), fall back to `slug` or `part_number` for lookup:

```js
for (const comp of placeable) {
  comp.footprint = lookupFootprint(comp.package ?? comp.part_number);
  // e.g. "TO-220" → "Package_TO_SOT_THT:TO-220_Vertical"
  // e.g. "16-Pad 3x3mm QFN" → "Package_DFN_QFN:QFN-16-1EP_3x3mm_P0.5mm_EP1.65x1.65mm"
}
```

### Step 4 — Place components

Use `position` as layout hints. Scale from schematic units to PCB mm:

```js
const SCALE = 0.1;  // 20 schematic units ≈ 2mm — tune as needed
for (const comp of placeable) {
  placePCBComponent({
    ref:       comp.designator,
    value:     comp.value,
    footprint: comp.footprint,
    x:         comp.position.x * SCALE,
    y:         comp.position.y * SCALE,
    rotation:  comp.rotation_quarters * 90,
  });
}
```

### Step 5 — Build ratsnest / assign pads to nets

```js
for (const comp of placeable) {
  for (const pin of comp.pins) {
    if (pin.net && pin.number !== null) {
      assignPadToNet(comp.designator, pin.number, pin.net);
    } else if (pin.net && pin.number === null) {
      // Passive component (no pin number) — match by pad order (pad 1 = first pin)
      const padIdx = comp.pins.indexOf(pin) + 1;
      assignPadToNet(comp.designator, padIdx, pin.net);
    }
    // pin.net === null → unconnected pad, leave unassigned
  }
}
```

---

## Known Limitations

| Limitation | Detail |
|------------|--------|
| **No pin numbers for passives** | Resistors, caps, inductors use `P1`/`P2` names; `pin.number` is `null`. The PCB editor must assign by pad order (P1 → pad 1, P2 → pad 2). |
| **Net names not always unique** | Two separate VCC symbols with the same label merge to the same net, which is correct. But two with different labels on the same wire produce ambiguous naming — last one found wins. |
| **No differential pairs** | Signal pairs are just two separate nets with no pairing metadata. |
| **No pin-swap information** | Component pins are in schematic symbol order, not necessarily physical pad order for multi-pad ICs. Always cross-reference with the library profile (`datasheets/<slug>/profile.json`) for pad ordering. |
| **Coordinates are schematic-relative** | No real-world scale. The PCB editor must define its own scale factor. `grid_units: 20` means all positions are multiples of 20. |
| **No board outline** | No PCB boundary, keep-out areas, or mechanical constraints are exported. |
| **Power symbols included in nets** | `nets[].pins` entries like `"GND1.GND"` refer to power symbols. Filter by checking `components[].symbol_type === 'vcc'` or `'gnd'`. |

---

## Cross-Reference: Library Profiles

For every IC component (`symbol_type === 'ic'`), the full datasheet profile is
available at:

```
datasheets/<slug>/profile.json
```

The profile contains:
- Full pin table with types, descriptions, requirements
- `required_passives` — bypass caps, pull-up resistors, etc. that must appear on the PCB
- `package_types` — all available packages
- `absolute_max` — voltage/current limits for DRC rules
- `common_mistakes` — design rules to check against

The PCB editor should load these profiles to run DRC checks (correct decoupling caps
placed, power pins in range, etc.) and to generate a full BOM.

```js
// Example: fetch profile for an IC
const res = await fetch(`/api/library/${comp.slug}`);
const profile = await res.json();
// profile.required_passives → verify these components exist in the schematic
// profile.pins[n].requirements → use for DRC rule strings
```
