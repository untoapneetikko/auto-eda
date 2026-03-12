# Generate Tickets — How They Work

Generate tickets deliver **AI-generation prompts** for component data — footprints, profiles, schematic examples, and PCB layout examples. They live in `gen_tickets.json` and are managed via the `/api/gen-tickets` API.

View them in the app: **🎫 Generate** tab → `http://localhost:3030`

---

## What a generate ticket is

A generate ticket is a ready-to-run prompt for Claude Code. When automatic generation fails (e.g. the inline footprint generator can't parse a package), the app creates a ticket containing the full instruction set. Claude Code reads the prompt, does the work, and marks the ticket done.

Generate tickets are **not** for tracking bugs or features — use [Improvement Tickets](./IMPROVEMENT_TICKETS.md) for that.

**Use these for:**
- Building a component footprint from a datasheet
- Parsing/rebuilding a component profile
- Generating a schematic example circuit
- Generating a PCB layout example

---

## Ticket types

| Type | What it does |
|------|-------------|
| `footprint` | Reads datasheet dimensions → builds pad geometry JSON → saves to `/api/footprints/:name` |
| `datasheet` | Parses raw PDF text → builds full component profile → saves to `/api/library/:slug` |
| `example` | Generates a schematic example circuit for a component |
| `layout` | Generates a PCB layout example — see [LAYOUT_EXAMPLE_INSTRUCTIONS.md](./LAYOUT_EXAMPLE_INSTRUCTIONS.md) for mandatory rules |

---

## Ticket fields

| Field | Description |
|-------|-------------|
| `id` | Auto-assigned integer. Displayed as `33: Title` in the Generate tab |
| `type` | `footprint` · `datasheet` · `example` · `layout` |
| `slug` | Library slug this ticket applies to, e.g. `PMA3_83LNW` |
| `title` | Human-readable label, e.g. `PMA3-83LNW+ → Layout Example` |
| `prompt` | The full Claude Code prompt to execute — click the ticket to copy it |
| `status` | `pending` (not started) · `done` (completed) |

There is **no locking mechanism** on generate tickets. They are single-agent, single-execution tasks — copy the prompt, run it, mark done.

---

## Workflow

```
1. Open the Generate tab in the app
2. Click a pending ticket to see the prompt
3. Copy the prompt → paste into Claude Code
4. Claude Code executes the work
5. Mark the ticket done:
   PUT http://localhost:3030/api/gen-tickets/:id
   Body: { "status": "done" }
```

Or from Claude Code directly:
```bash
# Copy prompt from ticket detail panel, run as Claude Code session
# When finished, mark done:
curl -X PUT http://localhost:3030/api/gen-tickets/33 \
  -H "Content-Type: application/json" \
  -d '{"status":"done"}'
```

---

## API reference

| Action | Method + URL | Body |
|--------|-------------|------|
| List all | `GET /api/gen-tickets` | — |
| Create | `POST /api/gen-tickets` | `{ type, slug, title, prompt, status }` |
| Update | `PUT /api/gen-tickets/:id` | any fields |
| Delete | `DELETE /api/gen-tickets/:id` | — |
| Retract | `POST /api/gen-tickets/:id/retract` | — (restores pre-ticket snapshot) |

**Status values:** `pending` `done`
**Type values:** `footprint` `datasheet` `example` `layout`

---

## Layout tickets — extra rules apply

For `layout` type tickets, the agent **must** read [LAYOUT_EXAMPLE_INSTRUCTIONS.md](./LAYOUT_EXAMPLE_INSTRUCTIONS.md) before doing anything. That file contains:

- Step 0: clear existing layout first (`DELETE /api/library/:slug/layout_example`)
- Package courtyard dimensions and minimum spacing tables
- Trace routing rules (45° bends, no crossings, closest-point routing)
- Self-check checklist before saving

**Never skip LAYOUT_EXAMPLE_INSTRUCTIONS.md for a layout ticket.**

---

## How tickets are created

Tickets are generated automatically by the app when:
- The inline footprint generator fails → creates a `footprint` ticket
- The user clicks **✨ Generate → 🗺 Layout Example** on a component → creates a `layout` ticket
- The user clicks **✨ Generate → 📐 Schematic Example** → creates an `example` ticket

They can also be created manually via `POST /api/gen-tickets`.

---

## Not to be confused with

**Improvement Tickets** ([IMPROVEMENT_TICKETS.md](./IMPROVEMENT_TICKETS.md)) track bugs and features in the application code. They use `/api/issues`, have a locking mechanism for multi-agent safety, and live in `improvements.json`. They are for changing `index.html`, `pcb.html`, or `server.js` — not for component data.
