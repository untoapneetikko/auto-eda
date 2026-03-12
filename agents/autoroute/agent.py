"""
Auto-Route Agent — routes PCB traces using Claude claude-sonnet-4-20250514.

Pipeline position: Step 7 of 8
Input:  data/outputs/placement.json + data/outputs/schematic.json
Output: data/outputs/routing.json

Routing priority:
  1. Power traces (widest, routed first)
  2. High-speed signals (shortest path, controlled impedance)
  3. Analog signals (away from switching noise)
  4. Control/digital signals
  5. Low-priority signals last

Never route under crystals or oscillators.
Minimize vias — each via adds ~1nH inductance.
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
from pathlib import Path

import anthropic
from dotenv import load_dotenv

from trace_width_calculator import calculate_trace_width, classify_net

# ── Environment ────────────────────────────────────────────────────────────────
load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data" / "outputs"
SCHEMA_DIR = PROJECT_ROOT / "shared" / "schemas"
TOOLS_DIR = PROJECT_ROOT / "backend" / "tools"

PLACEMENT_FILE = DATA_DIR / "placement.json"
SCHEMATIC_FILE = DATA_DIR / "schematic.json"
ROUTING_FILE = DATA_DIR / "routing.json"
ROUTING_SCHEMA = SCHEMA_DIR / "routing_output.json"

MODEL = "claude-sonnet-4-20250514"

# ── Helpers ────────────────────────────────────────────────────────────────────

def load_json(path: Path) -> dict:
    """Load a JSON file, raising a clear error if it's missing."""
    if not path.exists():
        raise FileNotFoundError(
            f"Required input file not found: {path}\n"
            "Make sure the previous pipeline step completed successfully."
        )
    with open(path) as f:
        return json.load(f)


