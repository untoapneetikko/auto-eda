"""
Datasheet Parser Agent — Pure Tool Layer

This module provides tool functions used by Claude Code (the orchestrator) to:
  1. Extract raw text and tables from a PDF datasheet.
  2. Persist a context dict so the orchestrator can read what needs processing.
  3. Accept already-extracted JSON (produced by the orchestrator), validate it,
     and write it to data/outputs/datasheet.json.

No Anthropic SDK calls are made here. The reasoning step is handled externally
by Claude Code.
"""

import io
import json
import os
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.getenv("DATA_DIR", str(_PROJECT_ROOT / "data")))
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", str(DATA_DIR / "outputs")))
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", str(DATA_DIR / "uploads")))

SCHEMA_PATH = _PROJECT_ROOT / "shared" / "schemas" / "datasheet_output.json"
OUTPUT_PATH = OUTPUT_DIR / "datasheet.json"
CONTEXT_PATH = OUTPUT_DIR / "datasheet_context.json"

# Add backend directory to path so tools can be imported
sys.path.insert(0, str(_PROJECT_ROOT / "backend"))

from tools.pdf_extractor import extract as pdf_extract  # noqa: E402
from tools.schema_validator import validate as schema_validate  # noqa: E402


# ---------------------------------------------------------------------------
# Public helpers kept for backward compatibility / utility
# ---------------------------------------------------------------------------


def extract_pdf(pdf_path: str) -> dict:
    """
    Extract raw text and tables from a PDF file.

    Args:
        pdf_path: Absolute or relative path to the PDF datasheet.

    Returns:
        dict with keys: text (str), tables (list), source_pdf (str).

    Raises:
        FileNotFoundError: If the PDF does not exist.
    """
    if not os.path.isfile(pdf_path):
        raise FileNotFoundError(f"PDF file not found: {pdf_path}")
    return pdf_extract(str(pdf_path))


def validate_output(data: dict, schema_path: str = None) -> bool:
    """
    Validate a component data dict against the shared JSON schema.

    Writes data to a temporary file then delegates to schema_validate so that
    the existing validator (which reads from disk) works unchanged.

    Args:
        data: The component data dict to validate.
        schema_path: Path to the JSON schema file. Defaults to the shared schema.

    Returns:
        True if valid, False otherwise.
    """
    import tempfile

    schema_path = schema_path or str(SCHEMA_PATH)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    ) as tmp:
        json.dump(data, tmp, indent=2, ensure_ascii=False)
        tmp_path = tmp.name

    # Suppress stdout so that emoji output from schema_validate doesn't crash
    # Windows consoles with narrow encodings.
    _saved_stdout = sys.stdout
    sys.stdout = io.TextIOWrapper(io.BytesIO(), encoding="utf-8", errors="replace")
    try:
        result = schema_validate(tmp_path, schema_path)
    finally:
        sys.stdout = _saved_stdout
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    return result


def write_output(data: dict, output_path: str = None) -> str:
    """
    Write component data dict to disk as JSON.

    Args:
        data: The component data dict.
        output_path: Destination path. Defaults to OUTPUT_PATH.

    Returns:
        The path that was written (as a string).
    """
    dest = Path(output_path) if output_path else OUTPUT_PATH
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    return str(dest)


# ---------------------------------------------------------------------------
# Orchestrator-facing functions
# ---------------------------------------------------------------------------


def run(pdf_path: str) -> dict:
    """
    Prepare a processing context from a PDF datasheet and persist it.

    This function replaces the old Anthropic-SDK-based extraction loop.
    It extracts raw text and tables from the PDF, builds a context dict,
    writes it to data/outputs/datasheet_context.json, and returns it.
    The actual JSON extraction (reasoning step) is performed externally by
    Claude Code, which then calls apply_extraction() with the result.

    Args:
        pdf_path: Absolute or relative path to the PDF datasheet.

    Returns:
        Context dict with keys: raw_text, tables, source_pdf, schema.

    Raises:
        FileNotFoundError: If the PDF does not exist.
    """
    pdf_path = str(pdf_path)
    if not os.path.isfile(pdf_path):
        raise FileNotFoundError(f"PDF file not found: {pdf_path}")

    # Extract text and tables from the PDF
    extraction = pdf_extract(pdf_path)
    raw_text: str = extraction.get("text", "")
    tables: list = extraction.get("tables", [])

    # Load schema so it is available in the context
    with open(SCHEMA_PATH, encoding="utf-8") as f:
        schema = json.load(f)

    context = {
        "raw_text": raw_text,
        "tables": tables,
        "source_pdf": pdf_path,
        "schema": schema,
    }

    # Persist context for the orchestrator to read
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONTEXT_PATH, "w", encoding="utf-8") as f:
        json.dump(context, f, indent=2, ensure_ascii=False)

    return context


def apply_extraction(extracted_json: dict, output_path: str = None) -> dict:
    """
    Accept already-extracted component JSON from the orchestrator, validate it
    against the schema, write datasheet.json, and return a result summary.

    Args:
        extracted_json: Component data dict produced by the orchestrator.
        output_path: Optional override for the output file path.

    Returns:
        dict with keys:
            success (bool)       — True if valid and written
            output_path (str)    — path where datasheet.json was written
            error (str | None)   — error message if validation failed
    """
    dest = Path(output_path) if output_path else OUTPUT_PATH

    # Write to disk (validator reads from disk)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, "w", encoding="utf-8") as f:
        json.dump(extracted_json, f, indent=2, ensure_ascii=False)

    # Validate
    _saved_stdout = sys.stdout
    sys.stdout = io.TextIOWrapper(io.BytesIO(), encoding="utf-8", errors="replace")
    try:
        is_valid = schema_validate(str(dest), str(SCHEMA_PATH))
    finally:
        sys.stdout = _saved_stdout

    if is_valid:
        return {"success": True, "output_path": str(dest), "error": None}
    else:
        return {
            "success": False,
            "output_path": str(dest),
            "error": "Output did not pass schema validation",
        }


def get_context() -> dict:
    """
    Read and return the current datasheet_context.json written by run().

    Returns:
        The context dict, or an empty dict if the file does not exist yet.
    """
    if not CONTEXT_PATH.exists():
        return {}
    with open(CONTEXT_PATH, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# CLI entry point — writes context and prints a summary
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python agent.py <pdf_path>", file=sys.stderr)
        sys.exit(1)
    ctx = run(sys.argv[1])
    summary = {
        "source_pdf": ctx["source_pdf"],
        "raw_text_chars": len(ctx.get("raw_text", "")),
        "table_count": len(ctx.get("tables", [])),
        "context_written_to": str(CONTEXT_PATH),
    }
    print(json.dumps(summary, indent=2))
