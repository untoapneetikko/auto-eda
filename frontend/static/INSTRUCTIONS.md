# AI Schematic Designer — Build Instructions

## Core Philosophy

**The datasheet is the source of truth. Everything else follows from it.**

The tool's primary job is to deeply understand components by reading their datasheets — not just extracting a pin table, but understanding what the component does, how it behaves, what it requires to function, and what will destroy it. The schematic is the output of that understanding.

A human expert reads a datasheet and thinks:
- "This pin needs a 100nF cap within 5mm or it oscillates"
- "This enable pin is active-low, floating it will disable the chip silently"
- "This part can't drive more than 25mA — that LED needs a resistor"

The AI must think the same way.

---

## What This Is

Drop in a datasheet (or a part number). Describe what you want to build. The AI reads, reasons, and generates a complete, correct schematic — with every connection justified by the datasheet.

**Example:**
> "I want to drive a 12V DC motor from a Raspberry Pi GPIO pin"
→ AI identifies needed components (H-bridge driver, flyback diodes, logic-level shifter, bulk cap)
→ AI reads each datasheet — extracts pins, ratings, application circuit, required passives
→ AI generates schematic with every wire explained: "BOOT pin pulled high via 100k per datasheet p.12 Fig.3"
→ Renders on canvas, exports to SVG/PDF/SPICE

---

## Tech Stack

- **Frontend**: React + Vite (or single HTML file for early phases)
- **Canvas**: Plain Canvas API for schematic rendering (no heavy lib)
- **AI**: Claude API (`claude-sonnet-4-6`) — the reasoning engine
- **PDF parsing**: PDF.js — extracts raw text from uploaded datasheet PDFs
- **Component DB**: Local JSON file, grows as user adds parts
- **Storage**: localStorage for schematics, IndexedDB for parsed datasheet cache
- **Export**: SVG, PNG, PDF, SPICE netlist (.net), BOM CSV

---

## Build Phases

### Phase 1 — Datasheet Parser (THIS IS THE FOUNDATION)

This is built first. Everything else depends on it.

**Input**: User uploads a PDF datasheet (or pastes a URL)
**Process**:
1. PDF.js extracts all text from the PDF
2. Text is sent to Claude with this task:

```
You are reading an electronics component datasheet. Extract the following and return as JSON:

{
  "part_number": "exact part number",
  "description": "one sentence — what this component does",
  "package_types": ["SOIC-8", "DIP-8"],
  "absolute_max": {
    "supply_voltage": "6V",
    "input_voltage": "VCC + 0.3V",
    "output_current": "40mA per pin",
    "operating_temp": "-40°C to +85°C"
  },
  "supply_voltage_range": "1.8V to 5.5V",
  "pins": [
    {
      "number": 1,
      "name": "VCC",
      "type": "power",
      "description": "Supply voltage",
      "requirements": "100nF decoupling cap to GND placed close to pin"
    },
    {
      "number": 2,
      "name": "EN",
      "type": "input",
      "active": "high",
      "internal_pulldown": true,
      "description": "Enable pin. Float or pull low to disable output.",
      "requirements": "Pull high via 10k if always-on operation required"
    }
  ],
  "required_passives": [
    {
      "type": "capacitor",
      "value": "100nF",
      "placement": "VCC to GND, within 5mm of IC",
      "reason": "Bypass capacitor required per datasheet section 7.3"
    }
  ],
  "application_circuits": [
    {
      "name": "Typical application",
      "description": "Single supply operation with enable control",
      "page": 12
    }
  ],
  "common_mistakes": [
    "Leaving BOOT pin floating causes undervoltage lockout",
    "Input voltage must not exceed VCC + 0.3V or latch-up occurs"
  ],
  "notes": "Any critical information not captured above"
}

Be thorough. Capture every warning, every implicit requirement, every 'must' and 'never' in the datasheet.
```

3. Parsed result saved to `components.json` under the part number
4. UI shows a **Component Profile** card: description, pin table, warnings, required passives

**This step alone is the core value of the tool.**

---

### Phase 2 — Component Library

