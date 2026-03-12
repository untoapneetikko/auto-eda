# Improvement Tickets — How They Work

Improvement tickets track **bugs, features, and enhancements** to the schematic designer application itself. They live in `improvements.json` and are managed via the `/api/issues` API.

View them in the app: **📋 Improvements** tab → `http://localhost:3030`

---

## What an improvement ticket is

An improvement ticket is a persistent work item describing something that needs to change in the codebase — a bug to fix, a feature to add, a refactor to make. Each ticket survives across sessions because `improvements.json` is committed to git.

**Use these for:**
- Bugs in the editor, parser, PCB renderer, or server
- New features requested by the user
- Refactors, UX improvements, performance issues
- Any work that modifies `index.html`, `pcb.html`, or `server.js`

**Do NOT use these for:**
- Component profiles, footprints, or layout examples → use [Generate Tickets](./GENERATE_TICKETS.md)
- One-off questions or explanations

---

## Ticket fields

| Field | Description |
|-------|-------------|
| `id` | Auto-assigned integer. Displayed as `123: Title` in the UI |
| `title` | Short description, e.g. `Library → Fit button broken on PCB tab` |
| `description` | Full spec of what needs to be done |
| `status` | `backlog` · `inprogress` · `done` · `cancelled` |
| `priority` | `urgent` · `high` · `medium` · `low` |
| `category` | `library` · `schematic` · `layout` · `other` |
| `locked` | `true` = being worked on by an agent right now — **do not touch** |
| `locked_by` | Agent name that holds the lock |
| `observations` | Running notes written by the AI while working — most important field |
| `dismissed_because` | Only for `cancelled` — why it was dropped |

---

## Workflow — mandatory every session

```
1. GET /api/issues              ← read ALL tickets first, every session
2. Check: is the ticket locked? → yes: pick another
3. POST /api/issues/:id/lock    ← BEFORE writing any code
4. PUT  /api/issues/:id  { "status": "inprogress" }
5. Do the work
6. Write findings in observations as you go (not just at the end)
7. PUT  /api/issues/:id  { "status": "done" }   (or "cancelled")
8. POST /api/issues/:id/unlock  ← ALWAYS unlock when finished
```

**An unreleased lock blocks every future agent session forever.**

---

## API reference

| Action | Method + URL | Body |
|--------|-------------|------|
| List all | `GET /api/issues` | — |
| Create | `POST /api/issues` | `{ title, description, priority, status, category }` |
| Update | `PUT /api/issues/:id` | any fields |
| Lock | `POST /api/issues/:id/lock` | `{ "agent": "Claude Code" }` |
| Unlock | `POST /api/issues/:id/unlock` | — |

**Priority values:** `urgent` `high` `medium` `low`
**Status values:** `backlog` `inprogress` `done` `cancelled`
**Category values:** `library` `schematic` `layout` `other`

Lock responses:
- `200 OK` → you own it, proceed
- `409 Conflict` → someone locked it first, pick another ticket

---

## Why the observations field matters

Each AI session starts fresh with no memory of previous sessions. The `observations` field is the only mechanism by which knowledge accumulates between sessions. Write what you tried, what failed, what you discovered, what tradeoffs you made. A future agent reading "tried X, failed because Y, used Z instead" avoids repeating the same failure.

Write observations **as you go**, not just at the end.

---

## Creating a new ticket

```
POST http://localhost:3030/api/issues
Content-Type: application/json

{
  "title": "Fit button broken after canvas resize",
  "description": "When switching to PCB tab while section was hidden, canvas has 0×0 dims.",
  "priority": "high",
  "status": "backlog",
  "category": "layout"
}
```

---

## Not to be confused with

**Generate Tickets** ([GENERATE_TICKETS.md](./GENERATE_TICKETS.md)) are for AI-generated component data — footprints, profiles, schematic examples, layout examples. They live in `gen_tickets.json` and use a completely separate API (`/api/gen-tickets`). They have no locking mechanism and are purely prompt-delivery vehicles for Claude Code.
