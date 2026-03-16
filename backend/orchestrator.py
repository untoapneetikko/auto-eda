"""
backend/orchestrator.py — Multi-agent EDA pipeline orchestrator.

Uses claude-agent-sdk to spawn Claude Code sessions for each reasoning step.
No Anthropic API key required — uses Claude Code's built-in auth
(run `claude auth login` once in your environment).

Pure-Python steps (no Claude reasoning needed):
  - connectivity  → check_connectivity() is fully deterministic
  - layout        → apply_layout() assembles deterministically from inputs

AI-reasoning steps (Claude Code session spawned per step):
  - datasheet-parser, component, footprint, example-schematic,
    schematic, autoplace, autoroute
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import anyio
import redis as redis_lib
from claude_agent_sdk import query, ClaudeAgentOptions, ResultMessage

PROJECT_ROOT = Path(__file__).parent.parent

# ── sys.path for pure-Python agent steps ─────────────────────────────────────
for _p in [
    str(PROJECT_ROOT),
    str(PROJECT_ROOT / "backend"),
    str(PROJECT_ROOT / "backend" / "tools"),
    str(PROJECT_ROOT / "agents" / "connectivity"),
    str(PROJECT_ROOT / "agents" / "layout"),
]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ── Pipeline step catalogue ───────────────────────────────────────────────────
PIPELINE_STEPS = [
    ("datasheet-parser",  "Parsing Datasheet"),
    ("component",         "Designing Component Symbol"),
    ("footprint",         "Designing Footprint"),
    ("example-schematic", "Building Reference Schematic"),
    ("schematic",         "Designing Project Schematic"),
    ("connectivity",      "Checking Connectivity"),
    ("autoplace",         "Placing Components"),
    ("autoroute",         "Routing Traces"),
    ("layout",            "Assembling Final Layout"),
]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _update_job(r: redis_lib.Redis, job_id: str, **kwargs: Any) -> None:
    r.hset(f"job:{job_id}", mapping={k: str(v) for k, v in kwargs.items()})
    r.publish(f"job:{job_id}", json.dumps({k: str(v) for k, v in kwargs.items()}))


def _output_path(filename: str) -> Path:
    return Path(os.getenv("OUTPUT_DIR", str(PROJECT_ROOT / "data" / "outputs"))) / filename


def _load_claude_md(agent_name: str) -> str:
    p = PROJECT_ROOT / "agents" / agent_name / "CLAUDE.md"
    return p.read_text(encoding="utf-8") if p.exists() else ""


# ── Claude Code step runner ───────────────────────────────────────────────────

async def _run_claude_step(task_prompt: str) -> str:
    """
    Spawn a Claude Code session for one pipeline step.
    Returns the result text from Claude Code.
    """
    result_text = ""
    async for message in query(
        prompt=task_prompt,
        options=ClaudeAgentOptions(
            cwd=str(PROJECT_ROOT),
            allowed_tools=["Bash", "Read", "Write", "Edit"],
            permission_mode="bypassPermissions",
            max_turns=30,
        ),
    ):
        if isinstance(message, ResultMessage):
            result_text = message.result
    return result_text


def _run_step(task_prompt: str) -> str:
    """Sync wrapper around the async Claude Code runner."""
    return anyio.from_thread.run_sync(
        lambda: anyio.run(_run_claude_step, task_prompt)
    )


# ── Individual pipeline step functions ───────────────────────────────────────

def _step_datasheet_parser(r: redis_lib.Redis, job_id: str, pdf_path: str) -> None:
    _update_job(r, job_id,
                step="datasheet-parser",
                step_label="Parsing Datasheet",
                step_index=0,
                step_status="running")

    claude_md = _load_claude_md("datasheet-parser")
    task = f"""You are running the Auto-EDA datasheet parser step.

AGENT INSTRUCTIONS:
{claude_md}

YOUR TASK:
Parse the PDF datasheet at: {pdf_path}

