# PCB Layout Example — Agent Instructions

**Read this file completely before writing any JSON. Every rule here is mandatory.**

---

## Step 0 — Clear existing layout first

```
DELETE http://localhost:3030/api/library/<slug>/layout_example
```

---

## Step 1 — Identify ALL components before placing anything

List every component that must appear in the layout:

1. **The IC itself** (U1)
2. **Every decoupling/bypass cap** — one per VCC/VDD/supply pin
3. **Every inductor** required (bias chokes, RF chokes, etc.)
4. **Every resistor** required (pull-ups, bias networks)
5. **Every component in the datasheet's "Typical Application" or "Recommended Layout"**

**All of these must appear in `components[]`. A layout that omits any is wrong.**

---

## Package courtyard dimensions

Use these to compute spacing. No two courtyards may overlap.

### IC / active packages

| Footprint | Courtyard half X | Courtyard half Y |
|-----------|-----------------|-----------------|
| QFN-12-3x3 | 2.0 | 2.0 |
| QFN-16-3x3 | 2.45 | 2.45 |
| MCLP-4 | 1.4 | 1.2 |
| MCLP-8 | 1.4 | 1.1 |
| MCLP-12 | 1.6 | 1.4 |
| DFN-6-1.8x1.8 | 1.3 | 1.225 |
| DFN-8-2x2 | 1.4 | 1.325 |
| SOT-23 | 1.6 | 1.1 |
| SOT-23-5 | 1.7 | 1.5 |
| SOIC-8 | 4.0 | 2.8 |
| LTCC-0402RF | 1.0 | 0.5 |
| LTCC-4PAD-2x1.6 | 1.3 | 1.1 |

### Passive packages

| Footprint | Courtyard half X | Courtyard half Y |
|-----------|-----------------|-----------------|
| 0201 | 0.54 | 0.3 |
| 0402 | 1.0 | 0.5 |
| 0603 | 1.5 | 0.6 |
| 0805 | 1.8 | 0.9 |

---

## Minimum safe center-to-center spacing

**Formula:** `min_distance = IC_courtyard_half + passive_courtyard_half + 0.25 mm`

The 0.25 mm margin ensures courtyard clearance is never violated.

### QFN-12-3x3 + 0402

| Direction | IC half | Cap half | Safety | **Minimum** |
|-----------|---------|----------|--------|-------------|
| Horizontal (left/right of IC) | 2.0 | 1.0 | 0.25 | **≥ 3.25 mm** |
| Vertical (above/below IC) | 2.0 | 0.5 | 0.25 | **≥ 2.75 mm** |

### QFN-16-3x3 + 0402

| Direction | IC half | Cap half | Safety | **Minimum** |
|-----------|---------|----------|--------|-------------|
| Horizontal | 2.45 | 1.0 | 0.25 | **≥ 3.70 mm** |
| Vertical | 2.45 | 0.5 | 0.25 | **≥ 3.20 mm** |

### General rule for any combination
```
min_X = IC_cy_half_X + passive_cy_half_X + 0.25
min_Y = IC_cy_half_Y + passive_cy_half_Y + 0.25
```

### Passive-to-passive spacing

For 0402 rotation=90 (top/bottom placement) — world X-extent is half Y = 0.5mm:
- Min X spacing between adjacent passives = 2 × 0.5 + 0.25 = **1.25 mm**

For 0402 rotation=0 (left/right placement):
- Min Y spacing between adjacent passives = 2 × 0.5 + 0.25 = **1.25 mm**

**Place passives as close together as allowed — do not use arbitrary large gaps.**

---

## Placement order

1. Decoupling/bypass caps first
2. RF chokes / bias chokes
3. DC-blocking caps
4. Input passives / matching networks

**Same-net components must be adjacent** (courtyard gap ≤ 0.25 mm between them). Never scatter same-net passives to opposite sides of the IC.

---

## Component orientation rules — follow every time

### Rule 1 — Single-axis alignment
Orient each passive so that **one pad aligns exactly** (same X or same Y) with the IC pin it connects to.
This allows a single straight horizontal or vertical trace — no dog-legs.

### Rule 2 — Connecting pad faces the IC
Place the connecting pad on the **side facing the IC pin**.

