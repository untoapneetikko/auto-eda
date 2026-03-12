"""
agents/footprint/agent.py

Footprint Agent — pure tools layer.

Claude Code (the orchestrator) handles all reasoning. This module:
  1. Reads datasheet.json and returns a compact summary (get_datasheet_summary)
  2. Accepts a footprint dict designed by the orchestrator, validates it,
     generates .kicad_mod, and writes outputs (apply_footprint)

No Anthropic SDK calls here. This is intentional.
"""

from __future__ import annotations

import io
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch as _patch

# ── resolve project root so relative paths always work ────────────────────
_AGENT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _AGENT_DIR.parent.parent

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


# ── ensure tools directory is importable ─────────────────────────────────
def _ensure_tools_on_path() -> None:
    tools_dir = str(_PROJECT_ROOT / "backend" / "tools")
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)


def _ensure_agent_on_path() -> None:
    agent_dir = str(_AGENT_DIR)
    if agent_dir not in sys.path:
        sys.path.insert(0, agent_dir)


# ── public API ────────────────────────────────────────────────────────────

def get_datasheet_summary(datasheet_path: str | Path | None = None) -> dict[str, Any]:
    """
    Read datasheet.json and return a compact summary dict.

    The summary contains only what the orchestrator needs to design a footprint:
      - package        : package type string (e.g. "SOT-23")
      - pad_count      : number of pins / pads
      - pitch_mm       : pin pitch in mm, or None if not available
      - courtyard_mm   : {"x": float, "y": float} body courtyard hint, or None
      - electrical     : subset of electrical characteristics (vcc_max, i_max_ma, etc.)
      - component_name : component identifier string

    Parameters
    ----------
    datasheet_path : optional override path for datasheet.json (used in tests)

    Raises
    ------
    FileNotFoundError  – if datasheet.json does not exist
    ValueError         – if datasheet.json is malformed / missing required fields
    """
    path = Path(datasheet_path) if datasheet_path else DATASHEET_JSON
    if not path.exists():
        raise FileNotFoundError(f"datasheet.json not found at {path}")

    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, dict):
        raise ValueError("datasheet.json must be a JSON object")

    # Extract footprint block (may or may not be present)
    fp_block: dict[str, Any] = data.get("footprint") or {}

    summary: dict[str, Any] = {
        "component_name": data.get("component_name") or data.get("name") or "unknown",
        "package": (
            fp_block.get("standard")
            or data.get("package")
            or "unknown"
        ),
        "pad_count": (
            fp_block.get("pad_count")
            or len(data.get("pins", []))
        ),
        "pitch_mm": fp_block.get("pitch_mm"),
        "courtyard_mm": fp_block.get("courtyard_mm"),
        "electrical": data.get("electrical"),
    }

    log.info(
        "get_datasheet_summary: component=%s package=%s pad_count=%s",
        summary["component_name"],
        summary["package"],
        summary["pad_count"],
    )
    return summary


