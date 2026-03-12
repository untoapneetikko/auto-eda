"""
Example Schematic Agent

Reads datasheet.json (example_application section), calls Claude to produce
a clean application schematic, validates against schema, and writes
data/outputs/example_schematic.json.
"""

import json
import os
import sys
import re
import logging
from pathlib import Path
from typing import Any

import anthropic
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths (resolved relative to project root via env or defaults)
# ---------------------------------------------------------------------------
_ROOT = Path(os.getenv("PROJECT_ROOT", Path(__file__).resolve().parents[2]))
INPUT_PATH = Path(os.getenv("DATASHEET_JSON", _ROOT / "data" / "outputs" / "datasheet.json"))
OUTPUT_PATH = Path(os.getenv("SCHEMATIC_JSON", _ROOT / "data" / "outputs" / "example_schematic.json"))
SCHEMA_PATH = Path(os.getenv("SCHEMATIC_SCHEMA", _ROOT / "shared" / "schemas" / "schematic_output.json"))
VALIDATOR_PATH = _ROOT / "backend" / "tools" / "schema_validator.py"

MODEL = "claude-sonnet-4-20250514"

# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are an expert electronics engineer specialising in schematic capture.
Your task is to create a clean, human-readable example application schematic
from datasheet information.

STRICT RULES — you must follow every one of these:
1. Power rails layout: VCC net at the top of the schematic, GND net at the bottom.
2. Signal flow is left-to-right across the schematic.
3. Every net that connects more than two pins MUST have a descriptive net label.
4. Net naming convention:
   - Power rails: VCC, VDD, GND, AGND, PGND  (never NET001-style names)
   - Signals: DESCRIPTIVE_NAME in UPPER_SNAKE_CASE, e.g. SPI_MOSI, OUTPUT_SIGNAL
   - Do NOT use NET001, NET002, N001, etc.
5. Only include components explicitly shown or implied by the datasheet example.
   Do NOT invent additional circuitry beyond what the datasheet shows.
6. All passives (resistors, capacitors, inductors) MUST have a value field.
   If the exact value is not stated in the datasheet, provide a typical value
   and set "assumed": true on that component.
7. Reference designators: U1, U2 for ICs; R1, R2 for resistors; C1, C2 for
   capacitors; L1 for inductors; D1 for diodes; Q1 for transistors.
8. Component spacing: minimum 50 mil between component bodies.
   Positions are in mils (1 mil = 0.0254 mm). Use a 50-mil grid.
9. Decoupling capacitors must be positioned adjacent (within 100 mil) to the
   VCC pin of the IC they decouple.
10. Output JSON must be valid and match the schema exactly — no extra keys
    outside what the schema defines at the top level.

OUTPUT FORMAT — respond with a single JSON object, no markdown fences, no
explanation text, just raw JSON matching this schema:
{
  "project_name": "string — component name + ' Application Circuit'",
  "nets": [
    { "name": "VCC", "label": "VCC", "type": "power" },
    { "name": "GND", "label": "GND", "type": "gnd" },
    { "name": "SIGNAL_NAME", "label": "SIGNAL_NAME", "type": "signal" }
  ],
  "components": [
    {
      "reference": "U1",
      "value": "component_name",
      "footprint": "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm",
      "position": { "x": 300.0, "y": 300.0 },
      "connections": [
        { "pin": 1, "net": "INPUT_SIGNAL" }
      ],
      "assumed": false
    }
  ],
  "power_symbols": ["VCC", "GND"],
  "format": "kicad_sch"
}

The "assumed" field on a component should be true if any of its values were
assumed rather than taken directly from the datasheet.
"""


def _build_user_prompt(datasheet: dict) -> str:
    component_name = datasheet.get("component_name", "Unknown Component")
    manufacturer = datasheet.get("manufacturer", "")
    package = datasheet.get("package", "")
    pins = datasheet.get("pins", [])
    electrical = datasheet.get("electrical", {})
    example_app = datasheet.get("example_application", {})

    pin_table = "\n".join(
        f"  Pin {p.get('number', '?')}: {p.get('name', '?')} ({p.get('type', '?')}) — {p.get('function', '')}"
        for p in pins
    )

    passives_list = "\n".join(
        f"  - {item}" for item in example_app.get("required_passives", [])
    )

    prompt = f"""
Component: {component_name}
Manufacturer: {manufacturer}
Package: {package}

--- ELECTRICAL SPECS ---
VCC min: {electrical.get('vcc_min', 'N/A')} V
VCC max: {electrical.get('vcc_max', 'N/A')} V
Max current: {electrical.get('i_max_ma', 'N/A')} mA

--- PIN DESCRIPTIONS ---
{pin_table}

--- EXAMPLE APPLICATION FROM DATASHEET ---
Description: {example_app.get('description', 'No description provided.')}

Required passives:
{passives_list if passives_list else '  (none listed)'}

Schematic notes from datasheet:
{example_app.get('typical_schematic_notes', 'No notes provided.')}

