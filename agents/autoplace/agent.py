"""
Auto-Place Agent — Tool Layer
==============================
Pure tool functions for PCB component placement.  Claude Code (the orchestrator)
handles all reasoning; this module provides the data-access and validation tools.

No Anthropic SDK calls here.  Python is pure tools.

Public API
----------
get_placement_context()
    Read schematic.json and return structured placement context for the
    orchestrator to reason about.

apply_placement(placement_dict, output_dir=None)
    Validate and write a placement dict produced by the orchestrator.
    Runs courtyard check (Polars) and external DRC; returns a result summary.

run()
    Not implemented — orchestration belongs to Claude Code.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

# Import from sibling module — works whether run as a module or as a script.
try:
    from agents.autoplace.placement_optimizer import (
        check_courtyard_clearances,
        compute_net_proximity_placement,
        summarize_violations,
    )
except ModuleNotFoundError:
    # Allow running directly from the agents/autoplace/ directory.
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    from agents.autoplace.placement_optimizer import (
        check_courtyard_clearances,
        compute_net_proximity_placement,
        summarize_violations,
    )

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

log = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [autoplace] %(levelname)s %(message)s",
)

_ROOT = Path(__file__).parent.parent.parent  # pcb-agent-autoplace/

OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", str(_ROOT / "data" / "outputs")))
SCHEMATIC_FILE = Path(os.getenv("SCHEMATIC_FILE", str(OUTPUT_DIR / "schematic.json")))
PLACEMENT_FILE = OUTPUT_DIR / "placement.json"
DRC_TOOL = _ROOT / "backend" / "tools" / "drc_checker.py"

BOARD_WIDTH_MM: float = float(os.getenv("BOARD_WIDTH_MM", "100.0"))
BOARD_HEIGHT_MM: float = float(os.getenv("BOARD_HEIGHT_MM", "80.0"))


# ---------------------------------------------------------------------------
# Tool: get_placement_context
# ---------------------------------------------------------------------------

def get_placement_context(
    schematic_path: str | Path | None = None,
    board_width_mm: float = BOARD_WIDTH_MM,
    board_height_mm: float = BOARD_HEIGHT_MM,
) -> dict[str, Any]:
    """
    Read schematic.json and return structured context for the orchestrator.

    Parameters
    ----------
    schematic_path:
        Path to schematic.json.  Defaults to SCHEMATIC_FILE env var.
    board_width_mm:
        PCB board width hint in mm (default 100).
    board_height_mm:
        PCB board height hint in mm (default 80).

    Returns
    -------
    dict with keys:
        project_name    — string
        board           — {"width_mm": float, "height_mm": float}
        components      — list of {reference, value, footprint, nets: [str]}
        net_connections — dict mapping net_name → list of "REF.pin" strings
    """
    path = Path(schematic_path) if schematic_path else SCHEMATIC_FILE
    log.info("Reading schematic from %s", path)
    with open(path) as f:
        schematic = json.load(f)

    components_raw = schematic.get("components", [])
    nets_raw = schematic.get("nets", [])

    # Build a net→connections map from component connection data.
    net_connections: dict[str, list[str]] = {}
    components_out: list[dict[str, Any]] = []

    for comp in components_raw:
        ref = comp.get("reference", "?")
        connections = comp.get("connections", [])
        comp_nets: list[str] = []
        for conn in connections:
            net = conn.get("net", "")
            pin = conn.get("pin", "?")
            if net:
                comp_nets.append(net)
                net_connections.setdefault(net, []).append(f"{ref}.{pin}")
        components_out.append({
            "reference": ref,
            "value": comp.get("value", ""),
            "footprint": comp.get("footprint", ""),
            "nets": list(dict.fromkeys(comp_nets)),  # deduplicated, order preserved
        })

    return {
        "project_name": schematic.get("project_name", "PCB Project"),
        "board": {
            "width_mm": board_width_mm,
            "height_mm": board_height_mm,
        },
        "components": components_out,
        "net_connections": net_connections,
    }


# ---------------------------------------------------------------------------
# Internal helpers shared by apply_placement
# ---------------------------------------------------------------------------

def _enrich_placements_with_footprints(
    placements: list[dict[str, Any]],
    schematic: dict[str, Any],
) -> list[dict[str, Any]]:
    """Inject footprint into each placement from the schematic so the optimizer
    can estimate bounding boxes."""
    fp_map = {c["reference"]: c.get("footprint", "") for c in schematic.get("components", [])}
    enriched = []
    for p in placements:
        ep = dict(p)
        ep.setdefault("footprint", fp_map.get(p.get("reference", ""), ""))
        enriched.append(ep)
    return enriched


def _run_drc(placement_path: Path) -> tuple[bool, str]:
    """Run the external DRC checker script. Returns (passed, output_text)."""
    result = subprocess.run(
        [sys.executable, str(DRC_TOOL), str(placement_path), "--check", "clearance,courtyard"],
        capture_output=True,
        text=True,
    )
    output = result.stdout + result.stderr
    return result.returncode == 0, output


# ---------------------------------------------------------------------------
# Tool: apply_placement
# ---------------------------------------------------------------------------

def apply_placement(
    placement_dict: dict[str, Any],
    output_dir: str | Path | None = None,
) -> dict[str, Any]:
    """
    Validate a placement dict produced by the orchestrator, write it to disk,
    and return a result summary.

    The placement dict must follow the schema:
        {
          "board": {"width_mm": float, "height_mm": float},
          "placements": [
            {
              "reference": str,
              "x": float, "y": float,
              "rotation": 0|90|180|270,
              "layer": "F.Cu",
              "footprint": str,      # optional — enriched from schematic if absent
              "rationale": str
            },
            ...
          ]
        }

    Parameters
    ----------
    placement_dict:
        The placement data to validate and persist.
    output_dir:
        Directory to write placement.json.  Defaults to OUTPUT_DIR env var.

    Returns
    -------
    {
        "success": bool,
        "violations": list[dict],   # courtyard violations (empty if none)
        "drc_passed": bool,
        "drc_output": str,
        "placement_path": str | None,
        "message": str,
    }
    """
    out_dir = Path(output_dir) if output_dir else OUTPUT_DIR
    placement_path = out_dir / "placement.json"

    placements = placement_dict.get("placements", [])

    # --- Courtyard check via Polars optimizer ---
    # Footprints may or may not be in the placement dict; pass what we have.
    opt_result = check_courtyard_clearances(placements)
    violations = opt_result.get("violations", [])

    if not opt_result["is_valid"]:
        summary = summarize_violations(opt_result)
        log.warning("Courtyard violations:\n%s", summary)
        return {
            "success": False,
            "violations": violations,
            "drc_passed": False,
            "drc_output": "",
            "placement_path": None,
            "message": f"Courtyard check failed: {summary}",
        }

    # --- Write to disk ---
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(placement_path, "w") as f:
        json.dump(placement_dict, f, indent=2)
    log.info("Wrote placement to %s", placement_path)

    # --- External DRC ---
    drc_passed, drc_output = _run_drc(placement_path)
    log.info("DRC output:\n%s", drc_output)

    if drc_passed:
        message = "Placement applied successfully. DRC passed."
    else:
        message = f"Placement written but DRC failed: {drc_output[:300]}"
        log.warning("DRC failed after writing placement")

    return {
        "success": drc_passed,
        "violations": violations,
        "drc_passed": drc_passed,
        "drc_output": drc_output,
        "placement_path": str(placement_path),
        "message": message,
    }


# ---------------------------------------------------------------------------
# Tool: compute_placement
# ---------------------------------------------------------------------------

def compute_placement(
    schematic_path: str | Path | None = None,
    connectivity_path: str | Path | None = None,
    board_width_mm: float = BOARD_WIDTH_MM,
    board_height_mm: float = BOARD_HEIGHT_MM,
    min_clearance_mm: float = 0.25,
    iterations: int = 250,
) -> dict[str, Any]:
    """
    One-shot tool: read schematic (+ optional connectivity), compute net-proximity
    placements, and return a placement dict ready to pass to apply_placement().

    This uses the force-directed net-proximity algorithm in placement_optimizer:
      - Components are pulled toward the centroid of their net-neighbours.
      - Courtyard clearances (package-to-package gap) are enforced iteratively.
      - Connectors are pinned to the board top edge.

    Parameters
    ----------
    schematic_path:
        Path to schematic.json.  Defaults to SCHEMATIC_FILE env var.
    connectivity_path:
        Path to connectivity.json (optional — augments net data if present).
    board_width_mm, board_height_mm:
        Board dimensions in mm.
    min_clearance_mm:
        Minimum package-to-package gap (courtyard clearance).  Default 0.25 mm.
    iterations:
        Force-directed optimisation iterations.  More = tighter packing.

    Returns
    -------
    A placement dict that can be passed directly to apply_placement():
        {
          "board":      {"width_mm": float, "height_mm": float},
          "placements": [{reference, x, y, rotation, layer, footprint, rationale}, ...]
        }
    """
    # --- 1. Load schematic context ---
    context = get_placement_context(
        schematic_path=schematic_path,
        board_width_mm=board_width_mm,
        board_height_mm=board_height_mm,
    )
    components = context["components"]      # [{reference, value, footprint, nets}]
    net_connections = context["net_connections"]

    # --- 2. Optionally augment net membership from connectivity.json ---
    conn_path = Path(connectivity_path) if connectivity_path else (OUTPUT_DIR / "connectivity.json")
    if conn_path.exists():
        log.info("Augmenting net data from %s", conn_path)
        with open(conn_path) as f:
            connectivity = json.load(f)
        # connectivity.json may contain a "nets" list with richer connection data;
        # merge any nets referenced there into the component dicts.
        extra_nets: dict[str, list[str]] = {}
        for item in connectivity.get("nets", []):
            net_name = item.get("name", "")
            for pin_ref in item.get("pins", []):
                # pin_ref format: "REF.pin"
                ref = pin_ref.split(".")[0]
                extra_nets.setdefault(ref, [])
                if net_name and net_name not in extra_nets[ref]:
                    extra_nets[ref].append(net_name)
        for comp in components:
            ref = comp["reference"]
            for net in extra_nets.get(ref, []):
                if net not in comp["nets"]:
                    comp["nets"].append(net)

    # --- 3. Run force-directed net-proximity placement ---
    log.info(
        "Running net-proximity placement for %d components on %g×%g mm board "
        "(clearance=%.2f mm, iterations=%d)",
        len(components), board_width_mm, board_height_mm, min_clearance_mm, iterations,
    )
    placements = compute_net_proximity_placement(
        components=components,
        board_width_mm=board_width_mm,
        board_height_mm=board_height_mm,
        min_clearance_mm=min_clearance_mm,
        iterations=iterations,
    )

    placement_dict: dict[str, Any] = {
        "board": {"width_mm": board_width_mm, "height_mm": board_height_mm},
        "placements": placements,
    }

    log.info("compute_placement complete — %d components placed", len(placements))
    return placement_dict


# ---------------------------------------------------------------------------
# Stub: run() — orchestration belongs to Claude Code
# ---------------------------------------------------------------------------

def run() -> None:
    """Not implemented. Orchestration is handled by Claude Code (the orchestrator).

    To perform a placement:
      1. Call get_placement_context() to get schematic data.
      2. Let the orchestrator design the placement.
      3. Call apply_placement(placement_dict) to validate and persist.
    """
    raise NotImplementedError(
        "run() is not implemented. "
        "Use get_placement_context() + apply_placement() via the orchestrator."
    )
