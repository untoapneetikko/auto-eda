"""
agents/footprint/agent.py

Footprint Agent — IPC-7351 PCB footprint generator using Anthropic SDK.

Pipeline:
  1. Read  data/outputs/datasheet.json
  2. Call  claude-sonnet-4-20250514 to design footprint per IPC-7351
  3. Write data/outputs/footprint.json  (validated against footprint_output.json)
  4. Write data/outputs/footprint.kicad_mod  (valid KiCad 7 S-expression)
  5. Validate both artefacts

Environment variables (from .env or environment):
  ANTHROPIC_API_KEY   – required
  OUTPUT_DIR          – default: data/outputs
  SCHEMA_DIR          – default: shared/schemas
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

import anthropic
from dotenv import load_dotenv

# ── resolve project root so relative paths always work ────────────────────
_AGENT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _AGENT_DIR.parent.parent

load_dotenv(_PROJECT_ROOT / ".env")

# ── logging ───────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [footprint] %(levelname)s %(message)s",
)
log = logging.getLogger("footprint")

# ── paths ─────────────────────────────────────────────────────────────────
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", str(_PROJECT_ROOT / "data" / "outputs")))
SCHEMA_DIR = Path(os.getenv("SCHEMA_DIR", str(_PROJECT_ROOT / "shared" / "schemas")))

DATASHEET_JSON = OUTPUT_DIR / "datasheet.json"
FOOTPRINT_JSON = OUTPUT_DIR / "footprint.json"
FOOTPRINT_KICAD = OUTPUT_DIR / "footprint.kicad_mod"
FOOTPRINT_SCHEMA = SCHEMA_DIR / "footprint_output.json"

MODEL = "claude-sonnet-4-20250514"


# ── IPC-7351 system prompt ────────────────────────────────────────────────
SYSTEM_PROMPT = """\
You are a precision PCB footprint engineer. You create KiCad-compatible footprints
that strictly follow IPC-7351B / IPC-7352 land-pattern standards.

CRITICAL RULES — never break these:
1. NEVER guess dimensions. Use only values found in the datasheet.
   If a value is absent, use the IPC-7351 nominal for that package type.
   Always record the source of each dimension in the "sources" field.
2. Manufacturing tolerances: add +0.1 mm to all land-pattern dimensions
   (pad width, pad height) beyond the nominal component lead dimensions.
3. Courtyard clearance: the courtyard rectangle must clear ALL pads by a
   minimum of 0.25 mm on every side.
4. Pin 1 marking: pin 1 must always be distinguishable — either a square
   pad shape (rect) when other pads are oval/round, or a silkscreen triangle/
   line marker at pad 1. Include both when possible.
5. Silkscreen must NOT overlap any copper pad. Keep silkscreen lines at
   least 0.2 mm from pad edges.
6. Reference designator on F.SilkS layer; value on F.Fab layer.
7. SMD pads must include F.Cu, F.Paste, and F.Mask layers.
8. Through-hole pads must include drill diameter from datasheet.
   If missing, use IPC-7251 column C (least-material condition) drill size.
9. All dimensions in millimetres to 4 decimal places.
10. Output ONLY valid JSON — no markdown, no prose.

Output schema (return exactly this JSON structure):
{
  "name": "<footprint name, IPC-7351 naming convention>",
  "package": "<package type, e.g. SOT-23, SOIC-8, QFN-16>",
  "ipc_standard": "IPC-7351B",
  "pad_count": <integer>,
  "pitch_mm": <float | null>,
  "sources": {
    "<dimension_key>": "<datasheet | IPC-7351 nominal>"
  },
  "pads": [
    {
      "number": <integer>,
      "type": "smd | thru_hole",
      "shape": "rect | oval | circle",
      "x": <float>,
      "y": <float>,
      "width": <float>,
      "height": <float>,
      "drill": <float | null>
    }
  ],
  "courtyard": {
    "x": <float>,
    "y": <float>,
    "width": <float>,
    "height": <float>
  },
  "silkscreen": [
    {"type": "line | arc | text", "data": { ... }}
  ],
  "format": "kicad_mod"
}

The courtyard x,y is the centre of the rectangle (usually 0,0 for symmetric parts).
width/height are the FULL dimensions (not half-extents).

For silkscreen lines, data = {"x1":f, "y1":f, "x2":f, "y2":f}.
For silkscreen arcs, data = {"x":f, "y":f, "radius":f, "start_angle":f, "end_angle":f}.
For silkscreen text, data = {"x":f, "y":f, "text":"string"}.

Pin 1 silkscreen marker: add a small triangle or a short line near pad 1
(outside the pad area) as an additional marker.
"""

USER_PROMPT_TEMPLATE = """\
Design an IPC-7351 PCB footprint for the following component.

DATASHEET DATA:
{datasheet_json}

Instructions:
- Package type: {package}
- Pin count: {pin_count}
- Use IPC-7351 land-pattern calculator values for this package where the
  datasheet does not provide exact land dimensions.
