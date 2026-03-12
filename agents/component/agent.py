"""
agent.py — Component Agent

Reads data/outputs/datasheet.json (output from the datasheet-parser agent),
uses the Anthropic SDK to design a KiCad schematic symbol following the
Symbol Design Rules from agents/component/CLAUDE.md, produces component.json
and component.kicad_sym, and validates both outputs.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import anthropic
from dotenv import load_dotenv

# Allow running from any working directory
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from agents.component.kicad_sym_writer import write_kicad_sym
from backend.tools.schema_validator import validate as validate_schema
from backend.tools.kicad_validator import validate_sexpr

load_dotenv(dotenv_path=REPO_ROOT / ".env")

# ─── Path constants (overridable via env) ───────────────────────────────────
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", str(REPO_ROOT / "data" / "outputs")))
DATASHEET_JSON = OUTPUT_DIR / "datasheet.json"
COMPONENT_JSON = OUTPUT_DIR / "component.json"
COMPONENT_KICAD_SYM = OUTPUT_DIR / "component.kicad_sym"
COMPONENT_SCHEMA = REPO_ROOT / "shared" / "schemas" / "component_output.json"

ANTHROPIC_MODEL = "claude-sonnet-4-20250514"

# ─── System prompt ──────────────────────────────────────────────────────────
SYSTEM_PROMPT = """\
You are a KiCad schematic symbol designer. You receive parsed datasheet data and
produce an optimal, human-readable schematic symbol with correct pin placement.

## Symbol Design Rules
1. Power pins (VCC, GND, POWER, VDD, VSS, VEE, VBAT, VIN, VOUT — anything power-related) go on top and bottom edges
2. Input pins go on the LEFT side
3. Output pins go on the RIGHT side
4. Bidirectional pins go on the RIGHT side
5. NC (no-connect) pins go on bottom, clearly marked NC
6. Pin spacing: 100mil (2.54mm) between pins
7. Body width: minimum 200mil (5.08mm), scale up for pin count
8. Body height: (pin_count / 2) * 100mil (2.54mm) minimum
9. Pin numbers must be visible and not overlapping
10. Pin names must be readable — abbreviate only if over 8 chars

## Optimal Symbol Rules
- Group functional pins together (e.g. all SPI pins adjacent)
- Symmetric layouts preferred for symmetric ICs
- Never place more than 8 pins on one side without splitting into sections
- Power section (VCC/GND) may be a separate hidden unit for clean schematics

## Pin coordinates
- Origin (0, 0) is the center of the symbol body
- Left-side pins: x = -(half_body_width + pin_length) = -(half_w + 2.54), y varies
- Right-side pins: x = (half_body_width + pin_length) = (half_w + 2.54), y varies
- Top pins: x varies, y = (half_body_height + pin_length) = (half_h + 2.54)
- Bottom pins: x varies, y = -(half_body_height + pin_length) = -(half_h + 2.54)
- Space pins 2.54mm (100mil) apart along their edge
- For left/right pins: start y at top and go down (decreasing y = higher on screen in KiCad)

## Output format
Return ONLY valid JSON with no markdown fences, no explanation text. The JSON must match:
{
  "symbol": {
    "name": "<COMPONENT_NAME>",
    "reference": "<U or R or C etc.>",
    "pins": [
      {
        "number": <integer pad number>,
        "name": "<pin name, max 8 chars>",
        "direction": "<left|right|top|bottom>",
        "type": "<power|input|output|bidirectional|passive|nc>",
        "x": <float mm>,
        "y": <float mm>
      }
    ],
    "body": {
      "width": <float mm, minimum 5.08>,
      "height": <float mm, minimum (pin_count/2)*2.54>
    },
    "format": "kicad_sym"
  }
}
"""

USER_PROMPT_TEMPLATE = """\
Design a KiCad schematic symbol for the following component.

Datasheet data:
{datasheet_json}