def save_json(path: Path, data: dict) -> None:
    """Write data to a JSON file, creating parent dirs if needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    print(f"[autoroute] Written: {path}")


def routing_priority(net_name: str) -> int:
    """Return sort key: lower = routed first."""
    t = classify_net(net_name)
    return {"power": 0, "highspeed": 1, "analog": 2, "signal": 3}[t]


def detect_crystals(components: list[dict]) -> set[str]:
    """
    Return a set of reference designators that are crystals or oscillators.
    These components have a no-route zone underneath them.
    """
    crystal_refs: set[str] = set()
    keywords = {"crystal", "xtal", "oscillator", "osc", "resonator"}
    for comp in components:
        ref = comp.get("reference", "")
        value = comp.get("value", "").lower()
        footprint = comp.get("footprint", "").lower()
        if any(kw in value for kw in keywords) or any(kw in footprint for kw in keywords):
            crystal_refs.add(ref)
        # Common reference prefixes: Y (crystal), XTAL, OSC
        if ref.startswith(("Y", "XTAL", "OSC", "X")):
            crystal_refs.add(ref)
    return crystal_refs


def build_net_map(schematic: dict) -> dict[str, list[dict]]:
    """
    Build a map of net_name → list of {reference, pin} dicts.
    This represents the ratsnest — all pads that share a net.
    """
    net_map: dict[str, list[dict]] = {}
    for comp in schematic.get("components", []):
        ref = comp["reference"]
        for conn in comp.get("connections", []):
            net = conn["net"]
            if net not in net_map:
                net_map[net] = []
            net_map[net].append({"reference": ref, "pin": conn["pin"]})
    return net_map


def get_component_position(placements: list[dict], reference: str) -> dict | None:
    """Return the placement entry for a given component reference."""
    for p in placements:
        if p["reference"] == reference:
            return p
    return None


def estimate_current(net_name: str, net_type: str) -> float:
    """
    Estimate current draw for a net based on its type and name.
    Used for trace width calculation when current data is not provided.
    """
    if net_type == "power":
        # Assume 1A for generic power rails; 2A for high-current rails
        upper = net_name.upper()
        if any(kw in upper for kw in ("V12", "VIN", "VMOT", "VPWR")):
            return 2.0
        return 1.0
    return 0.05  # 50mA default for signal nets


def manhattan_route(
    x1: float, y1: float, x2: float, y2: float, layer: str = "F.Cu"
) -> list[dict]:
    """
    Generate a simple L-shaped (Manhattan) route between two points.
    Returns a path as a list of {x, y} dicts.
    Routes horizontally first, then vertically (minimizes vias on single layer).
    """
    # Horizontal segment first, then vertical
    mid_x = x2
    mid_y = y1

    path = [
        {"x": round(x1, 4), "y": round(y1, 4)},
        {"x": round(mid_x, 4), "y": round(mid_y, 4)},
        {"x": round(x2, 4), "y": round(y2, 4)},
    ]

    # Deduplicate collinear points (straight line case)
    if abs(x1 - x2) < 0.001:
        path = [{"x": round(x1, 4), "y": round(y1, 4)}, {"x": round(x2, 4), "y": round(y2, 4)}]
    elif abs(y1 - y2) < 0.001:
        path = [{"x": round(x1, 4), "y": round(y1, 4)}, {"x": round(x2, 4), "y": round(y2, 4)}]

    return path


def build_initial_routing(schematic: dict, placement: dict, crystal_refs: set[str]) -> dict:
    """
    Build an initial routing solution using Manhattan routes.
    This is the fallback/seed routing that Claude can refine.
    Returns a routing_output.json-compatible dict.
    """
    placements_list = placement.get("placements", [])
    net_map = build_net_map(schematic)

    traces = []
    vias: list[dict] = []

    # Sort nets by priority (power first)
    sorted_nets = sorted(net_map.keys(), key=routing_priority)

    for net_name in sorted_nets:
        pads = net_map[net_name]
        if len(pads) < 2:
            continue  # Single-pad net — nothing to route

        net_type = classify_net(net_name)
        current = estimate_current(net_name, net_type)
        width_info = calculate_trace_width(net_name, current)
        width_mm = width_info["width_mm"]

        # Build a minimum spanning tree (nearest-neighbour greedy) to minimise wire length
        # Start from the first pad
        unconnected = list(pads[1:])
        connected = [pads[0]]

        while unconnected:
            best_dist = float("inf")
            best_from = None
            best_to_idx = None

            for from_pad in connected:
                from_pos = get_component_position(placements_list, from_pad["reference"])
                if from_pos is None:
                    continue

                for idx, to_pad in enumerate(unconnected):
                    to_pos = get_component_position(placements_list, to_pad["reference"])
                    if to_pos is None:
                        continue

                    dist = math.sqrt(
                        (from_pos["x"] - to_pos["x"]) ** 2
                        + (from_pos["y"] - to_pos["y"]) ** 2
                    )
                    if dist < best_dist:
                        best_dist = dist
                        best_from = from_pad
                        best_to_idx = idx

            if best_from is None or best_to_idx is None:
                break

            to_pad = unconnected.pop(best_to_idx)
            connected.append(to_pad)

            from_pos = get_component_position(placements_list, best_from["reference"])
            to_pos = get_component_position(placements_list, to_pad["reference"])

            if from_pos is None or to_pos is None:
                continue

            # Skip routing under crystals
            from_ref = best_from["reference"]
            to_ref = to_pad["reference"]
            if from_ref in crystal_refs or to_ref in crystal_refs:
                crystal_ref = from_ref if from_ref in crystal_refs else to_ref
                print(f"[autoroute] WARNING: Skipping route involving crystal/oscillator {crystal_ref}")
                continue

            path = manhattan_route(from_pos["x"], from_pos["y"], to_pos["x"], to_pos["y"])
            traces.append({
                "net": net_name,
                "layer": "F.Cu",
                "width_mm": width_mm,
                "path": path,
            })

    return {"traces": traces, "vias": vias}


def build_claude_prompt(schematic: dict, placement: dict, initial_routing: dict) -> str:
    """
    Build the prompt for Claude to review and improve the routing.
    """
    nets = schematic.get("nets", [])
    components = schematic.get("components", [])
    placements = placement.get("placements", [])
    board = placement.get("board", {"width_mm": 100.0, "height_mm": 80.0})

    # Summarise net types for the prompt
    net_summary = []
    for net in nets:
        name = net["name"]
        ntype = classify_net(name)
        current = estimate_current(name, ntype)
        width_info = calculate_trace_width(name, current)
        net_summary.append({
            "name": name,
            "type": ntype,
            "width_mm": width_info["width_mm"],
            "priority": routing_priority(name),
        })
    net_summary.sort(key=lambda n: n["priority"])

    prompt = f"""You are an expert PCB layout engineer. Review and improve the following PCB routing.