STEPS TO FOLLOW:
1. Read the agent module:
   python -c "import sys; sys.path.insert(0,'agents/datasheet-parser'); sys.path.insert(0,'backend'); from agent import run; ctx = run('{pdf_path}'); print('PDF extracted, chars:', len(ctx.get('raw_text','')))"

2. Read the raw text and tables from data/outputs/datasheet_context.json

3. Extract ALL component data following the schema at shared/schemas/datasheet_output.json
   - Extract EVERY pin (including NC pins)
   - Use null for missing values, never guess
   - Include example_application from the datasheet circuit

4. Write your result to data/outputs/datasheet.json

5. Validate:
   python backend/tools/schema_validator.py data/outputs/datasheet.json shared/schemas/datasheet_output.json

Report success or errors."""

    anyio.run(_run_claude_step, task)
    _update_job(r, job_id, step_status="done")


def _step_component(r: redis_lib.Redis, job_id: str) -> None:
    _update_job(r, job_id,
                step="component",
                step_label="Designing Component Symbol",
                step_index=1,
                step_status="running")

    claude_md = _load_claude_md("component")
    task = f"""You are running the Auto-EDA component symbol step.

AGENT INSTRUCTIONS:
{claude_md}

YOUR TASK:
Design a KiCad component symbol for the component in data/outputs/datasheet.json

STEPS TO FOLLOW:
1. Read data/outputs/datasheet.json
2. Design the symbol following all rules in the instructions above
3. Use the agent tool to write and validate:
   python -c "
import sys
sys.path.insert(0, '.')
sys.path.insert(0, 'backend')
sys.path.insert(0, 'agents/component')
from agent import apply_symbol
import json

symbol = {{YOUR_SYMBOL_DICT}}

result = apply_symbol(symbol)
print(json.dumps(result, indent=2))
"
4. Verify both component.json and component.kicad_sym are written

Report success or any errors."""

    anyio.run(_run_claude_step, task)
    _update_job(r, job_id, step_status="done")


def _step_footprint(r: redis_lib.Redis, job_id: str) -> None:
    _update_job(r, job_id,
                step="footprint",
                step_label="Designing Footprint",
                step_index=2,
                step_status="running")

    claude_md = _load_claude_md("footprint")
    task = f"""You are running the Auto-EDA footprint step.

AGENT INSTRUCTIONS:
{claude_md}

YOUR TASK:
Design a KiCad PCB footprint for the component in data/outputs/datasheet.json

STEPS TO FOLLOW:
1. Read data/outputs/datasheet.json — focus on the footprint/package section
2. Design the footprint using IPC-7351 standards
3. Write using:
   python -c "
import sys
sys.path.insert(0, '.')
sys.path.insert(0, 'backend')
sys.path.insert(0, 'backend/tools')
sys.path.insert(0, 'agents/footprint')
from agent import apply_footprint
import json

footprint = {{YOUR_FOOTPRINT_DICT}}
result = apply_footprint(footprint)
print(json.dumps(result, indent=2))
"
4. Verify footprint.json and footprint.kicad_mod are written

Report success or errors."""

    anyio.run(_run_claude_step, task)
    _update_job(r, job_id, step_status="done")


def _step_example_schematic(r: redis_lib.Redis, job_id: str) -> None:
    _update_job(r, job_id,
                step="example-schematic",
                step_label="Building Reference Schematic",
                step_index=3,
                step_status="running")

    claude_md = _load_claude_md("example-schematic")
    task = f"""You are running the Auto-EDA example schematic step.

AGENT INSTRUCTIONS:
{claude_md}

YOUR TASK:
Build an example application schematic from the datasheet's application circuit.

STEPS TO FOLLOW:
1. Read data/outputs/datasheet.json — focus on example_application section
2. Get the application context:
   python -c "
import sys
sys.path.insert(0, 'agents/example-schematic')
from agent import get_application_context
import json
print(json.dumps(get_application_context(), indent=2))
"
3. Design the example schematic following instructions above
4. Write and validate:
   python -c "
