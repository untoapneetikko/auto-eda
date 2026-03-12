"""
agent.py — Schematic Agent

Reads:
  data/outputs/datasheet.json
  data/outputs/component.json
  data/outputs/example_schematic.json

Accepts:
  project_brief (str) — user's design intent

Produces:
  data/outputs/schematic.json  (matches shared/schemas/schematic_output.json)

Process:
  1. Load all three input files
  2. Pre-compute net names via net_namer.suggest_net_names()
  3. Call Claude with a structured prompt containing all inputs + brief
  4. Parse and validate the JSON response against the schema
  5. Write validated output to disk
"""

from __future__ import annotations

import json
import os
import re
import sys
import textwrap
from pathlib import Path
from typing import Any

import anthropic
from dotenv import load_dotenv

# Local helpers
from net_namer import suggest_net_names  # noqa: E402

# ---------------------------------------------------------------------------
# Environment / paths
# ---------------------------------------------------------------------------

load_dotenv()

_ROOT = Path(os.getenv("DATA_DIR", Path(__file__).parent.parent.parent / "data"))
_OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", _ROOT / "outputs"))
_SCHEMA_DIR = Path(__file__).parent.parent.parent / "shared" / "schemas"
_VALIDATOR = Path(__file__).parent.parent.parent / "backend" / "tools" / "schema_validator.py"

DATASHEET_PATH = _OUTPUT_DIR / "datasheet.json"
COMPONENT_PATH = _OUTPUT_DIR / "component.json"
EXAMPLE_SCH_PATH = _OUTPUT_DIR / "example_schematic.json"
SCHEMATIC_OUT_PATH = _OUTPUT_DIR / "schematic.json"
SCHEMA_PATH = _SCHEMA_DIR / "schematic_output.json"

MODEL = "claude-sonnet-4-20250514"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_json(path: Path, label: str) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"{label} not found at {path}")
    with open(path) as fh:
        return json.load(fh)


def _extract_json_block(text: str) -> str:
    """Extract the first ```json ... ``` code fence, or the whole text if none."""
    fence_match = re.search(r"```(?:json)?\s*([\s\S]+?)```", text)
    if fence_match:
        return fence_match.group(1).strip()
    # Try to find a bare { ... } block
    brace_match = re.search(r"(\{[\s\S]+\})", text)
    if brace_match:
        return brace_match.group(1).strip()
    return text.strip()


def _validate(output_path: Path) -> bool:
    """Run schema_validator.py. Returns True if valid."""
    import subprocess  # noqa: PLC0415

    result = subprocess.run(
        [sys.executable, str(_VALIDATOR), str(output_path), str(SCHEMA_PATH)],
        capture_output=True,
        text=True,
    )
    print(result.stdout, end="")
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)
    return result.returncode == 0


