"""
FastAPI router for the Auto-Route agent.

Routes:
  GET  /agents/autoroute/context  — return structured routing context (nets, pads, widths, board)
  GET  /agents/autoroute/seed     — run greedy MST seed routing, return routing JSON
  POST /agents/autoroute/apply    — accept routing dict, post-process, run DRC, write routing.json
  POST /agents/autoroute/run      — 501 Not Implemented (removed; orchestrator drives routing)
  GET  /agents/autoroute/status   — last apply() result status
"""

from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

# ── Router ─────────────────────────────────────────────────────────────────────

router = APIRouter(prefix="/agents/autoroute", tags=["autoroute"])

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DATA_DIR = _PROJECT_ROOT / "data" / "outputs"


# ── Shared last-apply state ────────────────────────────────────────────────────

class _ApplyState:
    """Thread-safe record of the most recent apply_routing() call."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.status: Literal["idle", "done", "failed"] = "idle"
        self.finished_at: str | None = None
        self.success: bool | None = None
        self.drc_passed: bool | None = None
        self.errors: list[str] = []
        self.traces_count: int = 0
        self.vias_count: int = 0
        self.unrouted_nets: list[str] = []

    def record(self, result: dict) -> None:
        with self._lock:
            self.finished_at = datetime.utcnow().isoformat() + "Z"
            self.success = result.get("success", False)
            self.status = "done" if self.success else "failed"
            self.drc_passed = result.get("drc_passed")
            self.errors = result.get("errors", [])
            self.traces_count = result.get("traces_count", 0)
            self.vias_count = result.get("vias_count", 0)
            self.unrouted_nets = result.get("unrouted_nets", [])

    def to_dict(self) -> dict:
        with self._lock:
            return {
                "status": self.status,
                "finished_at": self.finished_at,
                "success": self.success,
                "drc_passed": self.drc_passed,
                "errors": list(self.errors),
                "traces_count": self.traces_count,
                "vias_count": self.vias_count,
                "unrouted_nets": list(self.unrouted_nets),
            }


_state = _ApplyState()


# ── Pydantic models ─────────────────────────────────────────────────────────────

class ApplyRequest(BaseModel):
    """Routing dict from the orchestrator, optionally with path overrides."""
    routing: dict[str, Any] = Field(
        description="Routing dict with 'traces' and 'vias' keys"
    )
    output_dir: str = Field(
        default="",
        description="Override output directory (empty = default data/outputs/)",
    )
    schematic_path: str = Field(
        default="",
        description="Override schematic.json path (empty = default)",
    )


class ApplyResponse(BaseModel):
    success: bool
    drc_passed: bool | None
    errors: list[str]
    traces_count: int
    vias_count: int
    unrouted_nets: list[str]


class StatusResponse(BaseModel):
    status: str
    finished_at: str | None
    success: bool | None
    drc_passed: bool | None
    errors: list[str]
    traces_count: int
    vias_count: int
    unrouted_nets: list[str]


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.get("/context")
async def get_context(
    placement_path: str = "",
    schematic_path: str = "",
) -> dict:
    """
    Return structured routing context for the orchestrator.

    Reads placement.json and schematic.json and returns:
      - board dimensions
      - all nets with pad positions and pre-computed trace widths
      - no-route zones (crystal/oscillator refs)
      - raw component and placement lists

    Query params (both optional):
      placement_path — override path to placement.json
      schematic_path — override path to schematic.json
    """
    from agent import get_routing_context, PLACEMENT_FILE, SCHEMATIC_FILE

    p_path = Path(placement_path) if placement_path else PLACEMENT_FILE
    s_path = Path(schematic_path) if schematic_path else SCHEMATIC_FILE

    for p in (p_path, s_path):
        if not p.exists():
            raise HTTPException(
                status_code=422,
                detail=f"Input file not found: {p}. Run earlier pipeline steps first.",
            )

    try:
        return get_routing_context(p_path, s_path)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/seed")
async def get_seed_routing(
    placement_path: str = "",
    schematic_path: str = "",
) -> dict:
    """
    Run the greedy Manhattan/MST seed router and return the routing JSON.

    This is a deterministic, LLM-free fallback the orchestrator can use as a
    starting point or return as-is when no improvement is needed.

    Returns a routing dict: { "traces": [...], "vias": [...] }

    Query params (both optional):
      placement_path — override path to placement.json
      schematic_path — override path to schematic.json
    """
    from agent import _seed_routing, PLACEMENT_FILE, SCHEMATIC_FILE

    p_path = Path(placement_path) if placement_path else PLACEMENT_FILE
    s_path = Path(schematic_path) if schematic_path else SCHEMATIC_FILE

    for p in (p_path, s_path):
        if not p.exists():
            raise HTTPException(
                status_code=422,
                detail=f"Input file not found: {p}. Run earlier pipeline steps first.",
            )

    try:
        return _seed_routing(p_path, s_path)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/apply", response_model=ApplyResponse)
async def apply_routing_endpoint(request: ApplyRequest) -> ApplyResponse:
    """
    Accept a routing dict from the orchestrator, post-process it, run DRC,
    and write data/outputs/routing.json.

    Post-processing steps (all automatic):
      - Clamp trace widths to >= 0.15mm
      - Enforce power trace minimum 0.4mm
      - Default layer to F.Cu if missing
      - Remove zero-length path segments
      - Schema validation
      - DRC check

    Body:
      routing       — { "traces": [...], "vias": [...] }  (required)
      output_dir    — override output directory (optional)
      schematic_path — override schematic.json path (optional)
    """
    from agent import apply_routing, DATA_DIR, SCHEMATIC_FILE

    output_dir = Path(request.output_dir) if request.output_dir else DATA_DIR
    s_path = Path(request.schematic_path) if request.schematic_path else SCHEMATIC_FILE

    try:
        result = apply_routing(
            routing_dict=request.routing,
            output_dir=output_dir,
            schematic_path=s_path,
        )
    except Exception as exc:
        result = {
            "success": False,
            "drc_passed": None,
            "errors": [str(exc)],
            "traces_count": 0,
            "vias_count": 0,
            "unrouted_nets": [],
        }

    _state.record(result)
    return ApplyResponse(**result)


@router.post("/run", status_code=501)
async def run_deprecated() -> dict:
    """
    Removed. Claude Code (the orchestrator) now drives routing.

    Workflow:
      1. GET  /agents/autoroute/context  — fetch nets, pads, widths, board
      2. GET  /agents/autoroute/seed     — optional: fetch greedy seed as starting point
      3. (orchestrator reasons over context and produces routing dict)
      4. POST /agents/autoroute/apply    — submit routing, runs DRC, writes routing.json
    """
    raise HTTPException(
        status_code=501,
        detail=(
            "POST /run is no longer supported. "
            "Use GET /context to get routing context, "
            "GET /seed for a greedy seed routing, "
            "and POST /apply to submit the final routing dict."
        ),
    )


@router.get("/status", response_model=StatusResponse)
async def get_status() -> StatusResponse:
    """
    Return the result of the most recent POST /apply call.

    status values:
      idle   — no apply() call has been made yet this session
      done   — last apply() completed (check drc_passed for DRC result)
      failed — last apply() failed schema validation or raised an exception
    """
    return StatusResponse(**_state.to_dict())
