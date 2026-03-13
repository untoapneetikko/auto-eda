"""
FastAPI router for the Datasheet Parser Agent.

Routes:
    POST /agents/datasheet-parser/extract  — runs extract_pdf, writes context,
                                             returns raw text preview + tables summary
    POST /agents/datasheet-parser/apply    — accepts {extracted_json: {...}},
                                             validates and writes datasheet.json
    GET  /agents/datasheet-parser/context  — returns the current context file
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


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class ExtractRequest(BaseModel):
    pdf_path: str

    @field_validator("pdf_path")
    @classmethod
    def pdf_must_exist(cls, v: str) -> str:
        p = Path(v)
        if not p.is_absolute():
            p = _UPLOAD_DIR / p
        if not p.exists():
            raise ValueError(f"PDF file not found: {p}")
        return str(p)


class ExtractResponse(BaseModel):
    status: str
    raw_text_preview: str          # first 500 chars of extracted text
    table_count: int               # number of tables found
    context_path: str              # where context was written


class ApplyRequest(BaseModel):
    extracted_json: dict


class ApplyResponse(BaseModel):
    success: bool
    output_path: str | None
    error: str | None


class ContextResponse(BaseModel):
    available: bool
    context: dict


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("/extract", response_model=ExtractResponse)
async def extract_endpoint(request: ExtractRequest) -> ExtractResponse:
    """
    Extract raw text and tables from the uploaded PDF and write a context file.

    Returns a preview of the raw text (first 500 chars) and a count of tables
    found, so the orchestrator knows what material is available for reasoning.
    """
    try:
        ctx = _agent.run(request.pdf_path)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

    raw_text: str = ctx.get("raw_text", "")
    tables: list = ctx.get("tables", [])

    return ExtractResponse(
        status="ok",
        raw_text_preview=raw_text[:500],
        table_count=len(tables),
        context_path=str(_agent.CONTEXT_PATH),
    )


@router.post("/apply", response_model=ApplyResponse)
async def apply_endpoint(request: ApplyRequest) -> ApplyResponse:
    """
    Apply already-extracted JSON (produced by the orchestrator/Claude Code).

    Validates against the shared schema and writes datasheet.json.
    """
    try:
        result = _agent.apply_extraction(request.extracted_json)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

    return ApplyResponse(
        success=result["success"],
        output_path=result.get("output_path"),
        error=result.get("error"),
    )


@router.get("/context", response_model=ContextResponse)
async def context_endpoint() -> ContextResponse:
    """
    Return the current datasheet_context.json written by the extract step.

    If no context has been written yet, returns {available: false, context: {}}.
    """
    ctx = _agent.get_context()
    return ContextResponse(available=bool(ctx), context=ctx)
