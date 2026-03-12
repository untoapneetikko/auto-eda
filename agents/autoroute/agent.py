"""
Auto-Route Agent — pure Python routing tools, no Anthropic SDK.

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

Claude Code handles all LLM reasoning. This module is pure tools:
  - get_routing_context()  — read inputs, return structured context for orchestrator
  - apply_routing()        — validate + post-process + write routing from orchestrator dict
  - _seed_routing()        — Manhattan/MST fallback, callable standalone
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
from pathlib import Path

from trace_width_calculator import calculate_trace_width, classify_net

# ── Paths ───────────────────────────────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data" / "outputs"
SCHEMA_DIR = PROJECT_ROOT / "shared" / "schemas"
TOOLS_DIR = PROJECT_ROOT / "backend" / "tools"

PLACEMENT_FILE = DATA_DIR / "placement.json"
SCHEMATIC_FILE = DATA_DIR / "schematic.json"
ROUTING_FILE = DATA_DIR / "routing.json"
ROUTING_SCHEMA = SCHEMA_DIR / "routing_output.json"

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
    Build a map of net_name -> list of {reference, pin} dicts.
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
    Build an initial routing solution using Manhattan routes and a greedy MST.
    This is the fallback/seed routing that the orchestrator can start from.
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
                errors.append(f"{prefix}: path must have >= 2 points")
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
    - Default layer to F.Cu
    - Remove zero-length segments
    """
    for trace in routing.get("traces", []):
        net_name = trace.get("net", "")
        net_type = classify_net(net_name)

        # Enforce minimum width
        width = trace.get("width_mm", 0.2)
        if width < 0.15:
            trace["width_mm"] = 0.15
        else:
            trace["width_mm"] = width

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


# ── Public tool API (called by orchestrator / endpoints) ────────────────────────

def get_routing_context(
    placement_path: Path = PLACEMENT_FILE,
    schematic_path: Path = SCHEMATIC_FILE,
) -> dict:
    """
    Read placement.json and schematic.json and return a structured context dict
    ready for the orchestrator (Claude Code) to reason over.

    Returns:
        {
            "board": { width_mm, height_mm },
            "nets": [
                {
                    "name": str,
                    "type": "power"|"highspeed"|"analog"|"signal",
                    "priority": int,          # 0=power … 3=signal
                    "width_mm": float,        # pre-computed trace width
                    "estimated_current_amps": float,
                    "pads": [ {"reference": str, "pin": ..., "x": float, "y": float} ]
                }
            ],
            "no_route_refs": [str],           # crystal/oscillator refs
            "components": [...],              # raw component list from schematic
            "placements": [...],              # raw placement list
        }
    """
    placement = load_json(placement_path)
    schematic = load_json(schematic_path)

    board = placement.get("board", {"width_mm": 100.0, "height_mm": 80.0})
    placements_list = placement.get("placements", [])
    components = schematic.get("components", [])

    # Build position lookup
    pos_by_ref: dict[str, dict] = {p["reference"]: p for p in placements_list}

    # Detect no-route zones
    crystal_refs = detect_crystals(components)

    # Build enriched net list
    net_map = build_net_map(schematic)
    nets_out = []
    for net_name, pads in net_map.items():
        net_type = classify_net(net_name)
        current = estimate_current(net_name, net_type)
        width_info = calculate_trace_width(net_name, current)

        # Attach pad positions
        enriched_pads = []
        for pad in pads:
            pos = pos_by_ref.get(pad["reference"])
            enriched_pads.append({
                "reference": pad["reference"],
                "pin": pad["pin"],
                "x": pos["x"] if pos else None,
                "y": pos["y"] if pos else None,
            })

        nets_out.append({
            "name": net_name,
            "type": net_type,
            "priority": routing_priority(net_name),
            "width_mm": width_info["width_mm"],
            "estimated_current_amps": current,
            "pads": enriched_pads,
        })

    # Sort by routing priority
    nets_out.sort(key=lambda n: n["priority"])

    return {
        "board": board,
        "nets": nets_out,
        "no_route_refs": sorted(crystal_refs),
        "components": components,
        "placements": placements_list,
    }