Example: IC VIN pin at world (6.05, 5.45). Cap placed to its left:
- `comp.y = 5.45` (same Y as VIN pin)
- `comp.rotation = 0` → pad2 is the right pad at `(comp.x + 0.5, comp.y)`
- `comp.x` so pad2 is just outside IC courtyard (use spacing formula above)
- Trace: one straight horizontal segment

### Rule 3 — GND pad faces away
The non-connecting pad (GND or supply return) faces **away** from the IC, toward the board edge.

---

## Trace routing rules — follow every time

1. **No crossings**: Check every trace pair for crossings **before writing the JSON**. Different-net traces must not cross.
2. **GND routing direction**: Route GND back-traces going toward the outer board edge, away from signal traces.
3. **EN-to-VIN ties**: Route that loop on the **opposite side** of the IC from the signal traces to avoid crossings.
4. **No trace through a foreign pad**: Never route a trace at the same Y (or X) as a pad row if that trace is a different net.
5. **Plan before writing**: List all trace paths, then check each pair for intersections before committing to JSON.

---

## 45° angle routing — always prefer 45° bends, never 90°

- **Never** use 90° bends (horizontal segment directly connected to a vertical segment).
- Use a short 45° diagonal to change direction at every corner.
- Pattern: straight → 45° diagonal → straight.
- Copper clearance applies to diagonals too. Verify: `ptSegDist(pad_center, seg_start, seg_end) − trace_half_width − pad_radius ≥ clearance`
- If a 45° diagonal is too close to a pad, extend the straight segment further before turning.

---

## Closest-valid-point routing

When connecting a pin to a net that already has multiple pads, **route to the nearest existing point**, not to an arbitrary pad.

Example: EN tied to VIN. VIN exists on U1.pad1 (1 mm away) and C1.pad2 (4 mm away).
→ Route EN → U1.pad1 (nearest). The existing VIN trace carries the connection to C1.pad2.

---

## Float pads

Pads explicitly marked `float` in the profile must have `"net": ""` — **never assign them to GND**.

---

## Board JSON format

```json
{
  "version": "1.0",
  "title": "<PART_NUMBER> Layout Example",
  "board": { "width": 20, "height": 15, "units": "mm" },
  "components": [
    {
      "id": "U1", "ref": "U1", "value": "<PART_NUMBER>",
      "footprint": "<footprint-name>",
      "x": 10, "y": 7.5, "rotation": 0, "layer": "F",
      "pads": [
        { "number": "1", "name": "VIN", "x": -1.0, "y": 0,
          "type": "smd", "shape": "rect", "size_x": 0.6, "size_y": 1.5, "net": "VIN" }
      ]
    }
  ],
  "nets": [{ "name": "VIN", "pads": ["U1.1", "C1.1"] }],
  "traces": [
    { "net": "VIN", "segments": [
        { "start": { "x": 9.0, "y": 7.5 }, "end": { "x": 7.5, "y": 7.5 } }
    ]}
  ],
  "vias": []
}
```

Key rules:
- `pads[].x/y` are **relative to component center** (unrotated)
- `traces[].segments[].start/end` are **world coordinates** (mm from board origin)
- Every signal pad must have a non-empty `net`
- Every net in `nets[]` must list all pad refs (`"REF.padnumber"`)
- Board size: 1 mm margin around all component courtyards

---

## Self-check before saving

Before `PUT .../layout_example`, verify every item:

1. **All components present** — every required component is in `components[]`
2. **No courtyard overlaps** — every pair satisfies minimum spacing
3. **No 90° bends** — every direction change uses a 45° segment
4. **No trace crossings** — every different-net trace pair is non-intersecting
5. **Cap alignment** — each cap's connecting pad shares X or Y with its IC pin
6. **Cap orientation** — connecting pad faces IC, GND pad faces away
7. **Closest routing** — each pin routes to nearest pad of its net
8. **All pads have nets** — no signal pad has empty `net`
9. **Float pads** — pads marked float in profile have `"net": ""`
10. **Board size fits** — all components + 1 mm margin inside board boundary

---

## Save

```
PUT http://localhost:3030/api/library/<slug>/layout_example
Content-Type: application/json

<board JSON>
```