import sys
sys.path.insert(0, 'agents/example-schematic')
sys.path.insert(0, 'backend')
from agent import apply_schematic
import json

schematic = {{YOUR_SCHEMATIC_DICT}}
result = apply_schematic(schematic)
print(json.dumps(result, indent=2))
"
Report success or errors."""

    anyio.run(_run_claude_step, task)
    _update_job(r, job_id, step_status="done")


def _step_schematic(r: redis_lib.Redis, job_id: str, project_brief: str) -> None:
    _update_job(r, job_id,
                step="schematic",
                step_label="Designing Project Schematic",
                step_index=4,
                step_status="running")

    claude_md = _load_claude_md("schematic")
    task = f"""You are running the Auto-EDA schematic design step.

AGENT INSTRUCTIONS:
{claude_md}

PROJECT BRIEF:
{project_brief or "General-purpose development board using the component from the datasheet."}

YOUR TASK:
Design a complete project schematic for the brief above.

STEPS TO FOLLOW:
1. Get the design context:
   python -c "
import sys
sys.path.insert(0, 'agents/schematic')
sys.path.insert(0, 'backend')
from agent import get_design_context
import json
print(json.dumps(get_design_context(), indent=2))
"
2. Design the schematic:
   - Every net gets a meaningful name (no NET001-style)
   - Every IC pin must have a connection (NC marker for unused pins)
   - Power symbols first, then IC, then passives
3. Write and validate:
   python -c "
import sys
sys.path.insert(0, 'agents/schematic')
sys.path.insert(0, 'backend')
from agent import apply_schematic
import json

schematic = {{YOUR_SCHEMATIC_DICT}}
result = apply_schematic(schematic)
print(json.dumps(result, indent=2))
"
Report success or errors."""

    anyio.run(_run_claude_step, task)
    _update_job(r, job_id, step_status="done")


def _step_connectivity(r: redis_lib.Redis, job_id: str) -> None:
    """Pure Python — no Claude reasoning needed."""
    _update_job(r, job_id,
                step="connectivity",
                step_label="Checking Connectivity",
                step_index=5,
                step_status="running")

    # Import connectivity agent (pure Python, in sys.path)
    from agent import run_from_file  # agents/connectivity/agent.py

    result = run_from_file()
    report = result.get("report", {})
    stats = report.get("stats", {})

    _update_job(r, job_id,
                step_status="done",
                connectivity_valid=report.get("valid", False),
                connectivity_errors=stats.get("error_count", 0),
                connectivity_warnings=stats.get("warning_count", 0))


def _step_autoplace(r: redis_lib.Redis, job_id: str) -> None:
    _update_job(r, job_id,
                step="autoplace",
                step_label="Placing Components",
                step_index=6,
                step_status="running")

    claude_md = _load_claude_md("autoplace")
    task = f"""You are running the Auto-EDA component placement step.

AGENT INSTRUCTIONS:
{claude_md}

YOUR TASK:
Place all components on the PCB. Read data/outputs/schematic.json for components.

STEPS TO FOLLOW:
1. Get placement context:
   python -c "
import sys
sys.path.insert(0, '.')
sys.path.insert(0, 'backend')
sys.path.insert(0, 'agents/autoplace')
from agent import get_placement_context
import json
print(json.dumps(get_placement_context(), indent=2))
"
2. Design optimal placement (connectors at edge, decoupling caps near ICs, etc.)
3. Write and validate:
   python -c "
import sys
sys.path.insert(0, '.')
sys.path.insert(0, 'backend')
sys.path.insert(0, 'agents/autoplace')
from agent import apply_placement
import json

placement = {{YOUR_PLACEMENT_DICT}}
result = apply_placement(placement)
print(json.dumps(result, indent=2))
"
4. Every placement entry MUST include a rationale string.
Report success or errors."""

    anyio.run(_run_claude_step, task)
    _update_job(r, job_id, step_status="done")


def _step_autoroute(r: redis_lib.Redis, job_id: str) -> None:
    _update_job(r, job_id,
                step="autoroute",
                step_label="Routing Traces",
                step_index=7,
                step_status="running")

    claude_md = _load_claude_md("autoroute")
    task = f"""You are running the Auto-EDA routing step.

