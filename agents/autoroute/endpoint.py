"""
FastAPI router for the Auto-Route agent.

Routes:
  POST /agents/autoroute/run    — trigger a routing run
  GET  /agents/autoroute/status — get current run status
"""

from __future__ import annotations

import asyncio
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel, Field

# ── State ──────────────────────────────────────────────────────────────────────

class RunState:
    """Thread-safe state for the current (or last) routing run."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.status: Literal["idle", "running", "done", "failed"] = "idle"
        self.started_at: str | None = None
        self.finished_at: str | None = None
        self.error: str | None = None
        self.traces_count: int = 0
        self.vias_count: int = 0
        self.unrouted_nets: list[str] = []
        self.drc_passed: bool | None = None
        self.logs: list[str] = []

    def start(self) -> None:
        with self._lock:
            self.status = "running"
            self.started_at = datetime.utcnow().isoformat() + "Z"
            self.finished_at = None
            self.error = None
            self.traces_count = 0
            self.vias_count = 0
            self.unrouted_nets = []
            self.drc_passed = None
            self.logs = []

    def finish(self, result: dict) -> None:
        with self._lock:
            self.status = "done"
            self.finished_at = datetime.utcnow().isoformat() + "Z"
            self.traces_count = len(result.get("traces", []))
            self.vias_count = len(result.get("vias", []))

    def fail(self, error: str) -> None:
        with self._lock:
            self.status = "failed"
            self.finished_at = datetime.utcnow().isoformat() + "Z"
            self.error = error

    def append_log(self, msg: str) -> None:
        with self._lock:
            self.logs.append(msg)

    def to_dict(self) -> dict:
        with self._lock:
            return {
                "status": self.status,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "error": self.error,
                "traces_count": self.traces_count,
                "vias_count": self.vias_count,
                "unrouted_nets": list(self.unrouted_nets),
                "drc_passed": self.drc_passed,
                "logs": list(self.logs[-50:]),  # last 50 log lines
            }


_state = RunState()

# ── Router ─────────────────────────────────────────────────────────────────────

router = APIRouter(prefix="/agents/autoroute", tags=["autoroute"])

# Resolve paths relative to project root (2 levels up from this file)
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DATA_DIR = _PROJECT_ROOT / "data" / "outputs"


class RunRequest(BaseModel):
    """Optional overrides for the routing run."""
    placement_path: str = Field(
        default="",
        description="Override path to placement.json (empty = default)",
    )
    schematic_path: str = Field(
        default="",
        description="Override path to schematic.json (empty = default)",
    )
    output_path: str = Field(
        default="",
        description="Override path to write routing.json (empty = default)",
    )
    use_claude: bool = Field(
        default=True,
        description="Whether to use Anthropic API for routing optimisation",
    )


class RunResponse(BaseModel):
    message: str
    status: str


class StatusResponse(BaseModel):
    status: str
    started_at: str | None
    finished_at: str | None
    error: str | None
    traces_count: int
    vias_count: int
    unrouted_nets: list[str]
    drc_passed: bool | None
    logs: list[str]


def _run_in_thread(
    placement_path: Path,
    schematic_path: Path,
    output_path: Path,
    use_claude: bool,
) -> None:
    """Run the agent in a background thread and update shared state."""
    try:
        # Import here to avoid circular deps and to capture stdout to logs
        import io
        import sys as _sys

        # Redirect stdout to capture log lines
        original_stdout = _sys.stdout
        log_capture = io.StringIO()

        class TeeStream(io.TextIOWrapper):
            def write(self, data: str) -> int:
                log_capture.write(data)
                original_stdout.write(data)
                original_stdout.flush()
                if "\n" in data:
                    for line in data.splitlines():
                        line = line.strip()
                        if line:
                            _state.append_log(line)
                return len(data)

        tee = TeeStream(io.BytesIO(), encoding="utf-8")
        tee.write = lambda data: (  # type: ignore[method-assign]
            log_capture.write(data) or original_stdout.write(data) or
            [_state.append_log(l) for l in data.splitlines() if l.strip()] or len(data)
        )

        # Directly call the agent run function
        from agent import run, check_all_nets_routed, load_json, run_drc

        result = run(
            placement_path=placement_path,
            schematic_path=schematic_path,
            output_path=output_path,
            use_claude=use_claude,
        )

        # Check unrouted nets
        schematic = load_json(schematic_path)
        unrouted = check_all_nets_routed(schematic, result)
        with _state._lock:
            _state.unrouted_nets = unrouted

        # DRC status
        drc_ok, _ = run_drc(output_path)
        with _state._lock:
            _state.drc_passed = drc_ok

        _state.finish(result)

    except Exception as exc:
        _state.fail(str(exc))
        import traceback
        _state.append_log(f"ERROR: {exc}")
        _state.append_log(traceback.format_exc())


@router.post("/run", response_model=RunResponse, status_code=202)
async def run_autoroute(request: RunRequest, background_tasks: BackgroundTasks) -> RunResponse:
    """
    Trigger a PCB auto-route run.

    The run executes asynchronously. Poll GET /agents/autoroute/status to track progress.
    Returns 409 if a run is already in progress.
    """
    if _state.status == "running":
        raise HTTPException(
            status_code=409,
            detail="A routing run is already in progress. Poll /status for updates.",
        )

    # Resolve paths
    placement_path = (
        Path(request.placement_path) if request.placement_path
        else _DATA_DIR / "placement.json"
    )
    schematic_path = (
        Path(request.schematic_path) if request.schematic_path
        else _DATA_DIR / "schematic.json"
    )
    output_path = (
        Path(request.output_path) if request.output_path
        else _DATA_DIR / "routing.json"
    )

    # Validate inputs exist
    for p in (placement_path, schematic_path):
        if not p.exists():
            raise HTTPException(
                status_code=422,
                detail=f"Input file not found: {p}. Run earlier pipeline steps first.",
            )

    _state.start()
    _state.append_log(f"Starting autoroute run at {_state.started_at}")

    background_tasks.add_task(
        _run_in_thread,
        placement_path,
        schematic_path,
        output_path,
        request.use_claude,
    )

    return RunResponse(
        message="Routing run started. Poll /agents/autoroute/status for progress.",
        status="running",
    )


@router.get("/status", response_model=StatusResponse)
async def get_status() -> StatusResponse:
    """
    Return the current status of the auto-route agent.

    status values:
      idle    — no run has started yet
      running — a run is currently in progress
      done    — last run completed successfully
      failed  — last run failed (see error field)
    """
    data = _state.to_dict()
    return StatusResponse(**data)