- Apply +0.1 mm manufacturing tolerance to all pad dimensions.
- Ensure courtyard clears all pads by at least 0.25 mm.
- Mark pin 1 with both a square pad shape AND a silkscreen line marker.
- Do not overlap silkscreen with any copper pad.
- Output valid JSON only — no markdown fences, no extra text.
"""


def _read_datasheet() -> dict:
    """Read and return datasheet.json. Raises if missing or malformed."""
    if not DATASHEET_JSON.exists():
        raise FileNotFoundError(f"datasheet.json not found at {DATASHEET_JSON}")
    with open(DATASHEET_JSON, encoding="utf-8") as f:
        data = json.load(f)
    log.info("Read datasheet.json: component=%s package=%s",
             data.get("component_name"), data.get("package"))
    return data


def _call_anthropic(datasheet: dict) -> dict:
    """
    Call claude-sonnet-4-20250514 and parse the returned JSON footprint dict.
    Retries once on JSON parse failure.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise EnvironmentError(
            "ANTHROPIC_API_KEY is not set. Add it to .env or the environment."
        )

    client = anthropic.Anthropic(api_key=api_key)

    package = datasheet.get("package", "unknown")
    pin_count = len(datasheet.get("pins", []))

    user_prompt = USER_PROMPT_TEMPLATE.format(
        datasheet_json=json.dumps(datasheet, indent=2),
        package=package,
        pin_count=pin_count,
    )

    log.info("Calling %s for package=%s pin_count=%d", MODEL, package, pin_count)

    for attempt in range(2):
        response = client.messages.create(
            model=MODEL,
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )

        raw = response.content[0].text.strip()
        log.debug("Raw LLM response (%d chars): %s...", len(raw), raw[:200])

        # Strip accidental markdown fences
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()

        try:
            footprint = json.loads(raw)
            log.info("Parsed footprint JSON successfully on attempt %d", attempt + 1)
            return footprint
        except json.JSONDecodeError as exc:
            log.warning("JSON parse failed (attempt %d): %s", attempt + 1, exc)
            if attempt == 1:
                raise ValueError(
                    f"LLM returned invalid JSON after 2 attempts: {exc}"
                ) from exc

    # unreachable
    raise RuntimeError("Unexpected exit from retry loop")


def _validate_footprint_json(data: dict) -> bool:
    """Validate footprint.json against its JSON schema using schema_validator."""
    import io
    from unittest.mock import patch as _patch

    # Add the backend/tools directory to path
    tools_dir = str(_PROJECT_ROOT / "backend" / "tools")
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)

    import schema_validator  # type: ignore

    # Redirect print to avoid Unicode console errors on Windows
    buf = io.StringIO()
    with _patch("builtins.print", lambda *a, **k: buf.write(" ".join(str(x) for x in a) + "\n")):
        result = schema_validator.validate(str(FOOTPRINT_JSON), str(FOOTPRINT_SCHEMA))

    log.info("Schema validation result: %s | %s", result, buf.getvalue().strip())
    return result


def _validate_kicad_mod() -> bool:
    """Validate .kicad_mod using kicad_validator."""
    import io
    from unittest.mock import patch as _patch

    tools_dir = str(_PROJECT_ROOT / "backend" / "tools")
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)

    import kicad_validator  # type: ignore

    buf = io.StringIO()
    with _patch("builtins.print", lambda *a, **k: buf.write(" ".join(str(x) for x in a) + "\n")):
        result = kicad_validator.validate_sexpr(str(FOOTPRINT_KICAD))

    log.info("KiCad validation result: %s | %s", result, buf.getvalue().strip())
    return result


def _strip_extra_fields(fp: dict) -> dict:
    """
    Return a copy of fp containing only the keys required by footprint_output.json
    so the schema validator does not trip over extra keys.
    """
    required_keys = {"name", "pads", "courtyard", "silkscreen", "format"}
    return {k: v for k, v in fp.items() if k in required_keys}


def run() -> dict:
    """
    Main entry point. Orchestrates the full footprint generation pipeline.
    Returns the footprint dict.
    Raises on validation failure.
    """
    # 1. Read input
    datasheet = _read_datasheet()

    # 2. Generate footprint via Anthropic
    footprint_full = _call_anthropic(datasheet)

    # Ensure required keys are present
    if "format" not in footprint_full:
        footprint_full["format"] = "kicad_mod"
    if "silkscreen" not in footprint_full:
        footprint_full["silkscreen"] = []

    # 3. Write full footprint JSON (includes extra fields like sources/package)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(FOOTPRINT_JSON, "w", encoding="utf-8") as f:
        json.dump(footprint_full, f, indent=2)
    log.info("Wrote %s", FOOTPRINT_JSON)

    # 4. Validate JSON against schema (use stripped version for strict validation)
    stripped = _strip_extra_fields(footprint_full)
    # Write stripped version temporarily to validate, then restore full version
    with open(FOOTPRINT_JSON, "w", encoding="utf-8") as f:
        json.dump(stripped, f, indent=2)

    valid_json = _validate_footprint_json(stripped)
    if not valid_json:
        raise ValueError("footprint.json failed schema validation")

    # Restore full footprint JSON after validation passes
    with open(FOOTPRINT_JSON, "w", encoding="utf-8") as f:
        json.dump(footprint_full, f, indent=2)

    # 5. Generate .kicad_mod
    # Import here to avoid circular imports at module level
    _agent_dir = str(_AGENT_DIR)
    if _agent_dir not in sys.path:
        sys.path.insert(0, _agent_dir)
    from kicad_mod_writer import write as write_kicad_mod  # type: ignore

    kicad_content = write_kicad_mod(footprint_full)
    with open(FOOTPRINT_KICAD, "w", encoding="utf-8") as f:
        f.write(kicad_content)
    log.info("Wrote %s", FOOTPRINT_KICAD)

    # 6. Validate .kicad_mod
    valid_kicad = _validate_kicad_mod()
    if not valid_kicad:
        raise ValueError("footprint.kicad_mod failed KiCad validation")

    log.info(
        "Footprint agent complete: %s  pads=%d",
        footprint_full.get("name"),
        len(footprint_full.get("pads", [])),
    )
    return footprint_full


if __name__ == "__main__":
    result = run()
    print(json.dumps(result, indent=2))
