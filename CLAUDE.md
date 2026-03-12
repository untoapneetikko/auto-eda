# Auto-EDA — Orchestrator

> **ALWAYS push to git after making changes.**
> ```
> git add -A && git commit -m "your message" && git push
> ```

## Project Purpose
Auto-EDA: fully automated EDA design pipeline. User uploads a datasheet PDF.
The system produces: component symbol, footprint, schematic, placement, and routed layout.

## Agent Pipeline (in order)

| Step | Agent | Input | Output |
|------|-------|-------|--------|
| 1 | datasheet-parser | PDF file | `data/outputs/datasheet.json` |
| 2 | component | `datasheet.json` | `data/outputs/component.json` |
| 3 | footprint | `datasheet.json` | `data/outputs/footprint.json` |
| 4 | example-schematic | `datasheet.json` | `data/outputs/example_schematic.json` |
| 5 | schematic | `datasheet.json` + user intent | `data/outputs/schematic.json` |
| 5b | connectivity | `schematic.json` | `data/outputs/connectivity.json` |
| 6 | autoplace | `schematic.json` + `connectivity.json` | `data/outputs/placement.json` |
| 7 | autoroute | `placement.json` + `schematic.json` | `data/outputs/routing.json` |
| 8 | layout | all outputs | `data/outputs/final_layout.json` |

## Rules for All Agents
- Read your input file first. Never assume its contents.
- Write output to `data/outputs/` using the exact filename in the table above.
- Validate your output against `shared/schemas/` before writing.
- Never modify another agent's output file.
- Run your validation script after every write.
- Commit your output with message: `feat(agent-name): complete run [timestamp]`

## Tech Stack
- Language: Python 3.12
- Framework: FastAPI
- Data: Polars
- AI: Claude Code (main agent orchestrates, no API key required)
- Storage: Redis (agent state), local filesystem (files)
- Output formats: KiCad (.kicad_sym, .kicad_mod, .kicad_sch, .kicad_pcb)

## Do Not
- Do not skip schema validation
- Do not hardcode file paths — always use environment variables from `.env`
- Do not call the Anthropic API directly — Claude Code IS the AI, all reasoning happens via Agent tool calls