def _build_prompt(
    datasheet: dict[str, Any],
    component: dict[str, Any],
    example_schematic: dict[str, Any],
    project_brief: str,
    net_hints: dict[str, str],
) -> str:
    schema = _load_json(SCHEMA_PATH, "schematic_output schema")

    return textwrap.dedent(f"""
        You are an expert electronics engineer designing a complete project schematic.

        ## Project Brief
        {project_brief}

        ## Pre-computed Net Name Suggestions
        The following mappings were derived from the component's pin functions.
        Use these names verbatim; do NOT invent NET001-style names.

        ```json
        {json.dumps(net_hints, indent=2)}
        ```

        ## Datasheet Data
        ```json
        {json.dumps(datasheet, indent=2)}
        ```

        ## Component Symbol Data
        ```json
        {json.dumps(component, indent=2)}
        ```

        ## Example Application Schematic (from datasheet)
        ```json
        {json.dumps(example_schematic, indent=2)}
        ```

        ## Required Output Schema
        ```json
        {json.dumps(schema, indent=2)}
        ```

        ## Design Rules — follow these exactly

        ### Step 1 — Name all nets BEFORE placing any components
        - Every net must have a meaningful human-readable name.
        - Power nets: include voltage in name (VCC_3V3, VCC_5V, VCC_12V).
        - Interface nets: I2C_SDA, SPI_MOSI, UART_TX, etc.
        - Active-low signals: prefix with n (nRESET, nOE, nFAULT).
        - Analog nets: ADC_IN, DAC_OUT, VREF.
        - NEVER use NET001 / NET002 / etc.
        - Use the pre-computed suggestions above wherever they apply.

        ### Step 2 — Place power symbols first
        - Every distinct supply rail must appear in `power_symbols`.
        - At minimum: the rail that powers the main IC and GND.

        ### Step 3 — Place main IC, then supporting passives
        - Main IC reference: U1.
        - Decoupling capacitors: C1, C2, … adjacent to power pins.
        - Pull-up/pull-down resistors: R1, R2, …
        - Other passives follow conventional designators.

        ### Step 4 — No floating pins
        - Every pin that is not connected to a net must be listed in `connections`
          with the net name "NC" (no-connect marker).
        - NC pins are included; they just use the "NC" net name.

        ### Step 5 — Output format
        - Return ONLY valid JSON matching the schema above.
        - Do not include any explanatory text outside the JSON.
        - `format` field must be exactly `"kicad_sch"`.
        - Net `type` values: `"power"`, `"signal"`, or `"gnd"`.
        - Positions use float coordinates on a 50-mil grid (multiples of 50).
        - The `project_name` should reflect the user's project brief.
    """).strip()


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def run(project_brief: str) -> dict[str, Any]:
    """
    Design a schematic for the given project_brief.

    Returns the validated schematic dict and writes it to data/outputs/schematic.json.
    Raises on validation failure.
    """
    # 1. Load inputs
    datasheet = _load_json(DATASHEET_PATH, "datasheet.json")
    component = _load_json(COMPONENT_PATH, "component.json")
    example_schematic = _load_json(EXAMPLE_SCH_PATH, "example_schematic.json")

    # 2. Pre-compute net name hints
    net_hints = suggest_net_names(datasheet)
    print(f"[schematic-agent] net hints: {json.dumps(net_hints, indent=2)}")

    # 3. Build prompt
    prompt = _build_prompt(datasheet, component, example_schematic, project_brief, net_hints)

    # 4. Call Claude
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise EnvironmentError("ANTHROPIC_API_KEY is not set")

    client = anthropic.Anthropic(api_key=api_key)
    print(f"[schematic-agent] calling {MODEL}…")

    message = client.messages.create(
        model=MODEL,
        max_tokens=4096,
        messages=[
            {
                "role": "user",
                "content": prompt,
            }
        ],
    )

    raw_text = message.content[0].text
    print(f"[schematic-agent] received {len(raw_text)} chars from model")

    # 5. Parse response
    json_str = _extract_json_block(raw_text)
    try:
        schematic: dict[str, Any] = json.loads(json_str)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Model returned invalid JSON: {exc}\n\nRaw output:\n{raw_text[:2000]}"
        ) from exc

    # 6. Post-process: enforce NC for any connection-less pins listed in component
    schematic = _ensure_no_floating_pins(schematic, component)

    # 7. Write output
    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(SCHEMATIC_OUT_PATH, "w") as fh:
        json.dump(schematic, fh, indent=2)
    print(f"[schematic-agent] wrote {SCHEMATIC_OUT_PATH}")

    # 8. Validate
    if not _validate(SCHEMATIC_OUT_PATH):
        raise RuntimeError(
            "schematic.json failed schema validation. Check output above."
        )

    return schematic


def _ensure_no_floating_pins(
    schematic: dict[str, Any], component: dict[str, Any]
) -> dict[str, Any]:
    """
    For every pin declared in component.json, ensure that the main IC component
    (U1) has a connection entry.  Missing pins are added with net="NC".
    This enforces the no-floating-pins rule even if Claude missed some.
    """
    all_pins: list[dict[str, Any]] = (
        component.get("symbol", {}).get("pins", [])
        or component.get("pins", [])
    )
    if not all_pins:
        return schematic

    all_pin_numbers: set[int] = {int(p["number"]) for p in all_pins}

    for comp in schematic.get("components", []):
        if comp.get("reference", "").startswith("U"):
            existing = {int(c["pin"]) for c in comp.get("connections", [])}
            missing = all_pin_numbers - existing
            for pin_num in sorted(missing):
                comp.setdefault("connections", []).append(
                    {"pin": pin_num, "net": "NC"}
                )
    return schematic


# ---------------------------------------------------------------------------
# CLI usage
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    brief = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else (
        "Design a breakout board for the component described in datasheet.json. "
        "Include decoupling capacitors, power supply filtering, and expose all I/O "
        "on 0.1-inch headers."
    )
    result = run(brief)
    print(f"[schematic-agent] done — {len(result.get('components', []))} components, "
          f"{len(result.get('nets', []))} nets")