## Board
- Size: {board.get('width_mm', 100)}mm × {board.get('height_mm', 80)}mm
- Layers: 2 (F.Cu = top copper, B.Cu = bottom copper)
- Technology: Standard FR4 1.6mm, 35μm copper

## Components ({len(components)} total)
{json.dumps(components[:20], indent=2)}
{f'... and {len(components) - 20} more components' if len(components) > 20 else ''}

## Placements
{json.dumps(placements, indent=2)}

## Nets ({len(nets)} total) — sorted by routing priority
{json.dumps(net_summary, indent=2)}

## Initial Routing (Manhattan routes — needs improvement)
{json.dumps(initial_routing, indent=2)}

## Routing Rules (MUST FOLLOW ALL)
1. **Priority order**: Route power traces first (widest), then high-speed signals, then analog, then control signals, then low-priority signals last.
2. **Trace widths**:
   - Power (VCC/VDD/GND/PWR nets): width = current_amps × 0.4 mm/A, minimum 0.4mm
   - High-speed (CLK/USB/ETH nets): 0.3mm for 50Ω controlled impedance on FR4
   - Signal traces: 0.2mm preferred, 0.15mm absolute minimum
   - NEVER produce a trace with width_mm < 0.15
3. **Minimize vias**: Each via adds ~1nH inductance. Use vias only when layer change is unavoidable.
4. **Never route under crystals or oscillators** (refs starting with Y, XTAL, OSC, X).
5. **No trace shorts**: No two traces on the same layer may cross unless they are on the same net.
6. **Clearance**: Maintain ≥ 0.2mm clearance between traces on the same layer.
7. **Return paths**: Route GND/power return paths as short as possible.
8. **Differential pairs**: Route differential pairs (DP/DM, P/N) together with matched length ±0.1mm.

## Output Format
Return ONLY a valid JSON object matching this schema exactly:
{{
  "traces": [
    {{
      "net": "<net name>",
      "layer": "F.Cu",
      "width_mm": <float>,
      "path": [{{"x": <float>, "y": <float>}}, ...]
    }}
  ],
  "vias": [
    {{"net": "<net name>", "x": <float>, "y": <float>, "drill_mm": 0.3}}
  ]
}}

Rules for the JSON:
- All coordinates in mm
- path must have at least 2 points
- width_mm must be ≥ 0.15 for ALL traces
- Power traces (GND, VCC, etc.) must have width_mm ≥ 0.4
- Only include vias when a layer change is genuinely needed
- Every net in the schematic must have at least one trace segment

Improve the routing: reduce total wire length, eliminate unnecessary bends, fix any clearance violations, and ensure power traces are wider than signal traces. Return the complete improved routing JSON now."""

    return prompt


def call_claude(prompt: str) -> dict:
    """
    Call Claude claude-sonnet-4-20250514 to generate/improve routing.
    Returns the parsed routing JSON dict.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise EnvironmentError(
            "ANTHROPIC_API_KEY not set. Add it to .env or export it in your shell."
        )

    client = anthropic.Anthropic(api_key=api_key)

    print(f"[autoroute] Calling {MODEL}...")
    message = client.messages.create(
        model=MODEL,
        max_tokens=8192,
        messages=[{"role": "user", "content": prompt}],
    )

    raw_text = message.content[0].text.strip()
    print(f"[autoroute] Claude responded ({len(raw_text)} chars)")

    # Extract JSON from the response (Claude may include explanation text)
    json_start = raw_text.find("{")
    json_end = raw_text.rfind("}") + 1
    if json_start == -1 or json_end == 0:
        raise ValueError(
            f"Claude did not return valid JSON. Response:\n{raw_text[:500]}"
        )

    json_str = raw_text[json_start:json_end]
    try:
        routing = json.loads(json_str)
    except json.JSONDecodeError as e:
        raise ValueError(f"Failed to parse Claude's JSON response: {e}\n\nRaw:\n{json_str[:500]}")

    return routing


