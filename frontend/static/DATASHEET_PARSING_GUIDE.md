# Datasheet Parsing Guide for AI Agents

This guide is the authoritative reference for parsing electronics component datasheets into `profile.json` files for this project. Read it before parsing any datasheet.

---

## Profile JSON — complete schema

```json
{
  "part_number": "exact string from datasheet cover/title, including suffixes like + or -T",
  "description": "one sentence: what the device does, key spec, frequency range if RF",
  "manufacturer": "manufacturer name",
  "package_types": ["exact package string from mechanical section, e.g. 12-lead MCLP 3x3x0.89mm (DQ1225)"],
  "supply_voltage_range": "verbatim from datasheet, e.g. 4.75–5.25V or 5.75–6.75V",
  "frequency_range": "only if RF/microwave — e.g. 0.4 to 8.0 GHz",
  "impedance": "only if RF — e.g. 50Ω",
  "designator": "U",
  "absolute_max": {
    "supply_voltage": "...",
    "input_voltage": "...",
    "output_current": "...",
    "operating_temp": "...",
    "junction_temperature": "...",
    "esd_hbm": "class and voltage e.g. Class 1A (250-500V)"
  },
  "key_performance": {
    "gain_typical": "22.6 dB @ 2 GHz, 6V — only if analog/RF device"
  },
  "pins": [ /* see pins section below */ ],
  "required_passives": [ /* see passives section below */ ],
  "application_circuits": [
    {
      "name": "Typical application (Fig. X, p.N)",
      "description": "Signal path described as A → B → C. Bias path described. Every component mentioned.",
      "page": 4
    }
  ],
  "common_mistakes": [
    "MUST / NEVER / WARNING / CAUTION / REQUIRED statements verbatim from datasheet",
    "Any trap that would destroy the device or cause malfunction"
  ],
  "notes": "PCB layout rules, evaluation board info, anything critical not captured above",
  "symbol_type": "ic",
  "status": "parsed",
  "confidence": "HIGH",
  "extraction_note": null,
  "filename": "ORIGINAL_FILENAME.pdf",
  "page_count": 5,
  "raw_text_length": 6553,
  "uploaded_at": "2026-03-08T16:52:47.739Z",
  "parsed_at": "<ISO timestamp — use new Date().toISOString()>",
  "builtin": false,
  "human_corrections": []
}
```

**Hard rules:**
- `symbol_type` is ALWAYS `"ic"` — no exceptions
- `human_corrections` is a sacred field — NEVER overwrite or delete it
- Preserve the existing `uploaded_at`, `filename`, `page_count`, `raw_text_length` from the file — do not invent these

---

## Pins

Every physical pad on the device gets its own entry — no grouping like `"number": "1,3,4"`.

```json
{
  "number": 8,
  "name": "RF-OUT/DC-IN",
  "type": "bidirectional",
  "description": "Combined RF output and DC supply input. RF exits via C3. VDD injected via L2.",
  "active": null,
  "internal_pull": null,
  "requirements": "MANDATORY: VDD via L2 (39nH choke). RF output via C3 (100pF DC block). Both required — see Fig.1 p.4.",
  "ambiguous": false,
  "datasheet_page": 3
}
```

**Pin type values:** `power`, `gnd`, `input`, `output`, `passive`, `bidirectional`
- `power` — supply input (VCC, VDD, VIN)
- `gnd` — ground (GND, VSS, Paddle)
- `input` — signal input only
- `output` — signal output only
- `bidirectional` — can be both, or combined function (e.g. RF-OUT/DC-IN above)
- `passive` — NC pins, test points, no active function

**`active`:** `"high"`, `"low"`, or `null` (for logic-active signals only)
**`internal_pull`:** `"up"`, `"down"`, or `null`

**NC (No Connect) pins:**
- Give each a separate entry
- Check carefully if some NC pads must float vs. must be grounded — these are different
- Example from PMA3-83LNW+: pads 1,3-10 → tie to GND; pads 11,12 → MUST FLOAT

**Exposed paddle / thermal pad:**
- Always gets its own entry, `"number": 0` (or the pad number if specified)
- Type: `"gnd"` if it's a thermal ground paddle
- Requirements must mention: full solder coverage, thermal vias, no partial soldering

---

## Required passives

Every component in the application circuit that is not the main IC:

```json
{
  "type": "inductor",
  "value": "18nH 0402",
  "part_number": "Murata LQP15MN18NJ02D",
  "placement": "RF-IN (pad 2) to GND",
  "reason": "L1 — shunt inductor for DC bias return and ESD. Required per Fig.1 p.4.",
  "datasheet_page": 4
}
```

**Type values:** `capacitor`, `inductor`, `resistor`, `ferrite`, `diode`, `connector`

If the datasheet gives a BOM (e.g. "Table 1 — BOM for test circuit"), extract every component from it with `part_number` populated. If only generic values, leave `part_number` as an empty string or omit it.

---

## Application circuits

Describe the signal path clearly as a chain: `source → component → pin → device → pin → component → load`.
Include bias path separately. Mention every passive by its designator and value.

**Example (PMA3-83LNW+):**
> RF path: RF source → C2 (10pF series) → pad 2 → [device] → pad 8 → C3 (100pF DC block) → RF output. Bias path: VDD → L2 (39nH choke) → pad 8. C1 (0.01µF) VDD bypass to GND. L1 (18nH) pad 2 to GND.

