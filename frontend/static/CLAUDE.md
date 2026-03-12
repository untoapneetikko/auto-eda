# Schematic Designer — Claude Code Operating Instructions

## What this project is

SVG-based electronics schematic editor. Users upload component datasheets (PDFs), the tool parses them into structured profiles, and engineers draw schematics using those components. Server runs at `http://localhost:3030`. Main files: `index.html` (entire frontend), `server.js` (Express API).

## Ticket system — two separate trackers

There are **two completely separate ticket systems**. Do not confuse them.

| | Improvement Tickets | Generate Tickets |
|-|--------------------|--------------------|
| **Purpose** | Bugs & features in the app code | AI prompts for component data |
| **File** | `improvements.json` | `gen_tickets.json` |
| **API** | `/api/issues` | `/api/gen-tickets` |
| **Tab** | 📋 Improvements | 🎫 Generate |
| **Locking** | Yes — mandatory | No |
| **Full docs** | [`IMPROVEMENT_TICKETS.md`](./IMPROVEMENT_TICKETS.md) | [`GENERATE_TICKETS.md`](./GENERATE_TICKETS.md) |

---

## Module map — read this before touching any code

**`MODULES.md`** is the definitive file-to-module map. Every module has exact file + line-range boundaries. Read it before starting work so you don't overlap with another agent.

Quick reference:
- **Library/Datasheets**: `server.js` 35–231, `index.html` 932–1804
- **Footprint Editor**: `server.js` 232–596, `index.html` 1398–1804
- **Gen Tickets**: `server.js` 849–900, `index.html` 1805–2008
- **Layout Example Tab**: `index.html` 2009–2108
- **Symbol Renderer**: `index.html` 2290–4417
- **Schematic Editor**: `index.html` 4418–7565
- **PCB Editor**: `pcb.html` (entire file)
- **Projects/Export**: `server.js` 606–750
- **Issue Tracker + Agents**: `server.js` 752–1087

---

## MANDATORY: Agent registry — do this in every session

Every agent **must** register itself with the live agent registry at startup, ping while working, and unregister when done. This is what makes the Agents tab show real-time status.

### Generate a unique agent ID

Pick a short unique ID for yourself — use your task slug + 4 random hex chars, e.g. `pcb-ratsnest-a3f1`.

### Step R1 — Register on startup (do this FIRST, before any other work)

```bash
curl -s -X POST http://localhost:3030/api/agents/register \
  -H "Content-Type: application/json" \
  -d "{\"id\":\"YOUR-ID\",\"name\":\"Claude Code\",\"task\":\"Short task description\",\"ticket_id\":54,\"model\":\"claude-sonnet-4-6\"}"
```

Fields:
- `id` — unique string you chose
- `name` — always `"Claude Code"`
- `task` — one-line description of what you're doing
- `ticket_id` — the issue tracker ticket number (omit if no ticket)
- `model` — `"claude-sonnet-4-6"` (or whichever model you are)
- `worktree` — git branch name if you're in a worktree (optional)

### Step R2 — Ping after every major step

After each significant action (read a file, wrote code, ran a test, etc.), send a ping:

```bash
curl -s -X POST http://localhost:3030/api/agents/YOUR-ID/ping \
  -H "Content-Type: application/json" \
  -d "{\"step\":\"Reading index.html to find toolbar\",\"observations\":\"Found toolbar at line 718\"}"
```

**Ping at minimum after every tool call group.** An agent that hasn't pinged in 90 seconds is marked stale (red) in the UI. Pings are cheap — do them often.

Fields:
- `step` — short description of the current action (shown in step log, last 3 visible)
- `observations` — longer running notes (replaces previous observations each ping)

### Step R3 — Unregister when done (success or failure)

```bash
curl -s -X DELETE http://localhost:3030/api/agents/YOUR-ID \
  -H "Content-Type: application/json" \
  -d "{\"status\":\"done\",\"observations\":\"Final summary of what was built\"}"
```

Use `"status":"failed"` if something went wrong. The record stays visible in the Done column for 10 minutes after unregistering.

### Agent registry API reference

| Action | Method + URL | Body |
|--------|-------------|------|
| Register | `POST /api/agents/register` | `{ id, name, task, ticket_id?, worktree?, model? }` |
| Ping | `POST /api/agents/:id/ping` | `{ step?, observations?, status? }` |
| Unregister | `DELETE /api/agents/:id` | `{ status?, observations? }` |
| List all | `GET /api/agents` | — |

The registry is in-memory (resets on server restart). Always re-register at the top of a session.

---

## MANDATORY: Ticket workflow — do this every session

This project uses a Linear-style issue tracker at `improvements.json`, served via API. **You must follow this workflow for every ticket you work on, every session.**

