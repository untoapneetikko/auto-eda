"""
Example Schematic Agent — FastAPI Router

Provides:
  GET  /agents/example-schematic/context  — extract application context from datasheet.json
  POST /agents/example-schematic/apply    — validate and persist a schematic dict
  POST /agents/example-schematic/run      — 501 stub (removed; Claude Code handles reasoning)
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

log = logging.getLogger(__name__)

router = APIRouter(prefix="/agents/example-schematic", tags=["example-schematic"])

# Ensure the agent directory is on sys.path so 'agent' and 'netlist_builder' resolve.
_agent_dir = str(Path(__file__).parent)
if _agent_dir not in sys.path:
    sys.path.insert(0, _agent_dir)


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class ApplyRequest(BaseModel):
    schematic: dict[str, Any]
    output_dir: str | None = None  # optional override for output directory


class ApplyResponse(BaseModel):
    success: bool
    errors: list[str]
    netlist_summary: str


class ContextResponse(BaseModel):
    component_name: str
    package: str
    example_application: dict[str, Any]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/context", response_model=ContextResponse)
async def get_context(datasheet_path: str | None = None) -> ContextResponse:
    """
    Extract the example_application section + component_name + package from
    datasheet.json so the orchestrator (Claude Code) can build a schematic.

    Query parameter:
      datasheet_path — optional override for the datasheet.json path.
    """
    import importlib
    agent = importlib.import_module("agent")

    path = Path(datasheet_path) if datasheet_path else agent.INPUT_PATH
    try:
        ctx = agent.get_application_context(path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return ContextResponse(**ctx)


@router.post("/apply", response_model=ApplyResponse)
async def apply_schematic(request: ApplyRequest) -> ApplyResponse:
    """
    Validate a schematic dict and write it to disk.

    The body must contain:
      schematic   — the schematic dict (matches schematic_output.json schema)
      output_dir  — optional path to write example_schematic.json into
    """
    import importlib
    agent = importlib.import_module("agent")

    out_dir = Path(request.output_dir) if request.output_dir else None

    try:
        result = agent.apply_schematic(request.schematic, output_dir=out_dir)
    except Exception as exc:
        log.exception("apply_schematic raised unexpectedly: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return ApplyResponse(
        success=result["success"],
        errors=result["errors"],
        netlist_summary=result["netlist_summary"],
    )


@router.post("/run")
async def run_agent_stub() -> None:
    """
    Removed. Claude Code handles reasoning.
    Use GET /context then POST /apply instead.
    """
    raise HTTPException(
        status_code=501,
        detail=(
            "run() is no longer implemented. "
            "Use GET /agents/example-schematic/context to read context "
            "and POST /agents/example-schematic/apply to persist results."
        ),
    )