---

## Common mistakes — what to include

Scan the full datasheet text for these patterns and include them verbatim or summarised accurately:
- MUST, NEVER, DO NOT, REQUIRED, CAUTION, WARNING, NOTE, IMPORTANT
- Absolute maximum ratings violation → device destruction
- NC pins with special treatment (must float, must be grounded)
- ESD class (Class 1A = fragile, handle carefully)
- Supply sequencing requirements
- Thermal requirements (thermal pad must be soldered, solder paste coverage %)
- Boot strapping pins (common in MCUs, e.g. GPIO0 must be LOW at boot)
- Reserved GPIOs connected to internal flash (e.g. ESP32 GPIO6-11)
- Input power limits (RF devices)
- Maximum junction temperature

---

## Device-type specific guidance

### Digital ICs (MCU, logic, memory)
- Extract every GPIO/pin individually — even if the table has 40 rows
- Note alternate functions in `description` (e.g. "PB5 / SPI_SCK / OC1A / PCINT5")
- Boot strapping pins go in `common_mistakes`
- Decoupling caps: one entry per VCC/VDD pin if they are separate pads

### Analog / op-amps / comparators
- Input offset, CMRR, GBW in `key_performance`
- Note if input range includes rail (rail-to-rail)
- Compensation capacitor requirements in `required_passives`

### RF / microwave MMICs
- `frequency_range` and `impedance` fields are mandatory
- `key_performance` must capture: NF, gain, OIP3/IIP3, P1dB, IP2 at typical frequency
- DC bias scheme is critical — bias-T inductors are mandatory passives
- Input/output DC blocks are mandatory passives
- Common_mistakes must include: max input power (both continuous and pulsed), ESD class
- PCB layout: 50Ω controlled impedance, short RF paths, no sharp corners
- Recommended evaluation board / PCB layout reference go in `notes`

### Power regulators / LDOs / DC-DC
- Input and output voltage ranges in `supply_voltage_range`
- Enable pin polarity in `active` field
- Soft-start capacitor and feedback resistors in `required_passives`
- Thermal resistance junction-to-case in `absolute_max` or `notes`

### RF switches / attenuators
- Control pin logic levels and timing
- Isolation and insertion loss in `key_performance`
- Driver requirements for control pins

### Passives (R/L/C)
- These get `symbol_type: "passive"` — exception to the ic-only rule
- Keep profiles minimal: value, tolerance, package, voltage/current rating

---

## Worked example: PMA3-83LNW+

This profile is the gold standard. Key decisions made during parsing:

1. **Pad 8 is RF-OUT/DC-IN combined** — not separate pads. The `type` is `bidirectional`. Both the RF path and DC bias path share this single pad. Missing either L2 or C3 destroys the circuit.

2. **NC pads 11 and 12 MUST FLOAT** — unlike all other NC pads (1,3-7,9-10) which are tied to GND on the characterization board. This is explicitly stated on p.3. Tying 11/12 to GND is a wiring mistake.

3. **C2 is 10pF, not 100pF** — the datasheet mentions 100pF as an optional additional series cap (separate from C2), not as C2's value. BOM on p.4 confirms C2 = 10pF. This was caught by human review and added to `human_corrections`.

4. **Exposed ground paddle has number 0** — not listed in the pad table but is physically the exposed bottom pad. Requires full solder coverage for thermal reasons.

5. **L2 is mandatory** — it is literally the only DC path to the device. Without it the amplifier gets no supply and will not function. This must appear in both `required_passives` and `common_mistakes`.

6. **`human_corrections` preserved** — after rebuild, all three human_corrections entries were kept verbatim. Never delete these.

---

## How to parse a datasheet — step by step

1. Read `raw_text.txt` in full before writing anything
2. Identify: part number, manufacturer, package
3. Find the pin table / pad description section — map every physical pad
4. Find the application circuit / typical circuit schematic — identify all passives
5. Find absolute maximum ratings — note ESD class
6. Scan full text for MUST/NEVER/WARNING/CAUTION/NOTE
7. Identify the device category (digital, analog, RF, power) and apply relevant guidance above
8. Write `profile.json` with all fields populated
9. Set `status: "parsed"`, `confidence: "HIGH"` (or "MEDIUM" if text quality is poor)
10. Leave `human_corrections: []` empty — do not populate it

---

## File locations

- Raw text: `library/<SLUG>/raw_text.txt`
- Profile: `library/<SLUG>/profile.json`
- Slug is uppercase with underscores, e.g. `PMA3_83LNW`

Read the existing `profile.json` first if it exists — it may have `human_corrections` that must be preserved.

---

## Quality checklist before saving

- [ ] Every physical pad has its own entry (no grouped pins)
- [ ] `symbol_type` is `"ic"`
- [ ] `human_corrections` from existing file preserved unchanged
- [ ] All mandatory passives in `required_passives` (not buried in `notes`)
- [ ] `common_mistakes` has all MUST/NEVER/WARNING items
- [ ] Application circuit description traces signal path as A→B→C chain
- [ ] NC pins that must float are flagged with MUST in `requirements`
- [ ] `status: "parsed"`, `parsed_at` set to current ISO timestamp
- [ ] `uploaded_at`, `filename`, `page_count`, `raw_text_length` copied from existing file
