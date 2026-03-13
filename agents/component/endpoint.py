"""
endpoint.py — FastAPI router for the Component Agent.

Exposes:
  GET  /agents/component/datasheet-summary — compact summary the orchestrator needs
  POST /agents/component/apply             — validate + write symbol given by orchestrator
  POST /agents/component/run               — 501 Not Implemented (removed; use /apply)
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/agents/component", tags=["component"])

OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "data/outputs"))


# ─── Request / response models ───────────────────────────────────────────────

class ApplyRequest(BaseModel):
    symbol: dict[str, Any]


# ─── Routes ─────────────────────────────────────────────────────────────────

@router.get("/datasheet-summary", summary="Get compact datasheet summary for orchestrator")
async def datasheet_summary() -> dict:
    """
    Read data/outputs/datasheet.json and return a compact summary:
      - component_name
      - package
      - pin_count
      - pin_list  (number, name, type per pin)

    The orchestrator uses this to design the symbol before calling /apply.
    """
    from agents.component.agent import get_datasheet_summary  # local import

    try:
        return get_datasheet_summary()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/apply", summary="Apply an orchestrator-designed symbol dict")
async def apply_symbol(request: ApplyRequest) -> dict:
    """
    Accept a symbol dict already designed by the orchestrator, validate it
    against the component_output.json schema, generate component.kicad_sym,
    and write both files to data/outputs/.

    Request body:
        { "symbol": { "name": "...", "reference": "...", "pins": [...], "body": {...}, "format": "kicad_sym" } }

    Returns:
        { "success": bool, "files": { "component_json": "...", "component_kicad_sym": "..." }, "errors": [...] }
    """
    from agents.component.agent import apply_symbol as _apply  # local import

    # Wrap back into the top-level dict the schema expects
    symbol_dict = {"symbol": request.symbol}

    result = _apply(symbol_dict, output_dir=OUTPUT_DIR)

    if not result["success"]:
        raise HTTPException(
            status_code=422,
            detail={"message": "Symbol validation or write failed", "errors": result["errors"]},
        )

    return result


@router.post("/run", summary="Removed — use /apply instead")
async def run_removed() -> dict:
    """
    This endpoint has been removed.

    The old /run endpoint called the Anthropic API internally. Reasoning is now
    handled by the orchestrator (Claude Code). Use:

      GET  /agents/component/datasheet-summary  — to get component info
      POST /agents/component/apply              — to write the symbol

    Returns HTTP 501 Not Implemented.
    """
    raise HTTPException(
        status_code=501,
        detail=(
            "POST /agents/component/run is no longer available. "
            "Reasoning is handled by the orchestrator. "
            "Use GET /agents/component/datasheet-summary and "
            "POST /agents/component/apply instead."
        ),
    )
