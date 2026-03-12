"""
Example Schematic Agent — FastAPI Router

Provides:
  POST /agents/example-schematic/run    — trigger agent run
  GET  /agents/example-schematic/status — get current run status
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

log = logging.getLogger(__name__)

router = APIRouter(prefix="/agents/example-schematic", tags=["example-schematic"])

# ---------------------------------------------------------------------------
# In-memory run state (single-run model; replace with Redis for production)
# ---------------------------------------------------------------------------
_state: dict[str, Any] = {
    "status": "idle",       # idle | running | done | failed
    "started_at": None,
    "finished_at": None,
    "output_path": None,
    "error": None,
    "netlist_summary": None,
}
_state_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class RunRequest(BaseModel):
    datasheet_path: str | None = None  # override default input path
    output_path: str | None = None     # override default output path


class StatusResponse(BaseModel):
    status: str
    started_at: str | None
    finished_at: str | None
    output_path: str | None
    error: str | None
    netlist_summary: str | None


class RunResponse(BaseModel):
    status: str
    message: str


# ---------------------------------------------------------------------------
# Background task
# ---------------------------------------------------------------------------

def _run_agent(datasheet_path: str | None, output_path: str | None) -> None:
    # Import lazily so the router can be mounted without requiring all deps at import time.
    # Works whether endpoint.py is run from the repo root (agents.example-schematic not a valid
    # package name due to the hyphen) or from within the agent directory itself.
    import importlib, sys as _sys
    _agent_dir = str(Path(__file__).parent)
    if _agent_dir not in _sys.path:
        _sys.path.insert(0, _agent_dir)
    agent = importlib.import_module("agent")
    from netlist_builder import build_netlist_summary  # noqa: PLC0415

    with _state_lock:
        _state["status"] = "running"
        _state["started_at"] = datetime.now(timezone.utc).isoformat()
        _state["finished_at"] = None
        _state["error"] = None
        _state["netlist_summary"] = None
        _state["output_path"] = None

    try:
        in_path = Path(datasheet_path) if datasheet_path else agent.INPUT_PATH
        out_path = Path(output_path) if output_path else agent.OUTPUT_PATH

        log.info("Starting example-schematic agent run: %s -> %s", in_path, out_path)
        schematic = agent.run(in_path, out_path)

        summary = build_netlist_summary(schematic)
        log.info("Netlist summary:\n%s", summary)

        with _state_lock:
            _state["status"] = "done"
            _state["finished_at"] = datetime.now(timezone.utc).isoformat()
            _state["output_path"] = str(out_path)
            _state["netlist_summary"] = summary

        log.info("Example-schematic agent run completed successfully")

    except Exception as exc:
        log.exception("Example-schematic agent run failed: %s", exc)
        with _state_lock:
            _state["status"] = "failed"
            _state["finished_at"] = datetime.now(timezone.utc).isoformat()
            _state["error"] = str(exc)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/run", response_model=RunResponse)
async def run_agent(request: RunRequest, background_tasks: BackgroundTasks) -> RunResponse:
    """
    Trigger an agent run in the background.
    Returns immediately with status='accepted'.
    Poll GET /agents/example-schematic/status for progress.
    """
    with _state_lock:
        current_status = _state["status"]

    if current_status == "running":
        raise HTTPException(status_code=409, detail="Agent is already running")

    background_tasks.add_task(
        _run_agent,
        request.datasheet_path,
        request.output_path,
    )

    return RunResponse(status="accepted", message="Agent run started in background")


@router.get("/status", response_model=StatusResponse)
async def get_status() -> StatusResponse:
    """
    Return the current status of the agent.
    """
    with _state_lock:
        snapshot = dict(_state)

    return StatusResponse(
        status=snapshot["status"],
        started_at=snapshot["started_at"],
        finished_at=snapshot["finished_at"],
        output_path=snapshot["output_path"],
        error=snapshot["error"],
        netlist_summary=snapshot["netlist_summary"],
    )