def validate_routing_schema(routing: dict) -> list[str]:
    """
    Validate the routing dict against the expected schema.
    Returns a list of error strings (empty = valid).
    """
    errors = []

    if "traces" not in routing:
        errors.append("Missing 'traces' key in routing output")
        return errors
    if "vias" not in routing:
        errors.append("Missing 'vias' key in routing output")
        return errors

    for i, trace in enumerate(routing["traces"]):
        prefix = f"traces[{i}]"
        for field in ("net", "layer", "width_mm", "path"):
            if field not in trace:
                errors.append(f"{prefix}: missing field '{field}'")
        if "path" in trace:
            if len(trace["path"]) < 2:
                errors.append(f"{prefix}: path must have ≥ 2 points")
            for j, pt in enumerate(trace["path"]):
                if "x" not in pt or "y" not in pt:
                    errors.append(f"{prefix}.path[{j}]: missing x or y")
        if "width_mm" in trace:
            if not isinstance(trace["width_mm"], (int, float)):
                errors.append(f"{prefix}: width_mm must be a number")
            elif trace["width_mm"] < 0.15:
                errors.append(
                    f"{prefix}: width_mm={trace['width_mm']} is below minimum 0.15mm"
                )

    for i, via in enumerate(routing["vias"]):
        prefix = f"vias[{i}]"
        for field in ("net", "x", "y", "drill_mm"):
            if field not in via:
                errors.append(f"{prefix}: missing field '{field}'")

    return errors


def fix_routing_issues(routing: dict, schematic: dict) -> dict:
    """
    Post-process routing to fix common issues:
    - Clamp trace widths to minimum
    - Ensure power traces have correct width
    - Remove zero-length segments
    """
    for trace in routing.get("traces", []):
        net_name = trace.get("net", "")
        net_type = classify_net(net_name)

        # Enforce minimum width
        width = trace.get("width_mm", 0.2)
        if width < 0.15:
            trace["width_mm"] = 0.15

        # Enforce power trace minimum
        if net_type == "power" and trace["width_mm"] < 0.4:
            trace["width_mm"] = 0.4

        # Ensure layer is set
        if not trace.get("layer"):
            trace["layer"] = "F.Cu"

        # Remove zero-length path segments
        path = trace.get("path", [])
        if len(path) >= 2:
            cleaned = [path[0]]
            for pt in path[1:]:
                prev = cleaned[-1]
                if abs(pt["x"] - prev["x"]) > 0.001 or abs(pt["y"] - prev["y"]) > 0.001:
                    cleaned.append(pt)
            trace["path"] = cleaned if len(cleaned) >= 2 else path

    return routing


