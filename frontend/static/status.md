# Schematic Designer — Status

## Current: Phase 2 — Schematic Editor + Seed Library
**Version:** 2.0

### What works:
- Local Express server on port 3030 (no API key needed)
- PDF upload via drag-and-drop or file browser
- Server-side PDF text extraction using pdf-parse
- Confidence detection (HIGH/MEDIUM/LOW) based on text quality
- Bad PDF detection with user guidance
- Component profiles stored as JSON files in datasheets/[SLUG]/
- Live SSE updates — browser refreshes when Claude Code writes a profile
- Claude Code IS the AI — parses on demand, no API cost
- Human correction system saved to profile.json
- Component library view, searchable
- Export profile as JSON, delete component
- Pending parse UI shows exact prompt to paste into Claude Code
- **Seed library**: RESISTOR, CAPACITOR, CAPACITOR_POL, INDUCTOR, VCC, GND, DIODE, LED, LM7805, AMS1117-3.3, NE555, 2N2222, BC547, IRF540N, LM358, L298N, DRV8833
- **Parsed datasheets**: PMA3-83LNW+, QPA9510
- **Schematic editor**: Canvas with Select/Wire/Delete/Rotate/Undo/Redo tools
- **Symbol rendering**: Resistor, Cap, Inductor, VCC, GND, Amplifier, Op-Amp, IC box, Diode, LED, NPN BJT, N-MOSFET
- **Projects**: Save/load via API, multiple projects
- **Component palette** in schematic: ⚡ button to load example circuit instantly
- **Example circuit viewer**: uses same SchematicEditor canvas (interactive)
- **Schematic symbol preview**: uses same SchematicEditor rendering pipeline
- **IC symbols**: proper DIP-style rectangles — exact pin count, colored stubs (green=input, blue=output, red=power, gray=GND, yellow=passive), pin names inside box, pin numbers outside
- **profileCache**: IC component profiles cached on place/load so symbols render with real pin data in schematic editor
- **PDF viewer**: opens on right side of profile view
- **Datasheet page refs**: shown on every pin (p.X) and passive (p.X) — click to jump to PDF page
- **Esc key**: cancels component placement and wire drawing in schematic editor
- **Edit Symbol button**: opens inline pin editor (rename/add/remove pins, change types) — saves via PUT /api/library/:slug/pins

### File structure:
- server.js — Express server, PDF upload, file serving, SSE
- index.html — frontend dashboard
- datasheets/ — permanent knowledge base (never delete)
- START.md — how to run

- **Component Builder**: "+ Build" button in schematic palette opens a modal to define a new component from scratch — enter part name, description, symbol type, and pins, then "Save & Place" creates it instantly in the library and starts placement
- **Schematic Info Panel**: right-side panel in the editor shows the IC symbol, pin list, and description of the selected component; "View Datasheet" button jumps to its datasheet page
- **Open in Editor**: "Open in Schematic Editor →" button on the Example Schematic tab loads the full app circuit into the main editor

- **Improvements tab**: Linear-styled issue tracker with 12 seeded tickets — status, priority, AI Observations field, Dismissed Because field. Persisted to `improvements.json`. API: GET/POST/PUT/DELETE `/api/issues`.
- **Ticket lock system**: Agents must lock a ticket before working on it (`POST /api/issues/:id/lock`), 409 if already locked, mandatory unlock when done (`POST /api/issues/:id/unlock`). Lock state visible in list (🔒 icon, amber row) and detail panel.
- **INSTRUCTIONS.md updated**: AI must read issue tracker before starting any work in a new session.

### What is next (Phase 3):
- More seed library components (ATmega328P, STM32F103, ESP32, etc.)
- Manual component editor (add/edit pins without PDF)
- Tag/category filtering in library
- Import profile from JSON
- Net labels on schematic (same-name wires = same net)
- Export schematic to SVG/PNG

### Phase 4 after that:
- AI circuit generator (describe circuit → schematic auto-generated)
- Cross-component compatibility checks (voltage, current, logic level)
- Power budget calculator
- Safety report (errors, warnings, OK)