### Step 1 — Read all open tickets first

```
GET http://localhost:3030/api/issues
```

Do this before touching any code. The tracker is the persistent memory across sessions. It tells you what is open, in-progress, blocked, and done. Do not re-implement things that are already done.

### Step 2 — Check the lock before claiming a ticket

Every issue has a `locked` field. **If `locked: true` — do not touch it.** It is owned by another agent. Pick a different ticket.

### Step 3 — Lock BEFORE writing any code

```
POST http://localhost:3030/api/issues/:id/lock
Content-Type: application/json

{ "agent": "Claude Code" }
```

- `200 OK` → you own it, proceed
- `409 Conflict` → someone locked it between your check and your lock attempt, pick another ticket
- **Never skip this step.** An unlocked ticket you work on can be double-worked.

### Step 4 — Set status to in-progress

```
PUT http://localhost:3030/api/issues/:id
Content-Type: application/json

{ "status": "inprogress" }
```

### Step 5 — Do the work

Write findings into the `observations` field as you go — not just at the end. If you hit a problem, write what you tried. This is how knowledge survives between sessions.

### Step 6 — Mark done and UNLOCK

When finished (success or failure):

```
PUT http://localhost:3030/api/issues/:id
{ "status": "done" }          ← or "cancelled" with "dismissed_because" filled in
```

Then immediately:

```
POST http://localhost:3030/api/issues/:id/unlock
```

**An unreleased lock blocks every future session forever.**

### Full checklist — run through this every time:

```
[ ] GET /api/issues — read all open tickets
[ ] Is the ticket locked? → yes: pick another / no: continue
[ ] POST /api/issues/:id/lock — lock it BEFORE writing code
[ ] PUT /api/issues/:id { "status": "inprogress" }
[ ] Do the work, write observations as you go
[ ] PUT /api/issues/:id { "status": "done" } (or "cancelled")
[ ] POST /api/issues/:id/unlock — ALWAYS unlock when done
```

---

## Issue API reference

| Action | Method + URL | Body |
|--------|-------------|------|
| List all issues | `GET /api/issues` | — |
| Create issue | `POST /api/issues` | `{ title, description, priority, status, category }` |
| Update issue | `PUT /api/issues/:id` | any fields to update |
| Lock | `POST /api/issues/:id/lock` | `{ "agent": "Claude Code" }` |
| Unlock | `POST /api/issues/:id/unlock` | — |

Priority values: `urgent`, `high`, `medium`, `low`
Status values: `backlog`, `inprogress`, `done`, `cancelled`
Category values: `library`, `schematic`, `layout`, `other` — always set category when creating a ticket

---

## Git — commit and push when done

After completing a ticket or group of related fixes:

```bash
git add -A
git commit -m "description of what changed"
git push
```

Always push. Changes sitting uncommitted are lost if the machine restarts.

---

## Key architecture facts

- **Single HTML file**: `index.html` contains the entire frontend (~4500 lines). No build step.
- **SchematicEditor class**: SVG-based canvas editor. Two instances: `editor` (main schematic tab) and `appCircuitEditor` (example schematic inside datasheet panel).
- **`_isEmbedded` flag**: `appCircuitEditor` has this set to `true`. Prevents it from corrupting the main editor's toolbar/status. Do not remove this.
- **`profileCache`**: Global map of slug → profile JSON. IC symbols read from this. Populate it before placing IC components.
- **`_icLayout(slug)`**: Computes IC pin layout. `PIN_STUB = 40` — wires connect at pin stub tips.
- **`library/`**: Permanent knowledge base. Never delete files here.
- **`improvements.json`**: Issue tracker data. Served by server.js. Do not edit directly — use the API.
- **`labelInputId` option**: When creating `appCircuitEditor`, pass `{ labelInputId: 'acc-label-input' }` so it uses its own label input, not the main editor's.

---

## Datasheet / profile rules

**Read `DATASHEET_PARSING_GUIDE.md` before parsing any component** — it has the full schema, device-type guidance, and a worked example (PMA3-83LNW+).

Quick reminders:
- `symbol_type` must always be `"ic"` — no exceptions
- Every physical pad gets its own pin entry — no grouped pins like `"number": "1,3,4"`
- `human_corrections` array in profiles is sacred — never overwrite or delete entries
- When rebuilding a profile, preserve all existing `human_corrections`

---

## Code conventions

- No build tools, no TypeScript, no frameworks — plain HTML/CSS/JS
- All frontend code lives in `index.html`
- `esc()` function escapes strings for SVG/HTML injection — use it everywhere user data appears in HTML
- Grid is 20px — all component positions should snap to multiples of 20
- Wire format: `{ id, points: [{x,y}...] }` — never use old `x1/y1/x2/y2` format