AGENT INSTRUCTIONS:
{claude_md}

YOUR TASK:
Route all PCB traces. Inputs: data/outputs/placement.json + data/outputs/schematic.json

STEPS TO FOLLOW:
1. Get routing context + seed routing:
   python -c "
import sys
sys.path.insert(0, '.')
sys.path.insert(0, 'backend')
sys.path.insert(0, 'agents/autoroute')
from agent import get_routing_context, _seed_routing
import json
ctx = get_routing_context()
seed = _seed_routing()
print('=== ROUTING CONTEXT ===')
print(json.dumps(ctx, indent=2)[:5000])
print('=== SEED ROUTING ===')
print(json.dumps(seed, indent=2)[:3000])
"
2. Improve the seed routing:
   - Power traces first, widest (≥0.4mm for power, 0.2mm for signals)
   - No routing under crystals/oscillators
   - Minimize vias
3. Write and validate:
   python -c "
import sys
sys.path.insert(0, '.')
sys.path.insert(0, 'backend')
sys.path.insert(0, 'agents/autoroute')
from agent import apply_routing
import json

routing = {{YOUR_ROUTING_DICT}}
result = apply_routing(routing)
print(json.dumps(result, indent=2))
"
Report success or errors."""

    anyio.run(_run_claude_step, task)
    _update_job(r, job_id, step_status="done")


def _step_layout(r: redis_lib.Redis, job_id: str, project_brief: str) -> None:
    """Pure Python — deterministic assembly from all prior outputs."""
    _update_job(r, job_id,
                step="layout",
                step_label="Assembling Final Layout",
                step_index=8,
                step_status="running")

    # Add layout agent to sys.path
    layout_dir = str(PROJECT_ROOT / "agents" / "layout")
    if layout_dir not in sys.path:
        sys.path.insert(0, layout_dir)

    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "eda_layout_agent",
        PROJECT_ROOT / "agents" / "layout" / "agent.py"
    )
    la = importlib.util.module_from_spec(spec)
    sys.modules["eda_layout_agent"] = la
    spec.loader.exec_module(la)

    board_meta = {
        "project_name": (project_brief[:50] if project_brief else "Auto-EDA Project"),
        "revision": "v1.0",
        "author": "Auto-EDA",
    }

    result = la.apply_layout(board_meta=board_meta)

    kicad_pcb = result.get("files", {}).get("kicad_pcb", "")
    _update_job(r, job_id,
                step_status="done",
                kicad_pcb=kicad_pcb)


# ── Main pipeline entry point ─────────────────────────────────────────────────

def run_pipeline(r: redis_lib.Redis, job_id: str, pdf_path: str, project_brief: str = "", api_key: str = "") -> None:
    """Run the complete 9-step EDA pipeline for a given job."""
    if api_key:
        os.environ["ANTHROPIC_API_KEY"] = api_key
    try:
        _update_job(r, job_id,
                    status="running",
                    started_at=time.time(),
                    total_steps=len(PIPELINE_STEPS))

        _step_datasheet_parser(r, job_id, pdf_path)
        _step_component(r, job_id)
        _step_footprint(r, job_id)
        _step_example_schematic(r, job_id)
        _step_schematic(r, job_id, project_brief)
        _step_connectivity(r, job_id)
        _step_autoplace(r, job_id)
        _step_autoroute(r, job_id)
        _step_layout(r, job_id, project_brief)

        _update_job(r, job_id, status="done", completed_at=time.time())

    except Exception as exc:
        import traceback
        tb = traceback.format_exc()
        _update_job(r, job_id,
                    status="error",
                    error=str(exc),
                    error_trace=tb[:3000])
        raise