--- TASK ---
Produce the example_schematic.json for this component.
Follow all system rules strictly. Return only the JSON object.
""".strip()
    return prompt


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

def _strip_code_fences(text: str) -> str:
    """Remove markdown code fences if the model wrapped its output in them."""
    text = text.strip()
    # ```json ... ``` or ``` ... ```
    match = re.match(r"^```(?:json)?\s*([\s\S]*?)```\s*$", text, re.MULTILINE)
    if match:
        return match.group(1).strip()
    return text


def _validate_net_names(schematic: dict) -> list[str]:
    """Return a list of violation strings for bad net names."""
    bad_pattern = re.compile(r"^N[ET]*\d+$", re.IGNORECASE)
    violations = []
    for net in schematic.get("nets", []):
        name = net.get("name", "")
        if bad_pattern.match(name):
            violations.append(f"Net '{name}' uses a generic name (NET001-style) — must be descriptive")
    return violations


def _validate_passive_values(schematic: dict) -> list[str]:
    """Return violations where passives have missing/empty values."""
    passive_refs = re.compile(r"^[RCLD]\d", re.IGNORECASE)
    violations = []
    for comp in schematic.get("components", []):
        ref = comp.get("reference", "")
        if passive_refs.match(ref):
            value = comp.get("value", "").strip()
            if not value or value.lower() in ("", "?", "unknown", "tbd"):
                violations.append(f"Passive {ref} has no value — must specify a value (set assumed:true if guessed)")
    return violations


def run(datasheet_path: Path = INPUT_PATH, output_path: Path = OUTPUT_PATH) -> dict:
    """
    Main entry point. Reads datasheet.json, calls Claude, validates, writes output.
    Returns the schematic dict on success, raises on failure.
    """
    log.info("Reading datasheet from %s", datasheet_path)
    if not datasheet_path.exists():
        raise FileNotFoundError(f"Datasheet not found: {datasheet_path}")

    with open(datasheet_path) as f:
        datasheet = json.load(f)

    log.info("Component: %s", datasheet.get("component_name", "unknown"))

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise EnvironmentError(
            "ANTHROPIC_API_KEY is not set. "
            "Add it to your .env file or export it as an environment variable."
        )
    client = anthropic.Anthropic(api_key=api_key)

    user_prompt = _build_user_prompt(datasheet)

    log.info("Calling Claude (%s)…", MODEL)
    response = client.messages.create(
        model=MODEL,
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )

    raw_text = response.content[0].text
    log.info("Received %d characters from Claude", len(raw_text))

    cleaned = _strip_code_fences(raw_text)

    try:
        schematic = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Claude returned non-JSON output: {exc}\n---\n{raw_text[:500]}") from exc

    # Enforce format field
    schematic["format"] = "kicad_sch"

    # Post-generation checks (warn, do not abort — schema validator is authoritative)
    net_violations = _validate_net_names(schematic)
    value_violations = _validate_passive_values(schematic)
    for v in net_violations + value_violations:
        log.warning("VALIDATION WARNING: %s", v)

    if net_violations or value_violations:
        # Attempt a repair pass
        log.info("Violations found — requesting repair from Claude…")
        schematic = _repair_pass(client, schematic, net_violations + value_violations)

    # Write output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(schematic, f, indent=2)
    log.info("Wrote schematic to %s", output_path)

    # Schema validation
    _schema_validate(output_path)

    return schematic


def _repair_pass(client: anthropic.Anthropic, schematic: dict, violations: list[str]) -> dict:
    """Ask Claude to fix specific violations in the schematic."""
    violation_text = "\n".join(f"- {v}" for v in violations)
    repair_prompt = f"""
The following schematic JSON has these violations that must be fixed:
{violation_text}

Here is the current JSON:
{json.dumps(schematic, indent=2)}

Return ONLY the corrected JSON object, no explanation, no markdown fences.
All violations must be resolved. Net names must be descriptive. Passives must have values.
""".strip()

    response = client.messages.create(
        model=MODEL,
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": repair_prompt}],
    )
    raw = _strip_code_fences(response.content[0].text)
    try:
        repaired = json.loads(raw)
        repaired["format"] = "kicad_sch"
        log.info("Repair pass succeeded")
        return repaired
    except json.JSONDecodeError:
        log.warning("Repair pass returned invalid JSON — using original")
        return schematic


def _schema_validate(output_path: Path) -> None:
    """Run schema_validator.py as a subprocess for authoritative validation."""
    import subprocess
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    result = subprocess.run(
        [sys.executable, str(VALIDATOR_PATH), str(output_path), str(SCHEMA_PATH)],
        capture_output=True,
        text=True,
        env=env,
    )
    if result.returncode != 0:
        log.error("Schema validation FAILED:\n%s\n%s", result.stdout, result.stderr)
        raise ValueError(f"Schema validation failed for {output_path}")
    log.info("Schema validation passed: %s", result.stdout.strip())


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Example Schematic Agent")
    parser.add_argument("--datasheet", default=str(INPUT_PATH), help="Path to datasheet.json")
    parser.add_argument("--output", default=str(OUTPUT_PATH), help="Path for output JSON")
    args = parser.parse_args()

    result = run(Path(args.datasheet), Path(args.output))
    print(json.dumps(result, indent=2))
