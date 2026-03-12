"""
endpoint.py — FastAPI router for the Connectivity Agent.

Routes:
  POST /agents/connectivity/check   — check_connectivity(body.schematic) in-memory, no file I/O
  POST /agents/connectivity/run     — load schematic.json from disk, write connectivity.json
  GET  /agents/connectivity/status  — last run result
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

_last_run: dict[str, Any] = {
    "state": "idle",
    "started_at": None,
    "finished_at": None,
    "valid": None,
    "error_count": None,
    "warning_count": None,
    "errors": [],
    "output_path": None,
}

_OUTPUT_PATH = Path(
    os.getenv("OUTPUT_DIR", Path(__file__).parent.parent.parent / "data" / "outputs")
) / "connectivity.json"

# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/agents/connectivity", tags=["connectivity"])


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class CheckRequest(BaseModel):
    schematic: dict[str, Any]


class CheckResponse(BaseModel):
    valid: bool
    error_count: int
    warning_count: int
    issues: list[dict[str, Any]]
    stats: dict[str, Any]


class RunResponse(BaseModel):
    success: bool
    valid: bool
    error_count: int
    warning_count: int
    errors: list[str]
    output_path: str


class StatusResponse(BaseModel):
    state: str
    started_at: float | None
    finished_at: float | None
    valid: bool | None
    error_count: int | None
    warning_count: int | None
    errors: list[str]
    output_path: str | None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/check", response_model=CheckResponse)
def check_endpoint(body: CheckRequest) -> CheckResponse:
    """
    Run connectivity checks on the supplied schematic dict (in-memory, no file I/O).

    Returns the full connectivity report.

    Raises:
      422 — if schematic body is missing or empty
      500 — on unexpected errors
    """
    if not body.schematic:
        raise HTTPException(status_code=422, detail="schematic must not be empty")

    try:
        from agent import check_connectivity  # noqa: PLC0415
        report = check_connectivity(body.schematic)
        return CheckResponse(
            valid=report["valid"],
            error_count=report["stats"]["error_count"],
            warning_count=report["stats"]["warning_count"],
            issues=report["issues"],
            stats=report["stats"],
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc


@router.post("/run", response_model=RunResponse)
def run_endpoint() -> RunResponse:
    """
    Load data/outputs/schematic.json, run connectivity checks, write
    data/outputs/connectivity.json.

    Returns {success, valid, error_count, warning_count, errors, output_path}.

    Raises:
      404 — if schematic.json is missing
      500 — on unexpected errors
    """
    _last_run.update(
        state="running",
        started_at=time.time(),
        finished_at=None,
        valid=None,
        error_count=None,
        warning_count=None,
        errors=[],
        output_path=None,
    )

    try:
        from agent import run_from_file  # noqa: PLC0415
        result = run_from_file()

        _last_run.update(
            state="done",
            finished_at=time.time(),
            valid=result["report"]["valid"],
            error_count=result["report"]["stats"]["error_count"],
            warning_count=result["report"]["stats"]["warning_count"],
            errors=result["errors"],
            output_path=str(_OUTPUT_PATH),
        )

        return RunResponse(
            success=result["success"],
            valid=result["report"]["valid"],
            error_count=result["report"]["stats"]["error_count"],
            warning_count=result["report"]["stats"]["warning_count"],
            errors=result["errors"],
            output_path=str(_OUTPUT_PATH),
        )

    except FileNotFoundError as exc:
        _last_run.update(state="error", finished_at=time.time(), errors=[str(exc)])
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    except Exception as exc:  # noqa: BLE001
        import traceback
        tb = traceback.format_exc()
        _last_run.update(
            state="error",
            finished_at=time.time(),
            errors=[f"{type(exc).__name__}: {exc}\n{tb}"],
        )
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc


@router.get("/status", response_model=StatusResponse)
def get_status() -> StatusResponse:
    """Return the status of the last run call."""
    return StatusResponse(**_last_run)