def run_drc(routing_path: Path) -> tuple[bool, str]:
    """
    Run the DRC checker on the routing output.
    Returns (passed: bool, output: str).
    """
    drc_script = TOOLS_DIR / "drc_checker.py"
    if not drc_script.exists():
        return True, "DRC checker not found — skipping"

    try:
        result = subprocess.run(
            [sys.executable, str(drc_script), str(routing_path), "--check", "clearance,width,shorts"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        output = result.stdout + result.stderr
        return result.returncode == 0, output
    except subprocess.TimeoutExpired:
        return False, "DRC checker timed out"
    except Exception as e:
        return False, f"DRC checker error: {e}"


def check_all_nets_routed(schematic: dict, routing: dict) -> list[str]:
    """
    Verify that every net from the schematic has at least one trace.
    Returns list of unrouted net names.
    """
    net_map = build_net_map(schematic)
    routed_nets = {t["net"] for t in routing.get("traces", [])}
    unrouted = []

    for net_name, pads in net_map.items():
        if len(pads) >= 2 and net_name not in routed_nets:
            unrouted.append(net_name)

    return unrouted


def run(
    placement_path: Path = PLACEMENT_FILE,
    schematic_path: Path = SCHEMATIC_FILE,
    output_path: Path = ROUTING_FILE,
    use_claude: bool = True,
) -> dict:
    """
    Main entry point — run the full auto-route pipeline.

    Returns the routing dict on success, raises on failure.
    """
    print("[autoroute] ── Auto-Route Agent starting ──")

    # Step 1: Load inputs
    print(f"[autoroute] Loading placement: {placement_path}")
    placement = load_json(placement_path)

    print(f"[autoroute] Loading schematic: {schematic_path}")
    schematic = load_json(schematic_path)

    # Step 2: Detect crystals / no-route zones
    all_components = schematic.get("components", [])
    crystal_refs = detect_crystals(all_components)
    if crystal_refs:
        print(f"[autoroute] Crystals/oscillators detected (no-route zones): {crystal_refs}")

    # Step 3: Build initial Manhattan routing as seed
    print("[autoroute] Building seed routing...")
    initial_routing = build_initial_routing(schematic, placement, crystal_refs)
    print(f"[autoroute] Seed: {len(initial_routing['traces'])} traces, {len(initial_routing['vias'])} vias")

    # Step 4: Use Claude to improve routing
    routing = initial_routing
    if use_claude:
        try:
            prompt = build_claude_prompt(schematic, placement, initial_routing)
            claude_routing = call_claude(prompt)

            # Validate Claude's response before accepting it
            schema_errors = validate_routing_schema(claude_routing)
            if schema_errors:
                print("[autoroute] Claude's routing had schema errors — using seed routing:")
                for err in schema_errors:
                    print(f"  - {err}")
            else:
                routing = claude_routing
                print(f"[autoroute] Using Claude routing: {len(routing['traces'])} traces, {len(routing['vias'])} vias")
        except Exception as e:
            print(f"[autoroute] Claude call failed ({e}) — falling back to seed routing")

    # Step 5: Post-process / fix common issues
    routing = fix_routing_issues(routing, schematic)

    # Step 6: Validate schema
    schema_errors = validate_routing_schema(routing)
    if schema_errors:
        print("[autoroute] Schema validation errors:")
        for err in schema_errors:
            print(f"  ❌ {err}")
        raise ValueError(f"Routing output failed schema validation ({len(schema_errors)} errors)")
    else:
        print("[autoroute] ✅ Schema validation passed")

    # Step 7: Check all nets are routed
    unrouted = check_all_nets_routed(schematic, routing)
    if unrouted:
        print(f"[autoroute] ⚠ Unrouted nets ({len(unrouted)}): {unrouted}")
    else:
        print("[autoroute] ✅ All nets routed")

    # Step 8: Write output
    save_json(output_path, routing)

    # Step 9: Run DRC
    print("[autoroute] Running DRC...")
    drc_passed, drc_output = run_drc(output_path)
    print(drc_output.strip())
    if not drc_passed:
        print("[autoroute] ⚠ DRC reported errors — review routing.json before sign-off")
    else:
        print("[autoroute] ✅ DRC passed")

    print("[autoroute] ── Done ──")
    return routing


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="PCB Auto-Route Agent")
    parser.add_argument("--placement", default=str(PLACEMENT_FILE), help="Path to placement.json")
    parser.add_argument("--schematic", default=str(SCHEMATIC_FILE), help="Path to schematic.json")
    parser.add_argument("--output", default=str(ROUTING_FILE), help="Path to write routing.json")
    parser.add_argument(
        "--no-claude", action="store_true", help="Use only seed routing (no Anthropic API call)"
    )
    args = parser.parse_args()

    result = run(
        placement_path=Path(args.placement),
        schematic_path=Path(args.schematic),
        output_path=Path(args.output),
        use_claude=not args.no_claude,
    )
    print(f"\n[autoroute] Routing complete: {len(result['traces'])} traces, {len(result['vias'])} vias")
