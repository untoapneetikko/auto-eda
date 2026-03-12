"""
agents/layout/endpoint.py — FastAPI router for the layout agent.

Mounts at /agents/layout and exposes:
  GET  /agents/layout/context  — return assembly context (input file summary)
  POST /agents/layout/apply    — run apply_layout() with optional board_meta
  POST /agents/layout/run      — triggers a full layout agent run (background thread)
  GET  /agents/layout/status   — returns current run status and output paths
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/agents/layout", tags=["layout"])

_OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "data/outputs"))

# ---------------------------------------------------------------------------
# In-memory run state (single-instance; replace with Redis for multi-worker)
# ---------------------------------------------------------------------------

_state: dict[str, Any] = {
    "status":      "idle",          # idle | running | done | error
    "started_at":  None,
    "finished_at": None,
    "message":     "Not yet started.",
    "result":      None,
}
_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class RunRequest(BaseModel):
    output_dir: str | None = None  # override output directory (optional)


class ApplyRequest(BaseModel):
    board_meta: dict | None = None  # optional overrides: project_name, revision, author
    output_dir: str | None = None


class RunResponse(BaseModel):
    accepted: bool
    message: str
    status: str


class StatusResponse(BaseModel):
    status: str
    started_at:  str | None
    finished_at: str | None
    message: str
    outputs: dict | None = None


# ---------------------------------------------------------------------------
# Shared agent import helper
# ---------------------------------------------------------------------------

def _import_agent():
    """Import the agent module from the same package directory."""
    import sys
    import importlib

    agent_dir = Path(__file__).parent
    if str(agent_dir) not in sys.path:
        sys.path.insert(0, str(agent_dir))

    return importlib.import_module("agent")


# ---------------------------------------------------------------------------
# Background task (used by /run)
# ---------------------------------------------------------------------------

def _run_agent(output_dir_override: str | None) -> None:
    """Execute the layout agent in a background thread."""
    try:
        agent_mod = _import_agent()
    except ImportError as exc:
        with _lock:
            _state["status"]      = "error"
            _state["finished_at"] = datetime.now(timezone.utc).isoformat()
            _state["message"]     = f"Import error: {exc}"
        return

    out_dir = Path(output_dir_override) if output_dir_override else None

    try:
        result = agent_mod.run(output_dir=out_dir)
        with _lock:
            _state["status"]      = "done" if result.get("status") == "ok" else "error"
            _state["finished_at"] = datetime.now(timezone.utc).isoformat()
            _state["message"]     = result.get("message", "")
            _state["result"]      = result
    except Exception as exc:
        with _lock:
            _state["status"]      = "error"
            _state["finished_at"] = datetime.now(timezone.utc).isoformat()
            _state["message"]     = f"Agent error: {exc}"


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/context")
async def get_context():
    """
    Return a summary of the 4 input files so the orchestrator can verify
    everything is ready before triggering assembly.

    Returns component count, net count, trace count, board dimensions,
    and a list of any missing files.
    """
    try:
        agent_mod = _import_agent()
    except ImportError as exc:
        raise HTTPException(status_code=500, detail=f"Import error: {exc}")

    try:
        context = agent_mod.get_assembly_context(output_dir=_OUTPUT_DIR)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Context error: {exc}")

    return context


@router.post("/apply")
async def apply_layout(req: ApplyRequest):
    """
    Run apply_layout() synchronously and return the result.

    Accepts optional board_meta overrides (project_name, revision, author)
    and an optional output_dir override.

    Returns success flag, output file paths, stats, and validation result.
    """
    try:
        agent_mod = _import_agent()
    except ImportError as exc:
        raise HTTPException(status_code=500, detail=f"Import error: {exc}")

    out_dir = Path(req.output_dir) if req.output_dir else _OUTPUT_DIR

    try:
        result = agent_mod.apply_layout(board_meta=req.board_meta, output_dir=out_dir)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Apply error: {exc}")

    if not result["success"]:
        raise HTTPException(status_code=422, detail=result)

    return result


@router.post("/run", response_model=RunResponse, status_code=202)
async def run_layout(req: RunRequest, background_tasks: BackgroundTasks):
    """
    Trigger a layout agent run asynchronously.

    Returns 202 Accepted immediately; poll GET /agents/layout/status for progress.
    Returns 409 if a run is already in progress.
    """
    with _lock:
        if _state["status"] == "running":
            raise HTTPException(
                status_code=409,
                detail="Layout agent is already running. Poll /agents/layout/status."
            )
        _state["status"]      = "running"
        _state["started_at"]  = datetime.now(timezone.utc).isoformat()
        _state["finished_at"] = None
        _state["message"]     = "Layout agent started."
        _state["result"]      = None

    # Offload to a thread so we don't block the event loop
    loop = asyncio.get_event_loop()
    loop.run_in_executor(None, _run_agent, req.output_dir)

    return RunResponse(
        accepted=True,
        message="Layout agent started. Poll /agents/layout/status for results.",
        status="running",
    )


@router.get("/status", response_model=StatusResponse)
async def get_status():
    """Return the current run status and output file paths (if done)."""
    with _lock:
        snap = dict(_state)

    outputs = None
    if snap["status"] == "done" and snap["result"]:
        res = snap["result"]
        outputs = {
            "final_layout_json":      res.get("final_layout_json"),
            "final_layout_kicad_pcb": res.get("final_layout_kicad_pcb"),
            "validation_passed":      res.get("validation_passed"),
        }

    return StatusResponse(
        status=snap["status"],
        started_at=snap.get("started_at"),
        finished_at=snap.get("finished_at"),
        message=snap.get("message", ""),
        outputs=outputs,
    )