def apply_footprint(
    footprint_dict: dict[str, Any],
    output_dir: str | Path | None = None,
) -> dict[str, Any]:
    """
    Validate a footprint dict designed by the orchestrator, generate a
    .kicad_mod file, and write both artefacts to data/outputs/.

    Parameters
    ----------
    footprint_dict : footprint definition matching shared/schemas/footprint_output.json.
                     Extra keys (package, ipc_standard, sources, etc.) are allowed
                     and will be preserved in footprint.json but stripped before
                     schema validation.
    output_dir     : optional override for the output directory (used in tests)

    Returns
    -------
    {
        "success": bool,
        "files":   list[str],   # absolute paths of written files
        "errors":  list[str],   # empty on success
    }
    """
    _ensure_tools_on_path()
    _ensure_agent_on_path()

    out_dir = Path(output_dir) if output_dir else OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    footprint_json_path = out_dir / "footprint.json"
    footprint_kicad_path = out_dir / "footprint.kicad_mod"

    errors: list[str] = []
    files: list[str] = []

    # ── 1. Normalise required keys ─────────────────────────────────────────
    fp = dict(footprint_dict)  # shallow copy; don't mutate caller's dict
    if "format" not in fp:
        fp["format"] = "kicad_mod"
    if "silkscreen" not in fp:
        fp["silkscreen"] = []

    # ── 2. Validate against schema (stripped to required keys only) ────────
    required_keys = {"name", "pads", "courtyard", "silkscreen", "format"}
    stripped = {k: v for k, v in fp.items() if k in required_keys}

    schema_path = SCHEMA_DIR / "footprint_output.json"
    if not schema_path.exists():
        errors.append(f"Schema not found at {schema_path}")
        return {"success": False, "files": files, "errors": errors}

    # Write stripped version to a temp file for schema_validator
    import tempfile
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    ) as tmp_f:
        json.dump(stripped, tmp_f)
        tmp_path = tmp_f.name

    try:
        import schema_validator  # type: ignore

        buf = io.StringIO()
        with _patch("builtins.print", lambda *a, **k: buf.write(" ".join(str(x) for x in a) + "\n")):
            schema_ok = schema_validator.validate(tmp_path, str(schema_path))

        if not schema_ok:
            errors.append(f"footprint dict failed schema validation: {buf.getvalue().strip()}")
            return {"success": False, "files": files, "errors": errors}

        log.info("Schema validation passed")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"Schema validation error: {exc}")
        return {"success": False, "files": files, "errors": errors}
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    # ── 3. Write full footprint.json (preserves extra metadata keys) ───────
    try:
        with open(footprint_json_path, "w", encoding="utf-8") as f:
            json.dump(fp, f, indent=2)
        files.append(str(footprint_json_path))
        log.info("Wrote %s", footprint_json_path)
    except OSError as exc:
        errors.append(f"Failed to write footprint.json: {exc}")
        return {"success": False, "files": files, "errors": errors}

    # ── 4. Generate .kicad_mod via kicad_mod_writer ────────────────────────
    try:
        from kicad_mod_writer import write as write_kicad_mod  # type: ignore

        kicad_content = write_kicad_mod(fp)
        with open(footprint_kicad_path, "w", encoding="utf-8") as f:
            f.write(kicad_content)
        log.info("Wrote %s", footprint_kicad_path)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"kicad_mod_writer failed: {exc}")
        return {"success": False, "files": files, "errors": errors}

    # ── 5. Validate .kicad_mod ─────────────────────────────────────────────
    try:
        import kicad_validator  # type: ignore

        buf = io.StringIO()
        with _patch("builtins.print", lambda *a, **k: buf.write(" ".join(str(x) for x in a) + "\n")):
            kicad_ok = kicad_validator.validate_sexpr(str(footprint_kicad_path))

        if not kicad_ok:
            errors.append(f"footprint.kicad_mod failed KiCad validation: {buf.getvalue().strip()}")
            return {"success": False, "files": files, "errors": errors}

        files.append(str(footprint_kicad_path))
        log.info("KiCad validation passed")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"KiCad validation error: {exc}")
        return {"success": False, "files": files, "errors": errors}

    log.info(
        "apply_footprint complete: %s  pads=%d  files=%s",
        fp.get("name"),
        len(fp.get("pads", [])),
        files,
    )
    return {"success": True, "files": files, "errors": []}


def run() -> dict[str, Any]:
    """
    Removed: orchestration is now handled by Claude Code (the orchestrator).

    Use get_datasheet_summary() to read inputs and apply_footprint() to
    write outputs. This function exists only as a backwards-compatibility stub.
    """
    raise NotImplementedError(
        "run() has been removed. "
        "Use get_datasheet_summary() + apply_footprint() instead. "
        "Claude Code (the orchestrator) designs the footprint between those two calls."
    )
