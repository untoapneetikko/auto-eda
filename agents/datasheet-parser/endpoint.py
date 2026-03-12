"""
FastAPI router for the Datasheet Parser Agent.

Routes:
    POST /agents/datasheet-parser/run    — accepts {"pdf_path": "..."}, runs the agent
    GET  /agents/datasheet-parser/status — returns last run status
"""

import os
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator

import importlib.util
import sys as _sys
from pathlib import Path as _Path

# Load agent module from file path (directory name contains a hyphen, so normal import won't work)
_agent_path = _Path(__file__).parent / "agent.py"
_spec = importlib.util.spec_from_file_location("datasheet_parser_agent", _agent_path)
_agent = importlib.util.module_from_spec(_spec)
_sys.modules["datasheet_parser_agent"] = _agent
_spec.loader.exec_module(_agent)

router = APIRouter(prefix="/agents/datasheet-parser", tags=["datasheet-parser"])

# Allowed upload directory (resolved at import time)
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", str(_PROJECT_ROOT / "data" / "uploads")))


class RunRequest(BaseModel):
    pdf_path: str

    @field_validator("pdf_path")
    @classmethod
    def pdf_must_exist(cls, v: str) -> str:
        # Resolve relative paths against the upload directory
        p = Path(v)
        if not p.is_absolute():
            p = _UPLOAD_DIR / p
        if not p.exists():
            raise ValueError(f"PDF file not found: {p}")
        return str(p)


class RunResponse(BaseModel):
    status: str
    output_path: str | None
    data: dict


class StatusResponse(BaseModel):
    status: str
    error: str | None
    output_path: str | None


@router.post("/run", response_model=RunResponse)
async def run_agent(request: RunRequest) -> RunResponse:
    """
    Run the datasheet parser agent on the given PDF.

    The pdf_path may be an absolute path or a filename relative to the uploads directory.
    On success, returns the structured component data and the output file path.
    """
    try:
        data = _agent.run(request.pdf_path)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    status = _agent.get_last_run_status()
    return RunResponse(
        status=status["status"],
        output_path=status["output_path"],
        data=data,
    )


@router.get("/status", response_model=StatusResponse)
async def get_status() -> StatusResponse:
    """Return the status of the most recent agent run."""
    status = _agent.get_last_run_status()
    return StatusResponse(
        status=status["status"],
        error=status.get("error"),
        output_path=status.get("output_path"),
    )
