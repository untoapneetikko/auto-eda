"""
endpoint.py — FastAPI router for the Component Agent.

Exposes:
  POST /agents/component/run    — trigger a synchronous component agent run
  GET  /agents/component/status — report current status / last run result
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/agents/component", tags=["component"])

# ─── Shared run state (in-process; for production use Redis) ────────────────
_state: dict[str, Any] = {
    "status": "idle",          # idle | running | done | failed
    "started_at": None,        # ISO timestamp string
    "finished_at": None,
    "error": None,
    "output_summary": None,    # short summary of what was produced
}
_state_lock = threading.Lock()

OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "data/outputs"))
COMPONENT_JSON = OUTPUT_DIR / "component.json"


def _set_state(**kwargs: Any) -> None:
    with _state_lock:
        _state.update(kwargs)


# ─── Background worker ───────────────────────────────────────────────────────

def _run_agent() -> None:
    """Execute the component agent in a background thread."""
    from agents.component.agent import run  # local import to avoid circular deps

    _set_state(
        status="running",
        started_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        finished_at=None,
        error=None,
        output_summary=None,
    )
    try:
        result = run()
        sym = result.get("symbol", {})
        summary = {
            "component_name": sym.get("name"),
            "reference": sym.get("reference"),
            "pin_count": len(sym.get("pins", [])),
            "body_width_mm": sym.get("body", {}).get("width"),
            "body_height_mm": sym.get("body", {}).get("height"),
        }
        _set_state(
            status="done",
            finished_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            output_summary=summary,
        )
    except Exception as exc:
        _set_state(
            status="failed",
            finished_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            error=str(exc),
        )


# ─── Routes ─────────────────────────────────────────────────────────────────

@router.post("/run", summary="Trigger component agent run")
async def run_component_agent() -> dict:
    """
    Trigger the component agent.
    Reads data/outputs/datasheet.json, generates component.json and
    component.kicad_sym using the Anthropic API.

    Runs synchronously in a background thread. Poll GET /agents/component/status
    for completion.

    Returns 409 if an agent run is already in progress.
    """
    with _state_lock:
        current_status = _state["status"]

    if current_status == "running":
        raise HTTPException(
            status_code=409,
            detail="Component agent is already running. Poll /agents/component/status.",
        )

    thread = threading.Thread(target=_run_agent, daemon=True)
    thread.start()

    return {"message": "Component agent started", "status": "running"}


@router.get("/status", summary="Get component agent status")
async def get_component_status() -> dict:
    """
    Return the current status of the component agent and (if done) a summary
    of the last outputs produced.

    Status values:
    - idle    — agent has not been run yet
    - running — agent is currently executing
    - done    — last run succeeded
    - failed  — last run failed (see 'error' field)
    """
    with _state_lock:
        snapshot = dict(_state)

    # Attach output file info if available
    outputs: dict[str, Any] = {}
    if snapshot["status"] == "done":
        component_json_path = OUTPUT_DIR / "component.json"
        component_sym_path = OUTPUT_DIR / "component.kicad_sym"
        outputs["component_json"] = str(component_json_path) if component_json_path.exists() else None
        outputs["component_kicad_sym"] = str(component_sym_path) if component_sym_path.exists() else None

    return {**snapshot, "outputs": outputs}
