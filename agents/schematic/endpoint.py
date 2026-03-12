"""
endpoint.py — FastAPI router for the Schematic Agent.

Routes:
  GET  /agents/schematic/context  — get_design_context() summary for the orchestrator
  POST /agents/schematic/apply    — apply_schematic(body.schematic)
  GET  /agents/schematic/status   — last apply result
  POST /agents/schematic/run      — 501 stub (removed; orchestrator does reasoning)
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

_last_apply: dict[str, Any] = {
    "state": "idle",       # idle | done | error
    "started_at": None,
    "finished_at": None,
    "success": None,
    "errors": [],
    "output_path": None,
}

_OUTPUT_PATH = Path(
    os.getenv("OUTPUT_DIR", Path(__file__).parent.parent.parent / "data" / "outputs")
) / "schematic.json"

# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/agents/schematic", tags=["schematic"])


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class ApplyRequest(BaseModel):
    schematic: dict[str, Any]


class ApplyResponse(BaseModel):
    success: bool
    errors: list[str]
    output_path: str


class StatusResponse(BaseModel):
    state: str
    started_at: float | None
    finished_at: float | None
    success: bool | None
    errors: list[str]
    output_path: str | None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/context")
def get_context() -> dict[str, Any]:
    """
    Return a compact design context for the orchestrator.

    Reads data/outputs/datasheet.json + component.json + example_schematic.json
    and returns component name, pin list, example net names, and suggested net names
    from net_namer.

    Raises:
      404 — if datasheet.json or component.json are missing
    """
    try:
        from agent import get_design_context  # noqa: PLC0415
        return get_design_context()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc


@router.post("/apply", response_model=ApplyResponse)
def apply_schematic_endpoint(body: ApplyRequest) -> ApplyResponse:
    """
    Accept a schematic dict, enforce no-floating-pins, validate against schema,
    and write data/outputs/schematic.json.

    Returns {success, errors, output_path}.

    Raises:
      422 — if schematic body is missing or empty
      500 — on unexpected errors
    """
    if not body.schematic:
        raise HTTPException(status_code=422, detail="schematic must not be empty")

    _last_apply.update(
        state="running",
        started_at=time.time(),
        finished_at=None,
        success=None,
        errors=[],
        output_path=None,
    )

    try:
        from agent import apply_schematic  # noqa: PLC0415

        result = apply_schematic(body.schematic)

        _last_apply.update(
            state="done",
            finished_at=time.time(),
            success=result["success"],
            errors=result["errors"],
            output_path=str(_OUTPUT_PATH),
        )

        return ApplyResponse(
            success=result["success"],
            errors=result["errors"],
            output_path=str(_OUTPUT_PATH),
        )

    except Exception as exc:  # noqa: BLE001
        import traceback
        tb = traceback.format_exc()
        _last_apply.update(
            state="error",
            finished_at=time.time(),
            success=False,
            errors=[f"{type(exc).__name__}: {exc}\n{tb}"],
        )
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc


@router.get("/status", response_model=StatusResponse)
def get_status() -> StatusResponse:
    """Return the status of the last apply call."""
    return StatusResponse(**_last_apply)


@router.post("/run")
def run_schematic_stub() -> None:
    """
    Removed — the orchestrator (Claude Code) now handles reasoning.
    Use GET /agents/schematic/context then POST /agents/schematic/apply instead.
    """
    raise HTTPException(
        status_code=501,
        detail=(
            "run() has been removed. "
            "Call GET /agents/schematic/context to read design inputs, "
            "then POST /agents/schematic/apply with your schematic dict."
        ),
    )
