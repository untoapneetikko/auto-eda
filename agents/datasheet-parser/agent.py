"""
Datasheet Parser Agent

Extracts structured component data from a PDF datasheet using the Anthropic SDK,
validates the result against the shared schema, and writes it to data/outputs/datasheet.json.
"""

import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
import anthropic

# Load environment variables from .env at project root
load_dotenv(dotenv_path=Path(__file__).resolve().parents[2] / ".env")

# Resolve paths from environment or fall back to sensible defaults relative to project root
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.getenv("DATA_DIR", str(_PROJECT_ROOT / "data")))
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", str(DATA_DIR / "outputs")))
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", str(DATA_DIR / "uploads")))

SCHEMA_PATH = _PROJECT_ROOT / "shared" / "schemas" / "datasheet_output.json"
OUTPUT_PATH = OUTPUT_DIR / "datasheet.json"

# Add backend directory to path so tools can be imported
sys.path.insert(0, str(_PROJECT_ROOT / "backend"))

from tools.pdf_extractor import extract as pdf_extract  # noqa: E402
from tools.schema_validator import validate as schema_validate  # noqa: E402

SYSTEM_PROMPT = """You are a precision datasheet parser for electronic components.
Your ONLY job is to extract structured data from datasheet text with exact accuracy.

Rules:
- Extract ONLY information that is explicitly present in the datasheet text.
- If a value is not found, use null — never guess or invent values.
- Include ALL pins, including NC (no connect) pins.
- Pin types must be one of exactly: power, input, output, bidirectional, passive, nc
- Use IPC naming for footprint standards when possible (e.g. SOT-23, SOIC-8, QFN-16).
- example_application must come from the datasheet's application circuit section, never invented.
- Output valid JSON only — no markdown code fences, no explanation text, no trailing commas.
- The JSON must exactly match the schema provided.
"""

USER_PROMPT_TEMPLATE = """Extract structured component data from the following datasheet text.

Output a single JSON object matching this schema exactly:
{schema}

Important constraints:
- component_name: full part number as printed on the datasheet
- manufacturer: manufacturer name
- package: package designation (e.g. SOT-23, DIP-8, QFN-16)
- pins: every physical pin, including NC pins; type must be one of power/input/output/bidirectional/passive/nc
- footprint.standard: IPC standard name
- footprint.pad_count: total number of pads/pins
- footprint.pitch_mm: pin-to-pin pitch in mm (null if not applicable e.g. BGA)
- footprint.courtyard_mm: overall body dimensions in mm as {{x, y}}
- electrical.vcc_min / vcc_max: supply voltage range in volts (null if not stated)
- electrical.i_max_ma: maximum current in milliamps (null if not stated)
- example_application: extract from datasheet application section only
- raw_text: include the first 500 characters of the raw text verbatim
- source_pdf: the pdf file path provided

Datasheet raw text:
---
{raw_text}
---

Respond with the JSON object only. No markdown. No explanation."""


_last_run_status: dict = {"status": "idle", "error": None, "output_path": None}


def run(pdf_path: str) -> dict:
    """
    Run the datasheet parser agent on a PDF file.

    Args:
        pdf_path: Absolute or relative path to the PDF datasheet.

    Returns:
        The extracted and validated component data as a dict.

    Raises:
        FileNotFoundError: If the PDF does not exist.
        ValueError: If the Anthropic API returns unparseable JSON or schema validation fails.
        RuntimeError: If the ANTHROPIC_API_KEY is not set.
    """
    global _last_run_status

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        _last_run_status = {"status": "failed", "error": "ANTHROPIC_API_KEY not set", "output_path": None}
        raise RuntimeError("ANTHROPIC_API_KEY environment variable is not set.")

    pdf_path = str(pdf_path)
    if not os.path.isfile(pdf_path):
        _last_run_status = {"status": "failed", "error": f"PDF not found: {pdf_path}", "output_path": None}
        raise FileNotFoundError(f"PDF file not found: {pdf_path}")

    # Step 1: Extract raw text from PDF
    extraction = pdf_extract(pdf_path)
    raw_text: str = extraction.get("text", "")
    tables: list = extraction.get("tables", [])

    # Append table data as plain text rows so the model can see tabular pin data
    if tables:
        table_lines = ["\n\n[EXTRACTED TABLES]"]
        for table in tables:
            for row in table:
                if row:
                    table_lines.append("\t".join(str(cell) if cell is not None else "" for cell in row))
        raw_text += "\n".join(table_lines)

    # Load schema for embedding in prompt
    with open(SCHEMA_PATH) as f:
        schema_text = f.read()

    # Step 2: Call Anthropic SDK
    client = anthropic.Anthropic(api_key=api_key)

    user_prompt = USER_PROMPT_TEMPLATE.format(
        schema=schema_text,
        raw_text=raw_text[:12000],  # Truncate to stay within token limits for very large datasheets
    )

    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )

    response_text = message.content[0].text.strip()

    # Strip accidental markdown fences if the model wrapped the JSON
    if response_text.startswith("```"):
        lines = response_text.splitlines()
        # Remove first line (```json or ```) and last line (```)
        response_text = "\n".join(
            line for line in lines if not line.startswith("```")
        ).strip()

    # Step 3: Parse JSON response
    try:
        component_data = json.loads(response_text)
    except json.JSONDecodeError as e:
        _last_run_status = {
            "status": "failed",
            "error": f"Failed to parse JSON from model response: {e}",
            "output_path": None,
        }
        raise ValueError(f"Model returned invalid JSON: {e}\n\nRaw response:\n{response_text[:500]}") from e

    # Ensure source_pdf and raw_text are populated even if model omitted them
    component_data.setdefault("source_pdf", pdf_path)
    if not component_data.get("raw_text"):
        component_data["raw_text"] = extraction.get("text", "")[:500]

    # Step 4: Write output
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(component_data, f, indent=2, ensure_ascii=False)

    # Step 5: Validate against schema
    # Redirect stdout to a UTF-8 text wrapper so that schema_validator's emoji
    # output (✅ / ❌) does not crash on Windows consoles with narrow encodings.
    import io
    _saved_stdout = sys.stdout
    sys.stdout = io.TextIOWrapper(io.BytesIO(), encoding="utf-8", errors="replace")
    try:
        is_valid = schema_validate(str(OUTPUT_PATH), str(SCHEMA_PATH))
    finally:
        sys.stdout = _saved_stdout
    if not is_valid:
        _last_run_status = {
            "status": "failed",
            "error": "Output did not pass schema validation",
            "output_path": str(OUTPUT_PATH),
        }
        raise ValueError(f"Schema validation failed for {OUTPUT_PATH}. Check schema_validator output above.")

    _last_run_status = {"status": "done", "error": None, "output_path": str(OUTPUT_PATH)}
    return component_data


def get_last_run_status() -> dict:
    """Return status of the most recent run."""
    return dict(_last_run_status)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python agent.py <pdf_path>", file=sys.stderr)
        sys.exit(1)
    result = run(sys.argv[1])
    print(json.dumps(result, indent=2))
