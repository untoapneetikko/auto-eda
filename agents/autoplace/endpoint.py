"""
FastAPI router for the Auto-Place agent.

Endpoints:
    POST /agents/autoplace/run    — trigger a placement run (async via background task)
    GET  /agents/autoplace/status — return current run status

Mount this router in backend/main.py:
    from agents.autoplace.endpoint import router as autoplace_router
    app.include_router(autoplace_router)
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Lazy import of the agent so the router can be imported even without
# ANTHROPIC_API_KEY set (it is only needed at run time).
# ---------------------------------------------------------------------------

log = logging.getLogger(__name__)

router = APIRouter(prefix="/agents/autoplace", tags=["autoplace"])

_ROOT = Path(__file__).parent.parent.parent
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", str(_ROOT / "data" / "outputs")))
PLACEMENT_FILE = OUTPUT_DIR / "placement.json"

# ---------------------------------------------------------------------------
# In-memory job state (sufficient for single-node; swap for Redis in prod)
# ---------------------------------------------------------------------------

class _JobState:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, dict[str, Any]] = {}

    def create(self, job_id: str, params: dict[str, Any]) -> None:
        with self._lock:
            self._jobs[job_id] = {
                "job_id": job_id,
                "status": "queued",
                "started_at": time.time(),
                "finished_at": None,
                "error": None,
                "params": params,
                "result_path": None,
            }

    def update(self, job_id: str, **kwargs: Any) -> None:
        with self._lock:
            if job_id in self._jobs:
                self._jobs[job_id].update(kwargs)

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            return dict(self._jobs.get(job_id, {}))

    def latest(self) -> dict[str, Any] | None:
        """Return the most recently created job."""
        with self._lock:
            if not self._jobs:
                return None
            return dict(sorted(self._jobs.items(), key=lambda kv: kv[1]["started_at"])[-1][1])


_state = _JobState()

# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class RunRequest(BaseModel):
    schematic_path: str | None = Field(
        default=None,
        description="Absolute path to schematic.json.  Defaults to OUTPUT_DIR/schematic.json.",
    )
    board_width_mm: float = Field(default=100.0, description="PCB board width in mm")
    board_height_mm: float = Field(default=80.0, description="PCB board height in mm")


class RunResponse(BaseModel):
    job_id: str
    status: str
    message: str


class StatusResponse(BaseModel):
    job_id: str | None
    status: str
    started_at: float | None
    finished_at: float | None
    error: str | None
    result_path: str | None
    n_placements: int | None


# ---------------------------------------------------------------------------
# Background task
# ---------------------------------------------------------------------------


def _run_agent(job_id: str, params: dict[str, Any]) -> None:
    """Execute the placement agent in the background."""
    _state.update(job_id, status="running", started_at=time.time())

    try:
        # Import here to allow the module to load without ANTHROPIC_API_KEY.
        from agents.autoplace.agent import run_placement  # noqa: PLC0415

        schematic: dict[str, Any] | None = None
        if params.get("schematic_path"):
            with open(params["schematic_path"]) as f:
                schematic = json.load(f)

        result = run_placement(
            schematic=schematic,
            board_width=params.get("board_width_mm", 100.0),
            board_height=params.get("board_height_mm", 80.0),
        )

        _state.update(
            job_id,
            status="done",
            finished_at=time.time(),
            result_path=str(PLACEMENT_FILE),
            n_placements=len(result.get("placements", [])),
        )
        log.info("Job %s completed successfully (%d placements)", job_id, len(result.get("placements", [])))

    except Exception as exc:  # noqa: BLE001
        log.exception("Job %s failed: %s", job_id, exc)
        _state.update(
            job_id,
            status="failed",
            finished_at=time.time(),
            error=str(exc),
        )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("/run", response_model=RunResponse, summary="Trigger a placement run")
async def run_autoplace(request: RunRequest, background_tasks: BackgroundTasks) -> RunResponse:
    """
    Trigger an asynchronous component placement run.

    The agent reads schematic.json, calls Claude to compute optimal positions,
    validates with the courtyard optimizer, and writes placement.json.
    """
    job_id = str(uuid.uuid4())
    params = {
        "schematic_path": request.schematic_path,
        "board_width_mm": request.board_width_mm,
        "board_height_mm": request.board_height_mm,
    }
    _state.create(job_id, params)
    background_tasks.add_task(_run_agent, job_id, params)
    log.info("Queued placement job %s", job_id)
    return RunResponse(
        job_id=job_id,
        status="queued",
        message=f"Placement job {job_id} queued.  Poll GET /agents/autoplace/status?job_id={job_id}",
    )


@router.get("/status", response_model=StatusResponse, summary="Get placement run status")
async def get_status(job_id: str | None = None) -> StatusResponse:
    """
    Return status of a placement job.

    If `job_id` is omitted, returns the status of the most recent job.
    """
    if job_id:
        job = _state.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    else:
        job = _state.latest()
        if not job:
            # No jobs yet — check if a placement file exists from a previous run.
            if PLACEMENT_FILE.exists():
                with open(PLACEMENT_FILE) as f:
                    data = json.load(f)
                return StatusResponse(
                    job_id=None,
                    status="done",
                    started_at=None,
                    finished_at=None,
                    error=None,
                    result_path=str(PLACEMENT_FILE),
                    n_placements=len(data.get("placements", [])),
                )
            return StatusResponse(
                job_id=None,
                status="idle",
                started_at=None,
                finished_at=None,
                error=None,
                result_path=None,
                n_placements=None,
            )

    return StatusResponse(
        job_id=job.get("job_id"),
        status=job.get("status", "unknown"),
        started_at=job.get("started_at"),
        finished_at=job.get("finished_at"),
        error=job.get("error"),
        result_path=job.get("result_path"),
        n_placements=job.get("n_placements"),
    )