def _seed_routing(
    placement_path: Path = PLACEMENT_FILE,
    schematic_path: Path = SCHEMATIC_FILE,
) -> dict:
    """
    Run the Manhattan/greedy-MST seed router and return the raw routing dict.
    Does NOT write to disk — caller decides what to do with the result.
    Exposed via GET /agents/autoroute/seed so the orchestrator can use it
    as a starting point or fallback.
    """
    placement = load_json(placement_path)
    schematic = load_json(schematic_path)
    crystal_refs = detect_crystals(schematic.get("components", []))
    routing = build_initial_routing(schematic, placement, crystal_refs)
    print(f"[autoroute] Seed routing: {len(routing['traces'])} traces, {len(routing['vias'])} vias")
    return routing


def apply_routing(
    routing_dict: dict,
    output_dir: Path | None = None,
    schematic_path: Path = SCHEMATIC_FILE,
) -> dict:
    """
    Accept a routing dict from the orchestrator, post-process it, validate it,
    run DRC, and write data/outputs/routing.json.

    Args:
        routing_dict:  The routing produced by the orchestrator (traces + vias).
        output_dir:    Override output directory (default: DATA_DIR).
        schematic_path: Path to schematic.json for net completeness check.

    Returns:
        {
            "success": bool,
            "drc_passed": bool | None,
            "errors": [str],          # schema errors or DRC errors
            "traces_count": int,
            "vias_count": int,
            "unrouted_nets": [str],
        }
    """
    output_path = (output_dir or DATA_DIR) / "routing.json"
    errors: list[str] = []

    # Step 1: Load schematic for net checks (optional — skip if file missing)
    try:
        schematic = load_json(schematic_path)
    except FileNotFoundError:
        schematic = {"components": []}

    # Step 2: Post-process — clamp widths, enforce power minimum, default layer
    routing_dict = fix_routing_issues(routing_dict, schematic)

    # Step 3: Schema validation
    schema_errors = validate_routing_schema(routing_dict)
    if schema_errors:
        errors.extend(schema_errors)
        return {
            "success": False,
            "drc_passed": None,
            "errors": errors,
            "traces_count": len(routing_dict.get("traces", [])),
            "vias_count": len(routing_dict.get("vias", [])),
            "unrouted_nets": [],
        }

    # Step 4: Check net completeness
    unrouted = check_all_nets_routed(schematic, routing_dict)
    if unrouted:
        print(f"[autoroute] Unrouted nets ({len(unrouted)}): {unrouted}")

    # Step 5: Write output
    save_json(output_path, routing_dict)

    # Step 6: Run DRC
    drc_passed, drc_output = run_drc(output_path)
    print(drc_output.strip())
    if not drc_passed:
        errors.append(f"DRC failed: {drc_output.strip()}")

    return {
        "success": True,
        "drc_passed": drc_passed,
        "errors": errors,
        "traces_count": len(routing_dict.get("traces", [])),
        "vias_count": len(routing_dict.get("vias", [])),
        "unrouted_nets": unrouted,
    }


def run(*args, **kwargs) -> None:
    """
    Removed: Claude Code now drives routing via get_routing_context() + apply_routing().
    Use the endpoint or call those functions directly.
    """
    raise NotImplementedError(
        "run() is no longer implemented. "
        "Use get_routing_context() to fetch context for the orchestrator, "
        "_seed_routing() for the greedy MST fallback, "
        "and apply_routing(routing_dict) to write the final result."
    )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="PCB Auto-Route Agent tools")
    subparsers = parser.add_subparsers(dest="command")

    ctx_parser = subparsers.add_parser("context", help="Print routing context JSON")
    ctx_parser.add_argument("--placement", default=str(PLACEMENT_FILE))
    ctx_parser.add_argument("--schematic", default=str(SCHEMATIC_FILE))

    seed_parser = subparsers.add_parser("seed", help="Run seed routing and print JSON")
    seed_parser.add_argument("--placement", default=str(PLACEMENT_FILE))
    seed_parser.add_argument("--schematic", default=str(SCHEMATIC_FILE))

    args = parser.parse_args()

    if args.command == "context":
        ctx = get_routing_context(Path(args.placement), Path(args.schematic))
        print(json.dumps(ctx, indent=2))
    elif args.command == "seed":
        seed = _seed_routing(Path(args.placement), Path(args.schematic))
        print(json.dumps(seed, indent=2))
    else:
        parser.print_help()
