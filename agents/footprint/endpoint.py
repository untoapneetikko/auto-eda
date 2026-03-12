"""
agents/footprint/endpoint.py

FastAPI router for the Footprint Agent (pure-tools layer).

Routes:
  GET  /agents/footprint/datasheet-summary  — read datasheet.json, return compact summary
  POST /agents/footprint/apply              — validate + write footprint designed by orchestrator
  POST /agents/footprint/run               — 501 Not Implemented (removed; use apply instead)
  GET  /agents/footprint/status            — last apply() result status
"""

from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

log = logging.getLogger("footprint.endpoint")

router = APIRouter(prefix="/agents/footprint", tags=["footprint"])

# ── ensure agent module is importable ─────────────────────────────────────
_AGENT_DIR = str(Path(__file__).resolve().parent)
if _AGENT_DIR not in sys.path:
    sys.path.insert(0, _AGENT_DIR)

_OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "data/outputs"))

# ── in-memory state for the last apply() call ────────────────────────────
_state: dict[str, Any] = {
    "status": "idle",       # idle | done | failed
    "finished_at": None,
    "output_files": [],
    "error": None,
    "result": None,
}


# ── Pydantic models ───────────────────────────────────────────────────────

class DatasheetSummaryResponse(BaseModel):
    component_name: str
    package: str
    pad_count: int | None
    pitch_mm: float | None
    courtyard_mm: dict | None
    electrical: dict | None


class ApplyRequest(BaseModel):
    footprint: dict[str, Any]
    output_dir: str | None = None


class ApplyResponse(BaseModel):
    success: bool
    files: list[str]
    errors: list[str]


class StatusResponse(BaseModel):
    status: str
    finished_at: float | None
    output_files: list[str]
    error: str | None
    result_summary: dict[str, Any] | None


# ── routes ────────────────────────────────────────────────────────────────

@router.get("/datasheet-summary", response_model=DatasheetSummaryResponse)
async def get_datasheet_summary_endpoint() -> DatasheetSummaryResponse:
    """
    Read data/outputs/datasheet.json and return a compact summary that the
    orchestrator (Claude Code) uses to design the footprint.

    Returns HTTP 404 if datasheet.json does not exist.
    Returns HTTP 422 if the file is malformed.
    """
    from agent import get_datasheet_summary  # type: ignore  # noqa: PLC0415

    try:
        summary = get_datasheet_summary()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return DatasheetSummaryResponse(
        component_name=summary["component_name"],
        package=summary["package"],
        pad_count=summary.get("pad_count"),
        pitch_mm=summary.get("pitch_mm"),
        courtyard_mm=summary.get("courtyard_mm"),
        electrical=summary.get("electrical"),
    )


@router.post("/apply", response_model=ApplyResponse)
async def apply_footprint_endpoint(body: ApplyRequest) -> ApplyResponse:
    """
    Accept a footprint dict designed by the orchestrator (Claude Code).

    Validates the dict against shared/schemas/footprint_output.json, generates
    a .kicad_mod file via kicad_mod_writer, validates it, then writes both
    artefacts to data/outputs/.

    Returns { success, files, errors }.
    """
    from agent import apply_footprint  # type: ignore  # noqa: PLC0415

    result = apply_footprint(
        footprint_dict=body.footprint,
        output_dir=body.output_dir,
    )

    # Update in-memory state
    _state["status"] = "done" if result["success"] else "failed"
    _state["finished_at"] = time.time()
    _state["output_files"] = result["files"]
    _state["error"] = result["errors"][0] if result["errors"] else None
    _state["result"] = (
        {
            "name": body.footprint.get("name"),
            "package": body.footprint.get("package"),
            "pad_count": len(body.footprint.get("pads", [])),
        }
        if result["success"]
        else None
    )

    return ApplyResponse(
        success=result["success"],
        files=result["files"],
        errors=result["errors"],
    )


@router.post("/run", status_code=501)
async def run_not_implemented() -> dict[str, str]:
    """
    Removed. The orchestrator (Claude Code) now handles reasoning.

    Use GET /agents/footprint/datasheet-summary to read inputs, design the
    footprint, then POST /agents/footprint/apply to write outputs.
    """
    raise HTTPException(
        status_code=501,
        detail=(
            "POST /agents/footprint/run has been removed. "
            "Use GET /agents/footprint/datasheet-summary + "
            "POST /agents/footprint/apply instead."
        ),
    )


@router.get("/status", response_model=StatusResponse)
async def get_status() -> StatusResponse:
    """
    Return the status of the most recent apply() call.

    Possible status values:
      idle    — no apply() call has been made yet
      done    — last apply() succeeded
      failed  — last apply() failed; see 'error' field for details
    """
    return StatusResponse(
        status=_state["status"],
        finished_at=_state["finished_at"],
        output_files=_state["output_files"],
        error=_state["error"],
        result_summary=_state["result"],
    )