- `components.json` grows with every parsed datasheet
- UI: searchable list of all known components
- Each component card shows: description, package, supply range, pin count, warnings
- User can manually edit any parsed field (AI isn't always right)
- Built-in seed library of the 50 most common components:
  - LM7805, AMS1117 (regulators)
  - NE555 (timer)
  - L298N, DRV8833 (motor drivers)
  - ATmega328P, STM32F103 (microcontrollers)
  - LM358, TL071 (op-amps)
  - IRF540N, 2N2222, BC547 (transistors)
  - Common passives: resistors, caps, inductors, diodes, LEDs

---

### Phase 3 — Canvas Schematic Editor

- Grid-snapped canvas
- Drag components from library onto canvas
- Components render as standard schematic symbols (IEEE/IEC style)
- Click pin → click another pin → wire drawn
- Wires route at 90° angles, no diagonal lines
- Net labels (name a wire, reuse the name elsewhere = same net)
- Zoom, pan, select, move, delete
- Save/load as JSON
- Undo/redo (Ctrl+Z)

---

### Phase 4 — AI Circuit Generator

User describes what they want. AI generates the full schematic.

**Flow:**
1. User types: "Control a brushless motor with an ESP32"
2. AI identifies needed components — queries `components.json` for matches
3. For any component not in the database, AI says: "I need the datasheet for ESC XYZ — please upload it"
4. Once all component profiles are available, AI generates:

```json
{
  "components": [
    { "id": "U1", "part": "ESP32-WROOM", "position": "left" },
    { "id": "U2", "part": "BLHeli_S ESC", "position": "right" },
    { "id": "C1", "part": "100nF", "position": "near U1 pin 1" }
  ],
  "nets": [
    {
      "name": "3V3_RAIL",
      "connects": ["U1.3V3", "C1.+"],
      "reason": "3.3V supply rail with decoupling"
    },
    {
      "name": "PWM_SIGNAL",
      "connects": ["U1.GPIO18", "U2.SIGNAL"],
      "reason": "ESC signal wire accepts 50Hz PWM, GPIO18 is PWM-capable on ESP32"
    }
  ],
  "warnings": [
    "ESC power ground must connect to ESP32 ground — floating ground will corrupt PWM signal",
    "Do not power ESP32 from ESC BEC if BEC is noisy — use separate LDO"
  ]
}
```

5. Schematic renders automatically from the JSON
6. Every wire shows its reason on hover

---

### Phase 5 — Deep Reasoning Mode

When the user wants maximum confidence:

1. AI reads the **full datasheet text** for every component in the schematic (not just the parsed summary)
2. AI cross-checks every connection against the datasheet:
   - "Pin 4 is BOOT — datasheet p.8 says must be pulled high for normal operation. Current schematic: floating. **ERROR.**"
   - "Motor driver VS pin rated max 46V. Current net voltage: 12V. OK."
   - "Output current per pin: 600mA max. LED load: 20mA. OK."
3. Produces a **Safety Report**:
   - ERRORS (will not work or will be damaged)
   - WARNINGS (may work but risky)
   - OK (verified against datasheet)

---

### Phase 6 — Export

- **SVG** — vector schematic, scales to any size
- **PNG** — rasterized at 300dpi
- **PDF** — print-ready with title block (project name, date, version, author)
- **SPICE netlist** (.net) — compatible with LTspice and ngspice
- **BOM CSV** — part, reference, value, package, quantity, Mouser/Digikey search link
- **JSON** — raw schematic data for re-import

---

## Datasheet Parsing Rules

These rules apply to every parse. The AI must follow them strictly.

1. **Never guess a pin function** — if the datasheet is ambiguous, mark it `"ambiguous": true` and quote the exact datasheet text
2. **Capture every absolute maximum** — these are safety limits. Missing one can destroy hardware.
3. **Required passives are mandatory** — if the datasheet says "a 100nF cap is required", it goes in `required_passives`, not `notes`
4. **Active-low pins must be marked** — `"active": "low"` prevents silent failures from logic inversion
5. **Internal pull-ups/pull-downs must be captured** — affects whether external resistors are needed
6. **Page numbers matter** — every extracted fact should reference the datasheet page it came from
7. **Common mistakes section is critical** — scan the datasheet for words like "must", "never", "do not", "required", "caution", "warning" and include those verbatim

---

## Component JSON Schema

**Symbol type rule: always use `"symbol_type": "ic"`**
All components — regardless of their physical form (MMIC, regulator, transistor, op-amp, etc.) — are rendered as the square IC box symbol in the schematic editor. Do not use resistor, capacitor, diode, amplifier, or any other symbol type in generated profiles. The IC box is the only symbol type used.

**Pin count rule: every pad on the device must have an entry**
If the datasheet shows N pads, the profile must have exactly N pins. NC (no-connect) pads must each have their own individual entry — do not group multiple pads into a single pin entry with a comma-separated number like `"number": "1,3,4,5"`. Every pad gets its own row. This ensures the schematic symbol shows the complete physical reality of the device.

```json
{
  "part_number": "LM7805",
  "description": "5V 1A positive linear voltage regulator",
  "designator": "U",
  "package_types": ["TO-220", "TO-92", "SOT-223"],
  "supply_voltage_range": "7V to 35V input",
  "output_voltage": "5V ±4%",
  "absolute_max": {
    "input_voltage": "35V",
    "output_current": "1.5A",
    "power_dissipation": "see thermal derating curve p.4"
  },
  "pins": [
    { "number": 1, "name": "INPUT", "type": "power", "requirements": "0.33µF ceramic cap to GND if more than 7cm from bulk capacitor", "datasheet_page": 1 },
    { "number": 2, "name": "GND",   "type": "gnd",   "requirements": "Connect to common ground", "datasheet_page": 1 },
    { "number": 3, "name": "OUTPUT","type": "power",  "requirements": "0.1µF ceramic cap to GND for stability", "datasheet_page": 1 }
  ],
  "required_passives": [
    { "type": "capacitor", "value": "0.33µF", "placement": "INPUT to GND", "reason": "Required if regulator is far from filter capacitor — datasheet p.1", "datasheet_page": 1 },
    { "type": "capacitor", "value": "0.1µF",  "placement": "OUTPUT to GND", "reason": "Improves transient response — datasheet p.1", "datasheet_page": 1 }
  ],
  "common_mistakes": [
    "Minimum input-output differential is 2V — at 5V output, input must be at least 7V",
    "No heatsink at >500mA will cause thermal shutdown"
  ],
  "symbol_type": "ic",
  "datasheet_source": "user_upload",
  "datasheet_pages": 18,
  "parsed_at": "2026-03-09T00:00:00Z"
}
```

---

## Datasheet Library — The AI's Long-Term Memory

The `datasheets/` directory is the AI's permanent knowledge base. Every datasheet ever parsed is stored here and never deleted. The AI reads from this library before every action.

### Directory structure:
```
datasheets/
├── index.json                        ← master index of all known parts
├── LM7805/
│   ├── raw_text.txt                  ← full text extracted from PDF
│   ├── profile.json                  ← parsed component profile (pins, ratings, etc.)
│   └── original.pdf                  ← original uploaded PDF
├── L298N/
│   ├── raw_text.txt
│   ├── profile.json
│   └── original.pdf
└── ESP32-WROOM-32/
    ├── raw_text.txt
    ├── profile.json
    └── original.pdf
```

### How the AI uses the library (like a Claude system prompt):

When the user asks to generate a circuit or verify a connection, the AI does this before reasoning:

1. **Load all relevant profiles** from `datasheets/*/profile.json` for every component involved
2. **Inject the profiles as context** at the top of the Claude prompt — exactly like a system prompt:

```
COMPONENT KNOWLEDGE BASE:
========================

[LM7805 — 5V Linear Regulator]
Input: 7–35V | Output: 5V 1A
Pin 1 INPUT: requires 0.33µF cap to GND if >7cm from bulk cap
Pin 2 GND
Pin 3 OUTPUT: requires 0.1µF cap to GND for stability
CRITICAL: Min 2V dropout. Input must be ≥7V for 5V output.
CRITICAL: No heatsink → thermal shutdown above 500mA

[L298N — Dual H-Bridge Motor Driver]
Logic supply VSS: 5V | Motor supply VS: 5–46V
Pin SENSE_A/B: connect 0.5Ω resistor to GND for current sensing, or short to GND to disable
...

========================
Now reason about the circuit using ONLY the above datasheet knowledge.
Every connection you make must reference a pin entry above.
```

3. The AI is **not allowed to guess** — if a component is not in the library, it must ask the user to upload the datasheet before proceeding
4. After the circuit is generated, the AI **cites the library** for every connection: "R1 on LM7805 INPUT — required per LM7805 profile, INPUT pin requirement"

### Library growth rules:
- Every newly uploaded datasheet is parsed and added permanently
- Profiles can be manually corrected by the user — corrections are saved and never overwritten by re-parsing
- `index.json` tracks: part number, description, date added, source (user upload or URL), times used in schematics
- The library is the single most valuable asset of the tool — it gets smarter every time a new part is added

---

## File Structure

```
schematic_designer/
├── INSTRUCTIONS.md           ← this file
├── index.html                ← main app
├── components.json           ← active component list (mirrors datasheets/index.json)
├── datasheets/               ← permanent AI knowledge base (never delete)
│   ├── index.json            ← master index
│   └── [PART_NUMBER]/        ← one folder per component
│       ├── profile.json      ← parsed knowledge
│       ├── raw_text.txt      ← full datasheet text
│       └── original.pdf      ← source PDF
├── schematics/               ← saved user schematics (JSON)
└── exports/                  ← SVG, PDF, BOM outputs
```

---

## Bad Datasheet Handling

Most datasheets are not clean. Scanned PDFs, image-heavy layouts, and poor OCR are the norm. The parser must handle this gracefully — a silent wrong parse is worse than an honest failure.

### Detection — know when extraction failed

After PDF.js extracts text, run these checks before sending to Claude:

```
- Total extracted characters < 500 → likely a scanned image PDF, no readable text
- Pin table found: scan for patterns like "Pin 1", "VCC", "GND", table rows with numbers
- If no pin table pattern detected → flag as "low confidence parse"
- If extracted text contains mostly garbage characters (%, §, random symbols) → flag as "corrupt extraction"
```

### Fallback strategy (in order):

**Level 1 — Retry with different PDF page range**
Some datasheets have the pin table on pages 3–6. If first pass misses it, retry extracting pages individually.

**Level 2 — Ask Claude to work with what it has**
Even partial text is useful. Send it with reduced expectations:
```
This datasheet text may be incomplete due to poor PDF scanning.
Extract whatever you can. For anything you cannot confirm, set "ambiguous": true
and explain what was missing. Do not fill in gaps with assumptions.
```

**Level 3 — Image fallback (if Claude vision available)**
Send the PDF pages as images to Claude's vision model. Tables and pin diagrams in images are often readable this way even when text extraction fails.

**Level 4 — Partial profile + manual completion**
If extraction is too poor for automation, create a skeleton profile with what is known (part number, description, package) and mark all pins as `"status": "needs_manual_entry"`. UI shows the component card in yellow with a warning: "Datasheet could not be fully parsed — please verify pins manually."

**Level 5 — Reject with clear explanation**
If nothing works, do not silently create a wrong profile. Tell the user:
```
Could not reliably parse [PART_NUMBER].pdf.
Reason: PDF appears to be a scanned image with no readable text.
Options:
1. Upload a text-based PDF version (search "[part number] datasheet filetype:pdf" on manufacturer's site)
2. Enter pin information manually
3. Skip this component and use a known alternative from the library
```

### Confidence scoring

Every parsed profile gets a confidence score shown in the UI:

```
HIGH   — full text extracted, pin table found, all pins named, page refs confirmed
MEDIUM — partial text, pin table found but some pins ambiguous
LOW    — limited text extraction, pins inferred, human review recommended
FAILED — could not parse, manual entry required
```

- HIGH and MEDIUM profiles can be used in AI circuit generation
- LOW profiles trigger a warning: "This component profile has low confidence — verify before ordering parts"
- FAILED profiles block AI generation until manually completed

### Never silently trust a bad parse

If a profile was parsed from a low-quality source, every schematic that uses it shows a banner:
> "⚠ [PART] profile is LOW confidence — review pin assignments before building"

This is non-negotiable. A wrong pin connection built in hardware costs real money and time.

---

## Component Selection Reasoning

When the user describes a need without naming a specific part, the AI must follow this process — never just pick the first match.

### Selection process:
1. **Extract requirements from the description**
   - Supply voltage available
   - Load voltage and current
   - Interface type (I2C, SPI, PWM, analog, etc.)
   - Package preference (through-hole for prototyping, SMD for production)
   - Any constraints mentioned (cost, availability, size)

2. **Query the library for candidates**
   - Search `datasheets/index.json` for components matching the function
   - If multiple candidates exist, compare their profiles against requirements

3. **Score each candidate**
   - PASS/FAIL against hard requirements (voltage, current within absolute max)
   - Prefer parts already in the library (user likely has them)
   - Prefer simpler parts when complexity isn't needed

4. **Explain the choice**
   - "Chose L298N over DRV8833 because user's motor is 12V and DRV8833 is rated max 10.8V per its datasheet"
   - If only one candidate exists, say so
   - If no candidate exists, name 2–3 suitable parts and ask the user to pick and upload the datasheet

5. **Never silently pick** — always show the selection reasoning before generating the schematic

---

## Cross-Component Compatibility Checks

Every connection between two components must pass these checks before the schematic is finalized. These run automatically after the AI generates the wiring netlist, before rendering.

### Check 1 — Voltage compatibility
- Output pin voltage ≤ absolute max input voltage of destination pin
- Example: 5V GPIO → input pin rated max 3.6V = **ERROR: needs level shifter**
- If supply voltage is unknown, ask before proceeding

### Check 2 — Current compatibility
- Source pin max output current ≥ load current requirement
- Example: MCU GPIO (max 25mA) → LED (20mA) with no resistor = **WARNING: add current limiting resistor**
- Example: Regulator (500mA) → circuit total draw (800mA) = **ERROR: regulator undersized**

### Check 3 — Logic level compatibility
- 3.3V output driving 5V logic input: check if destination has 5V-tolerant inputs (captured in profile)
- 5V output driving 3.3V input: always ERROR, requires level shifter
- Open-drain outputs need pull-up resistor — check profile for `"output_type": "open_drain"`

### Check 4 — Power budget
After full netlist is generated:
1. Sum total current draw of all components (from their profiles)
2. Verify power supply can deliver it with 20% headroom
3. Flag if any single rail is overloaded
4. Output a power budget table:
```
Rail     | Supply  | Draw    | Headroom | Status
---------|---------|---------|----------|--------
5V       | 1000mA  | 650mA   | 35%      | OK
3.3V     | 500mA   | 480mA   | 4%       | WARNING — tight
12V_MOTOR| 2000mA  | 1800mA  | 10%      | WARNING — add margin
```

### Check 5 — Ground connectivity
Every component must share a common ground with every other component it communicates with. Floating grounds are a silent failure mode. Flag any component whose GND pin is not connected to the common ground net.

---

## Correction Feedback Loop

When a user manually edits a connection the AI generated, that correction is valuable knowledge. The system must capture and learn from it.

### How it works:
1. User changes a wire, pin connection, or adds/removes a component the AI placed
2. UI prompts: "You changed [connection]. What was wrong with the original? (optional but helps improve future results)"
3. User's explanation (or just the diff if they skip) is written to the component profile:

```json
"human_corrections": [
  {
    "date": "2026-03-08",
    "original": "GPIO18 connected directly to motor driver IN1",
    "correction": "Added 100Ω series resistor between GPIO18 and IN1",
    "reason": "Series resistor protects GPIO from inductive spikes on the input line",
    "affects_pins": ["ESP32.GPIO18", "L298N.IN1"]
  }
]
```

4. On future circuit generations involving the same components, the AI loads `human_corrections` as high-priority context — above its own reasoning, below the datasheet itself
5. Corrections are never deleted — they accumulate as institutional knowledge

### Priority order for AI reasoning:
```
1. Datasheet absolute maximums (hard limits — never violate)
2. Human corrections in the library (learned from real mistakes)
3. Datasheet required passives and application notes
4. General electronics engineering rules
5. AI inference
```

---

## Schematic Symbol Standards

All components are drawn using **IEEE standard schematic symbols**. Consistency matters — a schematic drawn with mixed conventions is unreadable.

### Symbol rules:
- **Resistor**: rectangular box (IEEE) — not the zigzag (ANSI older style)
- **Capacitor**: two parallel lines — polarized cap has one curved plate
- **Inductor**: series of bumps (arcs)
- **Diode**: triangle pointing to bar — cathode is the bar
- **LED**: diode symbol with two arrows pointing away (light emission)
- **NPN transistor**: vertical line (base), arrow on emitter pointing outward
- **PNP transistor**: same but arrow on emitter pointing inward
- **Op-amp**: triangle with +/− inputs on left, output on right
- **IC/MCU**: rectangle with pins on left and right sides, pin names inside, pin numbers outside
- **Power symbols**: VCC = upward arrow with label, GND = downward triangle
- **Net labels**: flag shape — same label name = same electrical net (no wire needed)

### Layout conventions:
- Signal flows left → right
- Power flows top → bottom (VCC at top, GND at bottom)
- Inputs on left side of component, outputs on right
- No wires crossing unless marked with a junction dot
- Components aligned to grid (50mil or 100mil)
- Wire junctions shown as filled dots — a crossing with no dot is NOT connected

---

## Build Agent Instructions

1. Read this file completely before writing any code
2. Phase 1 (datasheet parser) is built first — no exceptions
3. After each phase: update `status.md` with version, what works, what is next
4. Test the parser with at least 3 real datasheets before moving to Phase 2
5. Never remove working features when adding new ones
6. The AI reasoning quality matters more than the UI — a plain ugly tool that parses correctly beats a beautiful tool that misses pin requirements
7. Every generated connection must be traceable to a datasheet page or a known engineering rule

---

## Continuing the Project — AI Checklist

**When a user says "continue", "keep going", or starts a new session on this project, the AI MUST do this before writing any code:**

### Step 1 — Read the issue tracker
Fetch `GET /api/issues` (or open the **📋 Improvements** tab in the app at http://localhost:3030).

The issue tracker (`improvements.json`) is the single source of truth for:
- What is broken (bugs)
- What is planned (features)
- What was dismissed or skipped and why
- What was completed

**Do not start implementing anything until you have read all open issues.**

### Step 2 — Check locks before touching anything

Every issue has a `locked` flag. **This is not optional to read.**

```
GET /api/issues
```

For every issue you are considering working on:

- If `"locked": true` → **STOP. Do not work on this ticket.** It is owned by another agent session. Working on a locked ticket causes conflicts and lost work.
- If `"locked": false` → you may claim it.

### Step 3 — Lock the ticket BEFORE writing any code

This is **mandatory**. You must lock the ticket before you start, not after.

```
POST /api/issues/:id/lock
Content-Type: application/json

{ "agent": "Claude Code" }
```

- If the response is `409 Conflict` → someone else locked it between your check and your lock attempt. Do not proceed. Pick a different ticket.
- If the response is `200 OK` → you own the ticket. Proceed.

**Do not skip this step.** A ticket that was not locked before work began is a ticket that can be double-worked.

### Step 4 — Update status as you work
- After locking → `PUT /api/issues/:id` with `{ "status": "inprogress" }`
- When done → `PUT /api/issues/:id` with `{ "status": "done" }`
- If a task reveals a new bug or subtask → `POST /api/issues` to create a new child issue

### Step 5 — Unlock when done

This is **mandatory**. You must unlock when finished, whether the work succeeded or failed.

```
POST /api/issues/:id/unlock
```

Unlock applies in ALL of these cases:
- Task completed successfully → mark `done`, then unlock
- Task abandoned (blocked, out of scope) → leave status unchanged, unlock, write why in Observations
- Task failed → mark `cancelled`, fill in `dismissed_because`, then unlock

**A lock you do not release is a lock that blocks the next agent forever.**

Checklist — memorize this:
```
[ ] Read all issues via GET /api/issues
[ ] Check: is the ticket I want locked? If yes → pick another
[ ] Lock the ticket: POST /api/issues/:id/lock  ← BEFORE writing code
[ ] Set status to inprogress
[ ] Do the work
[ ] Write findings in observations field
[ ] Set status to done (or cancelled with dismissed_because)
[ ] Unlock the ticket: POST /api/issues/:id/unlock  ← AFTER finishing
```

### Step 4 — Write findings into issues, not just code comments

Every issue has three text fields. Use them:

| Field | Purpose |
|-------|---------|
| **Description** | What needs to be done — the task specification |
| **AI Observations** | What you discovered *while working* — gotchas, constraints, design decisions, "I tried X and it failed because Y", tradeoffs made, things that were harder than expected |
| **Dismissed Because** | (only for `cancelled` issues) Why it was dropped — so the next session doesn't re-propose it and waste time re-arguing the same point |

**The AI Observations field is the most important.** It is the mechanism by which knowledge accumulates across sessions. A future AI that reads "I tried rendering SVG paths for each wire but Canvas arc coordinates don't translate 1:1 — ended up using PNG export instead" saves itself from repeating that exact failure.

Write observations as you go, not just at the end. If you hit a wall, write what you tried. If something worked unexpectedly well, write that too.

### Why this matters
Each session the AI starts fresh. The issue tracker is the **persistent memory** between sessions. Without reading it first, the AI risks:
- Re-implementing features that already exist
- Breaking things that were carefully fixed
- Repeating dismissed approaches that already failed
- Missing context about why decisions were made

The issue tracker is not just a to-do list — it is the AI's institutional memory and learning journal for this project. Over time, the Observations field across all tickets becomes a detailed record of what worked, what didn't, and what was overlooked. This is the data that makes future AI instructions better.

---

## Success Criteria

- Upload any common IC datasheet → tool produces a complete, accurate component profile in under 10 seconds
- Describe a circuit → schematic generated with zero connections that violate the datasheet
- Every wire on the schematic has a reason a junior engineer can read and verify
- An engineer can trust the output enough to order parts and build it
