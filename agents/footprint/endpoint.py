"""
agents/footprint/endpoint.py

FastAPI router for the Footprint Agent.

Routes:
  POST /agents/footprint/run     — trigger a footprint generation run
  GET  /agents/footprint/status  — return last run status and output paths
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

log = logging.getLogger("footprint.endpoint")

router = APIRouter(prefix="/agents/footprint", tags=["footprint"])

# ── shared state (in-memory for single-worker deployments) ────────────────
_state: dict[str, Any] = {
    "status": "idle",       # idle | running | done | failed
    "started_at": None,
    "finished_at": None,
    "duration_s": None,
    "output_files": [],
    "error": None,
    "result": None,
}

_OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "data/outputs"))


# ── Pydantic models ───────────────────────────────────────────────────────

class RunResponse(BaseModel):
    accepted: bool
    message: str
    status: str


class StatusResponse(BaseModel):
    status: str
    started_at: float | None
    finished_at: float | None
    duration_s: float | None
    output_files: list[str]
    error: str | None
    result_summary: dict[str, Any] | None


# ── background task ───────────────────────────────────────────────────────

def _run_agent() -> None:
    """Execute the footprint agent synchronously (called from background thread)."""
    import sys

    _agent_dir = str(Path(__file__).resolve().parent)
    if _agent_dir not in sys.path:
        sys.path.insert(0, _agent_dir)

    from agent import run  # type: ignore  # noqa: PLC0415

    _state["status"] = "running"
    _state["started_at"] = time.time()
    _state["finished_at"] = None
    _state["duration_s"] = None
    _state["output_files"] = []
    _state["error"] = None
    _state["result"] = None

    try:
        result = run()
        _state["status"] = "done"
        _state["result"] = {
            "name": result.get("name"),
            "package": result.get("package"),
            "pad_count": len(result.get("pads", [])),
        }

        # Collect output file paths
        files = []
        for fname in ("footprint.json", "footprint.kicad_mod"):
            p = _OUTPUT_DIR / fname
            if p.exists():
                files.append(str(p))
        _state["output_files"] = files

        log.info("Footprint agent finished successfully: %s", _state["result"])

    except Exception as exc:  # noqa: BLE001
        _state["status"] = "failed"
        _state["error"] = str(exc)
        log.exception("Footprint agent failed: %s", exc)

    finally:
        t_end = time.time()
        _state["finished_at"] = t_end
        start = _state["started_at"] or t_end
        _state["duration_s"] = round(t_end - start, 3)


# ── routes ────────────────────────────────────────────────────────────────

@router.post("/run", response_model=RunResponse, status_code=202)
async def run_footprint(background_tasks: BackgroundTasks) -> RunResponse:
    """
    Trigger a footprint generation run in the background.

    Returns immediately with HTTP 202 Accepted.
    Poll GET /agents/footprint/status for progress.
    Returns HTTP 409 if a run is already in progress.
    """
    if _state["status"] == "running":
        raise HTTPException(
            status_code=409,
            detail="A footprint generation run is already in progress.",
        )

    # Validate that the input file exists before starting
    datasheet_path = _OUTPUT_DIR / "datasheet.json"
    if not datasheet_path.exists():
        raise HTTPException(
            status_code=422,
            detail=(
                f"datasheet.json not found at {datasheet_path}. "
                "Run the datasheet-parser agent first."
            ),
        )

    background_tasks.add_task(_run_agent)

    return RunResponse(
        accepted=True,
        message="Footprint generation started. Poll /agents/footprint/status for updates.",
        status="running",
    )


@router.get("/status", response_model=StatusResponse)
async def get_status() -> StatusResponse:
    """
    Return the status of the most recent (or current) footprint generation run.

    Possible status values:
      idle    — no run has been triggered yet
      running — run is in progress
      done    — run completed successfully
      failed  — run failed; see 'error' field for details
    """
    return StatusResponse(
        status=_state["status"],
        started_at=_state["started_at"],
        finished_at=_state["finished_at"],
        duration_s=_state["duration_s"],
        output_files=_state["output_files"],
        error=_state["error"],
        result_summary=_state["result"],
    )
