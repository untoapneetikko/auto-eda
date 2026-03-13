"""
FastAPI router for the Auto-Place agent.

Endpoints:
    GET  /agents/autoplace/context  — return placement context from schematic.json
    POST /agents/autoplace/apply    — validate and persist a placement dict
    POST /agents/autoplace/run      — 501 Not Implemented (orchestration is in Claude Code)
    GET  /agents/autoplace/status   — 501 Not Implemented (no async jobs any more)

Mount this router in backend/main.py:
    from agents.autoplace.endpoint import router as autoplace_router
    app.include_router(autoplace_router)
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

log = logging.getLogger(__name__)

router = APIRouter(prefix="/agents/autoplace", tags=["autoplace"])

_ROOT = Path(__file__).parent.parent.parent
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", str(_ROOT / "data" / "outputs")))

# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class ContextResponse(BaseModel):
    project_name: str
    board: dict[str, float]
    components: list[dict[str, Any]]
    net_connections: dict[str, list[str]]


class ApplyRequest(BaseModel):
    placement_dict: dict[str, Any] = Field(
        description="Placement dict with 'board' and 'placements' keys."
    )
    board_width_mm: float = Field(default=100.0, description="PCB board width in mm")
    board_height_mm: float = Field(default=80.0, description="PCB board height in mm")


class ApplyResponse(BaseModel):
    success: bool
    violations: list[dict[str, Any]]
    drc_passed: bool
    drc_output: str
    placement_path: str | None
    message: str


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get(
    "/context",
    response_model=ContextResponse,
    summary="Return placement context from schematic.json",
)
async def get_context(
    schematic_path: str | None = None,
    board_width_mm: float = 100.0,
    board_height_mm: float = 80.0,
) -> ContextResponse:
    """
    Read schematic.json and return structured placement context.

    The orchestrator (Claude Code) reads this before designing placements.
    """
    from agents.autoplace.agent import get_placement_context  # noqa: PLC0415

    try:
        ctx = get_placement_context(
            schematic_path=schematic_path,
            board_width_mm=board_width_mm,
            board_height_mm=board_height_mm,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        log.exception("get_placement_context failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return ContextResponse(**ctx)


@router.post(
    "/apply",
    response_model=ApplyResponse,
    summary="Validate and persist a placement dict",
)
async def apply_placement_endpoint(request: ApplyRequest) -> ApplyResponse:
    """
    Apply a placement dict produced by the orchestrator.

    Runs courtyard overlap check (Polars) and external DRC, then writes
    data/outputs/placement.json.  Returns success status and any violations.
    """
    from agents.autoplace.agent import apply_placement  # noqa: PLC0415

    # Inject board dimensions into the placement dict if not already present.
    placement_dict = dict(request.placement_dict)
    placement_dict.setdefault("board", {})
    placement_dict["board"].setdefault("width_mm", request.board_width_mm)
    placement_dict["board"].setdefault("height_mm", request.board_height_mm)

    try:
        result = apply_placement(placement_dict=placement_dict)
    except Exception as exc:  # noqa: BLE001
        log.exception("apply_placement failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return ApplyResponse(**result)


@router.post(
    "/run",
    status_code=501,
    summary="Not implemented — orchestration is in Claude Code",
)
async def run_autoplace() -> dict[str, str]:
    """
    Trigger a placement run.

    This endpoint is no longer implemented.  Orchestration is handled by
    Claude Code.  Use GET /context then POST /apply instead.
    """
    raise HTTPException(
        status_code=501,
        detail=(
            "POST /run is not implemented. "
            "Use GET /agents/autoplace/context to read schematic data, "
            "then POST /agents/autoplace/apply with the placement dict."
        ),
    )


@router.get(
    "/status",
    status_code=501,
    summary="Not implemented — no async jobs any more",
)
async def get_status() -> dict[str, str]:
    """
    Return run status.

    This endpoint is no longer implemented.  apply_placement is synchronous.
    """
    raise HTTPException(
        status_code=501,
        detail=(
            "GET /status is not implemented. "
            "POST /agents/autoplace/apply is synchronous and returns results directly."
        ),
    )
