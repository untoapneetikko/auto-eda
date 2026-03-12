"""
agents/layout/endpoint.py — FastAPI router for the layout agent.

Mounts at /agents/layout and exposes:
  POST /agents/layout/run    — triggers a full layout agent run
  GET  /agents/layout/status — returns current run status and output paths
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
    "status":   "idle",          # idle | running | done | error
    "started_at":  None,
    "finished_at": None,
    "message":  "Not yet started.",
    "result":   None,
}
_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class RunRequest(BaseModel):
    output_dir: str | None = None  # override output directory (optional)


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
# Background task
# ---------------------------------------------------------------------------

def _run_agent(output_dir_override: str | None) -> None:
    """Execute the layout agent in a background thread."""
    # Import here to avoid circular imports at module load time
    import sys, importlib

    # Resolve the agent module path (same package directory)
    agent_dir = Path(__file__).parent
    if str(agent_dir) not in sys.path:
        sys.path.insert(0, str(agent_dir))

    try:
        agent_mod = importlib.import_module("agent")
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
        # Include AI review summary if present
        ai_review = res.get("ai_review")
        if ai_review:
            outputs["ai_review"] = ai_review

    return StatusResponse(
        status=snap["status"],
        started_at=snap.get("started_at"),
        finished_at=snap.get("finished_at"),
        message=snap.get("message", ""),
        outputs=outputs,
    )