Follow the Symbol Design Rules exactly. Assign correct pin directions and types.
Calculate body dimensions from pin count. Return only the JSON object.
"""


# ─── Core agent logic ────────────────────────────────────────────────────────

def read_datasheet(path: Path = DATASHEET_JSON) -> dict:
    """Read and return the datasheet.json produced by the datasheet-parser agent."""
    if not path.exists():
        raise FileNotFoundError(
            f"datasheet.json not found at {path}. "
            "Run the datasheet-parser agent first."
        )
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def call_anthropic(datasheet_data: dict) -> dict:
    """
    Call the Anthropic API to design the symbol.
    Returns the parsed JSON dict for component.json.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise EnvironmentError(
            "ANTHROPIC_API_KEY is not set. Add it to .env or the environment."
        )

    client = anthropic.Anthropic(api_key=api_key)

    user_message = USER_PROMPT_TEMPLATE.format(
        datasheet_json=json.dumps(datasheet_data, indent=2)
    )

    print("[component-agent] Calling Anthropic API...", flush=True)
    message = client.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        messages=[
            {"role": "user", "content": user_message},
        ],
    )

    raw = message.content[0].text.strip()

    # Strip markdown code fences if the model added them despite instructions
    if raw.startswith("```"):
        lines = raw.splitlines()
        # Remove opening fence (```json or ```)
        lines = lines[1:]
        # Remove closing fence
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        raw = "\n".join(lines).strip()

    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(
            f"Anthropic returned invalid JSON: {e}\n\nRaw response:\n{raw}"
        )


def validate_component_json(data: dict) -> bool:
    """Validate component.json against shared/schemas/component_output.json."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    # Write temp file for the validator (it reads from disk)
    tmp_path = OUTPUT_DIR / "_component_validate_tmp.json"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    ok = validate_schema(str(tmp_path), str(COMPONENT_SCHEMA))
    tmp_path.unlink(missing_ok=True)
    return ok


def write_outputs(component_data: dict) -> None:
    """Write component.json and component.kicad_sym to data/outputs/."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Write component.json
    with open(COMPONENT_JSON, "w", encoding="utf-8") as f:
        json.dump(component_data, f, indent=2)
    print(f"[component-agent] Wrote {COMPONENT_JSON}", flush=True)

    # Generate and write component.kicad_sym
    kicad_content = write_kicad_sym(component_data)
    with open(COMPONENT_KICAD_SYM, "w", encoding="utf-8") as f:
        f.write(kicad_content)
    print(f"[component-agent] Wrote {COMPONENT_KICAD_SYM}", flush=True)


def validate_outputs() -> tuple[bool, bool]:
    """
    Validate both output files.
    Returns (json_valid, kicad_valid).
    """
    json_valid = validate_schema(str(COMPONENT_JSON), str(COMPONENT_SCHEMA))
    kicad_valid = validate_sexpr(str(COMPONENT_KICAD_SYM))
    return json_valid, kicad_valid


def run() -> dict:
    """
    Full agent run:
    1. Read datasheet.json
    2. Call Anthropic API to design symbol
    3. Validate JSON structure
    4. Write component.json and component.kicad_sym
    5. Validate both outputs
    Returns the component data dict.
    """
    print("[component-agent] Starting...", flush=True)

    # Step 1: Read input
    datasheet_data = read_datasheet()
    print(
        f"[component-agent] Loaded datasheet for: "
        f"{datasheet_data.get('component_name', 'unknown')}",
        flush=True,
    )

    # Step 2: Generate symbol via AI
    component_data = call_anthropic(datasheet_data)

    # Step 3: Pre-validate structure
    print("[component-agent] Pre-validating JSON structure...", flush=True)
    if not validate_component_json(component_data):
        raise ValueError(
            "Generated component.json does not match schema. "
            "Check the Anthropic response format."
        )

    # Step 4: Write outputs
    write_outputs(component_data)

    # Step 5: Validate written files
    print("[component-agent] Validating written files...", flush=True)
    json_valid, kicad_valid = validate_outputs()

    if not json_valid:
        raise RuntimeError(f"component.json failed schema validation: {COMPONENT_JSON}")
    if not kicad_valid:
        raise RuntimeError(
            f"component.kicad_sym failed KiCad validation: {COMPONENT_KICAD_SYM}"
        )

    print("[component-agent] Done. All outputs valid.", flush=True)
    return component_data


if __name__ == "__main__":
    run()
