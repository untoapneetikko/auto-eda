"""
endpoint.py — FastAPI router for the Schematic Agent.

Routes:
  POST /agents/schematic/run    — body: { "project_brief": "string" }
  GET  /agents/schematic/status — returns last run status
"""

from __future__ import annotations

import json
import os
import time
import traceback
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# State (in-process; use Redis for multi-process setups)
# ---------------------------------------------------------------------------

_status: dict[str, Any] = {
    "state": "idle",   # idle | running | done | error
    "started_at": None,
    "finished_at": None,
    "error": None,
    "output_path": None,
    "component_count": None,
    "net_count": None,
}

_OUTPUT_PATH = Path(
    os.getenv("OUTPUT_DIR", Path(__file__).parent.parent.parent / "data" / "outputs")
) / "schematic.json"

# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/agents/schematic", tags=["schematic"])


class RunRequest(BaseModel):
    project_brief: str


class RunResponse(BaseModel):
    status: str
    output_path: str
    component_count: int
    net_count: int


class StatusResponse(BaseModel):
    state: str
    started_at: float | None
    finished_at: float | None
    error: str | None
    output_path: str | None
    component_count: int | None
    net_count: int | None


@router.post("/run", response_model=RunResponse)
def run_schematic(body: RunRequest) -> RunResponse:
    """
    Synchronously run the schematic agent for the given project brief.

    Reads data/outputs/datasheet.json, component.json, example_schematic.json.
    Calls Claude to produce data/outputs/schematic.json.
    Returns a summary of the result.

    Raises:
      422 — if project_brief is empty
      500 — if the agent fails (file missing, model error, validation error)
    """
    if not body.project_brief.strip():
        raise HTTPException(status_code=422, detail="project_brief must not be empty")

    if _status["state"] == "running":
        raise HTTPException(status_code=409, detail="Agent is already running")

    # Update state
    _status.update(
        state="running",
        started_at=time.time(),
        finished_at=None,
        error=None,
        output_path=None,
        component_count=None,
        net_count=None,
    )

    try:
        # Import here so FastAPI doesn't fail to start if dotenv isn't loaded yet
        from agent import run as agent_run  # noqa: PLC0415

        result = agent_run(body.project_brief)

        component_count = len(result.get("components", []))
        net_count = len(result.get("nets", []))

        _status.update(
            state="done",
            finished_at=time.time(),
            output_path=str(_OUTPUT_PATH),
            component_count=component_count,
            net_count=net_count,
        )

        return RunResponse(
            status="done",
            output_path=str(_OUTPUT_PATH),
            component_count=component_count,
            net_count=net_count,
        )

    except Exception as exc:  # noqa: BLE001
        tb = traceback.format_exc()
        _status.update(
            state="error",
            finished_at=time.time(),
            error=f"{type(exc).__name__}: {exc}\n{tb}",
        )
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc


@router.get("/status", response_model=StatusResponse)
def get_status() -> StatusResponse:
    """Return the status of the last (or current) schematic agent run."""
    return StatusResponse(**_status)
