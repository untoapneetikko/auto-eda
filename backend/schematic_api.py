"""
backend/schematic_api.py — Full port of schematic_designer server.js to FastAPI.

Mounts all /api/* endpoints used by index.html and pcb.html:
  /api/library, /api/footprints, /api/projects, /api/pcb-boards,
  /api/issues, /api/gen-tickets, /api/agents, /api/events,
  /api/upload, /api/build-from-netlist, /api/build-project, /api/generate-circuit,
  /api/export-library, /api/import-library, /api/export-design, /api/import-design
"""
from __future__ import annotations

import asyncio
import heapq
import threading
import io
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pdfplumber
from fastapi import APIRouter, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

# ── Extracted engines (pure algorithm modules) ────────────────────────────────
from backend.engines.netlist import (
    build_netlist, ic_layout, _SYMDEFS, _rotate_offset, _snap, _pt_key, _POWER_NAMES,
)
from backend.engines.drc import run_drc
from backend.engines.autoroute import run_autoroute, _autoroute_skip_net, _autoroute_trace_width
from backend.engines.autoplace import run_autoplace, _load_schematic_hints, _positions_are_spread
from backend.engines.importer import import_schematic, _pick_footprint, _match_package_to_footprint

router = APIRouter(prefix="/api")

# ── Paths ─────────────────────────────────────────────────────────────────────
_HERE = Path(__file__).parent.parent
STATIC_DIR   = _HERE / "frontend" / "static"
LIBRARY_DIR  = STATIC_DIR / "library"
INBOX_DIR    = LIBRARY_DIR / "_inbox"
FOOTPRINTS_DIR = STATIC_DIR / "pcb" / "footprints"
PROJECTS_DIR = STATIC_DIR / "projects"
PCB_BOARDS_DIR = STATIC_DIR / "pcb-boards"
ISSUES_FILE  = STATIC_DIR / "improvements.json"
GEN_TICKETS_FILE = STATIC_DIR / "gen_tickets.json"

for _d in [LIBRARY_DIR, INBOX_DIR, FOOTPRINTS_DIR, PROJECTS_DIR, PCB_BOARDS_DIR]:
    _d.mkdir(parents=True, exist_ok=True)

# ── SSE clients ───────────────────────────────────────────────────────────────
_sse_queues: list[asyncio.Queue] = []

def _broadcast(event: str, data: dict):
    msg = f"event: {event}\ndata: {json.dumps(data)}\n\n"
    for q in list(_sse_queues):
        try:
            q.put_nowait(msg)
        except asyncio.QueueFull:
            pass

# ── Agent registry (in-memory) ────────────────────────────────────────────────
_agents: dict[str, dict] = {}
_STALE_SECONDS = 120

def _prune_stale():
    now = time.time()
    dead = [aid for aid, a in _agents.items()
            if a.get("status") == "working"
            and (now - _parse_ts(a.get("last_ping", ""))) > _STALE_SECONDS]
    for aid in dead:
        _agents[aid]["status"] = "stale"

def _parse_ts(ts: str) -> float:
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0

# ── Library index helpers ─────────────────────────────────────────────────────
def _read_index() -> dict:
    idx_path = LIBRARY_DIR / "index.json"
    if idx_path.exists():
        try:
            return json.loads(idx_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return _rebuild_index()

def _rebuild_index() -> dict:
    idx: dict[str, Any] = {}
    for slug_dir in LIBRARY_DIR.iterdir():
        if slug_dir.name.startswith("_") or not slug_dir.is_dir():
            continue
        profile_path = slug_dir / "profile.json"
        if not profile_path.exists():
            continue
        try:
            p = json.loads(profile_path.read_text(encoding="utf-8"))
            idx[slug_dir.name] = {
                "slug": slug_dir.name,
                "part_number": p.get("part_number", slug_dir.name),
                "description": p.get("description", ""),
                "manufacturer": p.get("manufacturer", ""),
                "status": p.get("status", "parsed"),
                "confidence": p.get("confidence", "HIGH"),
                "symbol_type": p.get("symbol_type", "ic"),
                "package_types": p.get("package_types", []),
                "builtin": p.get("builtin", False),
                "filename": p.get("filename", ""),
                "uploaded_at": p.get("uploaded_at"),
                "parsed_at": p.get("parsed_at"),
            }
        except Exception:
            pass
    (LIBRARY_DIR / "index.json").write_text(json.dumps(idx, indent=2), encoding="utf-8")
    return idx

def _update_index():
    _rebuild_index()

def _profile_path(slug: str) -> Path:
    return LIBRARY_DIR / slug / "profile.json"

def _read_profile(slug: str) -> dict:
    p = _profile_path(slug)
    if not p.exists():
        raise HTTPException(status_code=404, detail="Not found")
    data = json.loads(p.read_text(encoding="utf-8"))
    if "slug" not in data:
        data["slug"] = slug
    return data

def _write_profile(slug: str, profile: dict):
    p = _profile_path(slug)
    p.write_text(json.dumps(profile, indent=2), encoding="utf-8")

# ── Issues helpers ────────────────────────────────────────────────────────────
_ISSUES_SEED = {
    "nextId": 13,
    "issues": [
        {"id":1,"title":"Add ATmega328P seed component","description":"Full 28-pin profile for the Arduino MCU.","status":"backlog","priority":"high","created_at":"2026-03-09T00:00:00Z","updated_at":"2026-03-09T00:00:00Z"},
        {"id":2,"title":"Add ESP32 seed component","description":"Generic ESP32 module profile.","status":"backlog","priority":"high","created_at":"2026-03-09T00:00:00Z","updated_at":"2026-03-09T00:00:00Z"},
        {"id":3,"title":"Add STM32F103C8T6 seed component","description":"Blue Pill MCU, 48 pins.","status":"backlog","priority":"medium","created_at":"2026-03-09T00:00:00Z","updated_at":"2026-03-09T00:00:00Z"},
        {"id":4,"title":"Net labels on schematic","description":"Same-name net labels = same electrical net.","status":"backlog","priority":"high","created_at":"2026-03-09T00:00:00Z","updated_at":"2026-03-09T00:00:00Z"},
        {"id":5,"title":"Export schematic to PNG","description":"Render canvas to PNG.","status":"backlog","priority":"high","created_at":"2026-03-09T00:00:00Z","updated_at":"2026-03-09T00:00:00Z"},
        {"id":6,"title":"Export schematic to SVG","description":"Generate clean SVG file.","status":"backlog","priority":"medium","created_at":"2026-03-09T00:00:00Z","updated_at":"2026-03-09T00:00:00Z"},
        {"id":7,"title":"Tag / category filtering in library","description":"Add category tags and filter chips.","status":"backlog","priority":"medium","created_at":"2026-03-09T00:00:00Z","updated_at":"2026-03-09T00:00:00Z"},
        {"id":8,"title":"Import profile from JSON","description":"Button to import a profile.json directly.","status":"backlog","priority":"low","created_at":"2026-03-09T00:00:00Z","updated_at":"2026-03-09T00:00:00Z"},
        {"id":9,"title":"AI circuit generator (Phase 4)","description":"Describe a circuit → AI generates full schematic.","status":"backlog","priority":"high","created_at":"2026-03-09T00:00:00Z","updated_at":"2026-03-09T00:00:00Z"},
        {"id":10,"title":"Cross-component compatibility checks","description":"Voltage, current, logic level checks.","status":"backlog","priority":"high","created_at":"2026-03-09T00:00:00Z","updated_at":"2026-03-09T00:00:00Z"},
        {"id":11,"title":"Power budget calculator","description":"Sum current draw from all placed components.","status":"backlog","priority":"medium","created_at":"2026-03-09T00:00:00Z","updated_at":"2026-03-09T00:00:00Z"},
        {"id":12,"title":"Safety report (DRC)","description":"Design Rule Check — scan for floating pins, missing decoupling caps.","status":"backlog","priority":"medium","created_at":"2026-03-09T00:00:00Z","updated_at":"2026-03-09T00:00:00Z"},
    ]
}

def _read_issues() -> dict:
    if not ISSUES_FILE.exists():
        ISSUES_FILE.write_text(json.dumps(_ISSUES_SEED, indent=2), encoding="utf-8")
        return dict(_ISSUES_SEED)
    return json.loads(ISSUES_FILE.read_text(encoding="utf-8"))

def _save_issues(data: dict):
    ISSUES_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")

def _read_gen_tickets() -> dict:
    if not GEN_TICKETS_FILE.exists():
        init = {"nextId": 1, "tickets": []}
        GEN_TICKETS_FILE.write_text(json.dumps(init, indent=2), encoding="utf-8")
        return init
    return json.loads(GEN_TICKETS_FILE.read_text(encoding="utf-8"))

def _save_gen_tickets(data: dict):
    GEN_TICKETS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")

# ── Upload PDF ────────────────────────────────────────────────────────────────
@router.post("/upload")
async def api_upload(file: UploadFile):
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "PDF file required")

    original_name = re.sub(r"[^a-zA-Z0-9._-]", "_", file.filename)
    slug = re.sub(r"[^a-zA-Z0-9\-_]", "-", original_name.replace(".pdf", "")).upper()
    part_dir = LIBRARY_DIR / slug
    part_dir.mkdir(parents=True, exist_ok=True)

    pdf_bytes = await file.read()
    pdf_path = part_dir / "original.pdf"
    pdf_path.write_bytes(pdf_bytes)

    raw_text = ""
    confidence = "HIGH"
    extraction_note = ""
    page_count = 0

    try:
        import io
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            page_count = len(pdf.pages)
            raw_text = "\n".join(p.extract_text() or "" for p in pdf.pages)

        char_count = len(re.sub(r"\s", "", raw_text))
        has_pin_table = bool(re.search(r"pin\s*\d|vcc|gnd|vss|vdd", raw_text, re.I))
        non_ascii = len(re.findall(r"[^\x00-\x7F]", raw_text))
        garbage_ratio = non_ascii / max(char_count, 1)

        if char_count < 500:
            confidence = "LOW"
            extraction_note = "Very little text extracted — PDF may be a scanned image."
        elif not has_pin_table:
            confidence = "MEDIUM"
            extraction_note = "No pin table pattern detected in extracted text."
        elif garbage_ratio > 0.1:
            confidence = "MEDIUM"
            extraction_note = "High proportion of non-ASCII characters."

        if len(raw_text) > 60000:
            raw_text = raw_text[:60000] + f"\n\n[TRUNCATED — full doc is {page_count} pages]"
    except Exception as e:
        confidence = "FAILED"
        extraction_note = f"PDF text extraction failed: {e}"

    (part_dir / "raw_text.txt").write_text(raw_text, encoding="utf-8")

    profile = {
        "part_number": None,
        "status": "pending_parse",
        "confidence": confidence,
        "extraction_note": extraction_note or None,
        "filename": original_name,
        "page_count": page_count,
        "raw_text_length": len(re.sub(r"\s", "", raw_text)),
        "uploaded_at": datetime.now(timezone.utc).isoformat(),
        "parsed_at": None,
        "human_corrections": [],
    }
    _write_profile(slug, profile)
    _update_index()
    _broadcast("library_updated", {"slug": slug, "status": "pending_parse"})

    # Auto-queue the datasheet parser — no manual Claude Code step needed
    gt = _read_gen_tickets()
    gt["tickets"].append({
        "id": gt["nextId"],
        "type": "datasheet",
        "slug": slug,
        "title": f"Parse {slug}",
        "prompt": f"Parse the datasheet for component {slug}.\nPDF file: data/uploads/{original_name}\nRaw text already extracted: frontend/static/library/{slug}/raw_text.txt\nWrite the structured profile to: frontend/static/library/{slug}/profile.json\n\nIMPORTANT: Set part_number to the SHORT part number only (e.g. 'MAX2870', 'LM358'). No descriptions, no suffixes, no series names.",
        "status": "pending",
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    gt["nextId"] += 1
    _save_gen_tickets(gt)

    return {"slug": slug, "confidence": confidence, "extractionNote": extraction_note,
            "charCount": profile["raw_text_length"]}

# ── Library index ─────────────────────────────────────────────────────────────
@router.get("/library")
def api_library_list():
    return _read_index()

@router.post("/library/new")
async def api_library_new(request: Request):
    body = await request.json()
    part_number = body.get("part_number")
    if not part_number:
        raise HTTPException(400, "part_number required")
    slug = re.sub(r"[^a-zA-Z0-9_\-]", "_", part_number).upper()
    d = LIBRARY_DIR / slug
    if d.exists():
        raise HTTPException(409, f"Component already exists: {slug}")
    d.mkdir(parents=True, exist_ok=True)
    profile = {
        "part_number": part_number,
        "description": body.get("description", ""),
        "manufacturer": "",
        "package_types": [],
        "supply_voltage_range": "",
        "absolute_max": {},
        "pins": body.get("pins", []),
        "required_passives": [],
        "application_circuits": [],
        "common_mistakes": [],
        "notes": "",
        "symbol_type": body.get("symbol_type", "ic"),
        "status": "parsed",
        "confidence": "HIGH",
        "extraction_note": None,
        "filename": "manual",
        "uploaded_at": None,
        "parsed_at": datetime.now(timezone.utc).isoformat(),
        "builtin": False,
        "human_corrections": [],
    }
    _write_profile(slug, profile)
    _update_index()
    return {"ok": True, "slug": slug}

@router.post("/library/import-profile")
async def api_library_import_profile(request: Request):
    """Import a single profile.json file into the library."""
    body = await request.json()
    # Require at least part_number or slug
    slug = body.get("slug") or (body.get("part_number") and re.sub(r"[^a-zA-Z0-9_\-]", "_", body["part_number"]).upper())
    if not slug:
        raise HTTPException(400, "profile must have part_number or slug field")
    d = LIBRARY_DIR / slug
    d.mkdir(parents=True, exist_ok=True)
    # Preserve human_corrections if component already exists
    pp = _profile_path(slug)
    if pp.exists():
        try:
            existing = json.loads(pp.read_text("utf-8"))
            if existing.get("human_corrections") and not body.get("human_corrections"):
                body["human_corrections"] = existing["human_corrections"]
        except Exception:
            pass
    _write_profile(slug, body)
    _update_index()
    return {"ok": True, "slug": slug, "updated": pp.exists()}

@router.get("/library/{slug}/raw")
def api_library_raw(slug: str):
    txt = LIBRARY_DIR / slug / "raw_text.txt"
    if not txt.exists():
        raise HTTPException(404, "Not found")
    return txt.read_text(encoding="utf-8")

@router.get("/library/{slug}/pdf")
def api_library_pdf(slug: str):
    pdf = LIBRARY_DIR / slug / "original.pdf"
    if not pdf.exists():
        raise HTTPException(404, "No PDF")
    return FileResponse(str(pdf), media_type="application/pdf",
                        headers={"Content-Disposition": "inline"})

@router.get("/library/{slug}")
def api_library_get(slug: str):
    return _read_profile(slug)

@router.post("/library/{slug}/correction")
async def api_library_correction(slug: str, request: Request):
    body = await request.json()
    profile = _read_profile(slug)
    profile.setdefault("human_corrections", [])
    profile["human_corrections"].append({
        "date": datetime.now(timezone.utc).date().isoformat(),
        **body
    })
    _write_profile(slug, profile)
    return {"ok": True}

@router.put("/library/{slug}/designator")
async def api_library_designator(slug: str, request: Request):
    body = await request.json()
    if not body.get("designator"):
        raise HTTPException(400, "designator required")
    profile = _read_profile(slug)
    profile["designator"] = body["designator"].upper()
    _write_profile(slug, profile)
    return {"ok": True}

@router.put("/library/{slug}/symbol_type")
async def api_library_symbol_type(slug: str, request: Request):
    body = await request.json()
    if not body.get("symbol_type"):
        raise HTTPException(400, "symbol_type required")
    profile = _read_profile(slug)
    if profile.get("builtin"):
        raise HTTPException(403, "Built-in components are read-only")
    profile["symbol_type"] = body["symbol_type"]
    _write_profile(slug, profile)
    return {"ok": True}

@router.put("/library/{slug}/params")
async def api_library_params(slug: str, request: Request):
    body = await request.json()
    profile = _read_profile(slug)
    if profile.get("builtin"):
        raise HTTPException(403, "Built-in components are read-only")
    allowed = {"part_number", "manufacturer", "value", "designator", "description"}
    for key in allowed:
        if key in body and isinstance(body[key], str):
            profile[key] = body[key]
    _write_profile(slug, profile)
    return {"ok": True}

@router.put("/library/{slug}/pins")
async def api_library_pins(slug: str, request: Request):
    body = await request.json()
    if not isinstance(body.get("pins"), list):
        raise HTTPException(400, "pins array required")
    profile = _read_profile(slug)
    if profile.get("builtin"):
        raise HTTPException(403, "Built-in components are read-only")
    profile["pins"] = body["pins"]
    _write_profile(slug, profile)
    return {"ok": True}

@router.put("/library/{slug}/example_circuit")
async def api_library_example_circuit(slug: str, request: Request):
    body = await request.json()
    profile = _read_profile(slug)
    profile["example_circuit"] = body
    _write_profile(slug, profile)
    return {"ok": True}

@router.put("/library/{slug}/layout_example")
async def api_library_layout_example(slug: str, request: Request):
    body = await request.json()
    profile = _read_profile(slug)
    profile["layout_example"] = body
    _write_profile(slug, profile)

    # Update the active version snapshot in-place — do NOT create a new version.
    # If an active version exists, overwrite its JSON file with the updated profile.
    # Only fall back to creating a new snapshot when there is no active version yet.
    av_path = _active_version_path(slug)
    active_id = None
    v_num = 1
    label = "layout_example"
    if av_path.exists():
        try:
            av = json.loads(av_path.read_text(encoding="utf-8"))
            active_id = av.get("id")
            v_num = av.get("vNum", 1)
            label = av.get("label", "layout_example")
        except Exception:
            pass

    if active_id:
        snap_path = _history_dir(slug) / (active_id + ".json")
        if snap_path.exists():
            snap_path.write_text(json.dumps({
                "saved_at": datetime.now(timezone.utc).isoformat(),
                "label": label,
                "profile": json.loads(_profile_path(slug).read_text(encoding="utf-8")),
            }, indent=2), encoding="utf-8")
            return {"ok": True, "snapshot_id": active_id}
        # Snapshot file missing — fall through to create fresh

    snapshot_id = _snapshot_profile(slug, label)
    if snapshot_id:
        h = _history_dir(slug)
        files = sorted(h.glob("*.json"))
        v_num = next((i + 1 for i, f in enumerate(files) if f.stem == snapshot_id), 1)
        av_path.write_text(
            json.dumps({"id": snapshot_id, "label": label, "vNum": v_num}, indent=2),
            encoding="utf-8")
    return {"ok": True, "snapshot_id": snapshot_id}

@router.put("/library/{slug}/footprint")
async def api_library_footprint(slug: str, request: Request):
    body = await request.json()
    profile = _read_profile(slug)
    profile["footprint"] = body.get("footprint")
    _write_profile(slug, profile)
    return {"ok": True}

@router.post("/library/{slug}/generate-footprint")
async def api_generate_footprint(slug: str, request: Request):
    body = await request.json() if request.headers.get("content-length", "0") != "0" else {}
    profile = _read_profile(slug)
    pkg = body.get("package") or (profile.get("package_types") or [""])[0]
    pins = profile.get("pins", [])
    result = _generate_footprint_rules(pkg, pins, profile.get("part_number", slug))
    if result is None:
        raise HTTPException(422, f"No rule matches package: {pkg}")
    name = re.sub(r"[^a-zA-Z0-9_\-]", "_", result["name"])
    fp_file = FOOTPRINTS_DIR / (name + ".json")
    fp_file.write_text(json.dumps(result, indent=2), encoding="utf-8")
    profile["footprint"] = result["name"]
    _write_profile(slug, profile)
    return {"footprint": result, "method": "rule-based", "confidence": "HIGH"}

@router.delete("/library/{slug}")
def api_library_delete(slug: str):
    d = LIBRARY_DIR / slug
    if d.exists():
        shutil.rmtree(d)
    _update_index()
    return {"ok": True}

# ── Footprints ─────────────────────────────────────────────────────────────────
@router.get("/footprints")
def api_footprints_list():
    result = []
    for f in FOOTPRINTS_DIR.glob("*.json"):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            result.append({"name": d.get("name"), "description": d.get("description"),
                           "pin_count": d.get("pin_count"), "file": f.name})
        except Exception:
            pass
    return result

@router.get("/footprints/{name}")
def api_footprints_get(name: str):
    fname = name if name.endswith(".json") else name + ".json"
    fp = FOOTPRINTS_DIR / fname
    if not fp.exists():
        raise HTTPException(404, "Not found")
    return json.loads(fp.read_text(encoding="utf-8"))

@router.put("/footprints/{name}")
async def api_footprints_put(name: str, request: Request):
    body = await request.json()
    safe = re.sub(r"[^a-zA-Z0-9_\-\.]", "", name)
    fname = safe if safe.endswith(".json") else safe + ".json"
    (FOOTPRINTS_DIR / fname).write_text(json.dumps(body, indent=2), encoding="utf-8")
    return {"ok": True}

# ── Projects ──────────────────────────────────────────────────────────────────
@router.get("/projects")
def api_projects_list():
    projects = []
    for f in PROJECTS_DIR.glob("*.json"):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            projects.append({"id": d["id"], "name": d.get("name"), "updated_at": d.get("updated_at"),
                              "component_count": len(d.get("components", []))})
        except Exception:
            pass
    projects.sort(key=lambda x: x.get("updated_at") or "", reverse=True)
    return projects

@router.post("/projects")
async def api_projects_create(request: Request):
    p = await request.json()
    if not p.get("id"):
        import secrets
        p["id"] = secrets.token_hex(6)
    p.setdefault("created_at", datetime.now(timezone.utc).isoformat())
    p["updated_at"] = datetime.now(timezone.utc).isoformat()
    (PROJECTS_DIR / (p["id"] + ".json")).write_text(json.dumps(p, indent=2), encoding="utf-8")
    return {"id": p["id"]}

@router.get("/projects/{pid}")
def api_projects_get(pid: str):
    f = PROJECTS_DIR / (pid + ".json")
    if not f.exists():
        raise HTTPException(404, "Not found")
    return json.loads(f.read_text(encoding="utf-8"))

@router.delete("/projects/{pid}")
def api_projects_delete(pid: str):
    f = PROJECTS_DIR / (pid + ".json")
    if f.exists():
        f.unlink()
    deleted_boards = 0
    for bf in PCB_BOARDS_DIR.glob("*.json"):
        try:
            d = json.loads(bf.read_text(encoding="utf-8"))
            if d.get("projectId") == pid:
                bf.unlink()
                deleted_boards += 1
        except Exception:
            pass
    return {"ok": True, "deletedBoards": deleted_boards}

# ── Export / Import design ────────────────────────────────────────────────────
_BUILTIN_SLUGS = {"RESISTOR","CAPACITOR","CAPACITOR_POL","INDUCTOR","VCC","GND",
                  "DIODE","LED","LM7805","AMS1117-3.3","NE555","2N2222","BC547",
                  "IRF540N","LM358","L298N","DRV8833","ATMEGA328P","ESP32_WROOM_32","STM32F103C8"}

@router.get("/export-design/{pid}")
def api_export_design(pid: str):
    f = PROJECTS_DIR / (pid + ".json")
    if not f.exists():
        raise HTTPException(404, "Not found")
    project = json.loads(f.read_text(encoding="utf-8"))
    slugs = list({c["slug"] for c in project.get("components", []) if c.get("slug")})
    library: dict[str, Any] = {}
    for slug in slugs:
        if slug in _BUILTIN_SLUGS:
            continue
        pp = _profile_path(slug)
        if not pp.exists():
            continue
        p = json.loads(pp.read_text(encoding="utf-8"))
        p.pop("raw_text", None)
        library[slug] = p
    fname = re.sub(r"[^a-zA-Z0-9_-]", "_", project.get("name", "design")) + ".schematic"
    bundle = {"format": "schematic-designer-v1",
              "exported_at": datetime.now(timezone.utc).isoformat(),
              "project": project, "library": library}
    return JSONResponse(content=bundle,
                        headers={"Content-Disposition": f'attachment; filename="{fname}"'})

@router.post("/import-design")
async def api_import_design(request: Request):
    bundle = await request.json()
    if bundle.get("format") != "schematic-designer-v1":
        raise HTTPException(400, "Unrecognised bundle format")
    installed, skipped = [], []
    for slug, profile in (bundle.get("library") or {}).items():
        pp = _profile_path(slug)
        if pp.exists():
            skipped.append(slug)
            continue
        (LIBRARY_DIR / slug).mkdir(parents=True, exist_ok=True)
        pp.write_text(json.dumps(profile, indent=2), encoding="utf-8")
        installed.append(slug)
    if installed:
        _update_index()
    import secrets
    p = bundle["project"]
    p["id"] = secrets.token_hex(6)
    p["imported_at"] = datetime.now(timezone.utc).isoformat()
    p["updated_at"] = datetime.now(timezone.utc).isoformat()
    (PROJECTS_DIR / (p["id"] + ".json")).write_text(json.dumps(p, indent=2), encoding="utf-8")
    return {"ok": True, "project_id": p["id"], "installed": installed, "skipped": skipped}

# ── PCB Boards ────────────────────────────────────────────────────────────────
@router.get("/pcb-boards")
def api_pcb_boards_list(projectId: str | None = None):
    boards = []
    for bf in PCB_BOARDS_DIR.glob("*.json"):
        try:
            d = json.loads(bf.read_text(encoding="utf-8"))
            if projectId and d.get("projectId") != projectId:
                continue
            boards.append({"id": d["id"], "title": d.get("title"), "updated_at": d.get("updated_at"),
                            "projectId": d.get("projectId"),
                            "component_count": len(d.get("components", []))})
        except Exception:
            pass
    boards.sort(key=lambda x: x.get("updated_at") or "", reverse=True)
    return boards

@router.post("/pcb-boards")
async def api_pcb_boards_create(request: Request):
    b = await request.json()
    if not b.get("id"):
        import secrets
        b["id"] = secrets.token_hex(6)
    b["updated_at"] = datetime.now(timezone.utc).isoformat()
    (PCB_BOARDS_DIR / (b["id"] + ".json")).write_text(json.dumps(b, indent=2), encoding="utf-8")
    return {"id": b["id"]}

@router.get("/pcb-boards/{bid}")
def api_pcb_boards_get(bid: str):
    f = PCB_BOARDS_DIR / (bid + ".json")
    if not f.exists():
        raise HTTPException(404, "Not found")
    return json.loads(f.read_text(encoding="utf-8"))

@router.delete("/pcb-boards/{bid}")
def api_pcb_boards_delete(bid: str):
    f = PCB_BOARDS_DIR / (bid + ".json")
    if f.exists():
        f.unlink()
    return {"ok": True}

# ── Issues ────────────────────────────────────────────────────────────────────
@router.get("/issues")
def api_issues_list():
    return _read_issues()

@router.post("/issues")
async def api_issues_create(request: Request):
    body = await request.json()
    data = _read_issues()
    now = datetime.now(timezone.utc).isoformat()
    issue = {"id": data["nextId"], "title": body.get("title", "Untitled"),
             "description": body.get("description", ""),
             "observations": body.get("observations", ""),
             "dismissed_because": body.get("dismissed_because", ""),
             "status": body.get("status", "backlog"),
             "priority": body.get("priority", "medium"),
             "created_at": now, "updated_at": now}
    data["nextId"] += 1
    data["issues"].append(issue)
    _save_issues(data)
    return issue

@router.put("/issues/{iid}")
async def api_issues_update(iid: int, request: Request):
    body = await request.json()
    data = _read_issues()
    idx = next((i for i, x in enumerate(data["issues"]) if x["id"] == iid), -1)
    if idx == -1:
        raise HTTPException(404, "Not found")
    data["issues"][idx] = {**data["issues"][idx], **body,
                            "id": iid, "updated_at": datetime.now(timezone.utc).isoformat()}
    _save_issues(data)
    return data["issues"][idx]

@router.delete("/issues/{iid}")
def api_issues_delete(iid: int):
    data = _read_issues()
    data["issues"] = [x for x in data["issues"] if x["id"] != iid]
    _save_issues(data)
    return {"ok": True}

@router.post("/issues/{iid}/lock")
async def api_issues_lock(iid: int, request: Request):
    body = await request.json()
    data = _read_issues()
    issue = next((x for x in data["issues"] if x["id"] == iid), None)
    if not issue:
        raise HTTPException(404, "Not found")
    if issue.get("locked"):
        raise HTTPException(409, f"Already locked by {issue.get('locked_by')}")
    issue["locked"] = True
    issue["locked_by"] = body.get("agent", "Unknown agent")
    issue["locked_at"] = datetime.now(timezone.utc).isoformat()
    issue["updated_at"] = issue["locked_at"]
    _save_issues(data)
    return {"ok": True, "locked_by": issue["locked_by"], "locked_at": issue["locked_at"]}

@router.post("/issues/{iid}/unlock")
async def api_issues_unlock(iid: int, request: Request):
    data = _read_issues()
    issue = next((x for x in data["issues"] if x["id"] == iid), None)
    if not issue:
        raise HTTPException(404, "Not found")
    issue.update({"locked": False, "locked_by": None, "locked_at": None,
                  "updated_at": datetime.now(timezone.utc).isoformat()})
    _save_issues(data)
    return {"ok": True}

# ── Gen Tickets ───────────────────────────────────────────────────────────────
@router.get("/gen-tickets")
def api_gen_tickets_list():
    return _read_gen_tickets()

@router.post("/gen-tickets")
async def api_gen_tickets_create(request: Request):
    body = await request.json()
    data = _read_gen_tickets()
    ticket = {"id": data["nextId"], "type": body.get("type", "footprint"),
              "slug": body.get("slug", ""), "title": body.get("title", "Untitled"),
              "prompt": body.get("prompt", ""), "status": body.get("status", "pending"),
              "created_at": datetime.now(timezone.utc).isoformat()}
    data["nextId"] += 1
    data["tickets"].append(ticket)
    _save_gen_tickets(data)
    return ticket

@router.put("/gen-tickets/{tid}")
async def api_gen_tickets_update(tid: int, request: Request):
    body = await request.json()
    data = _read_gen_tickets()
    idx = next((i for i, t in enumerate(data["tickets"]) if t["id"] == tid), -1)
    if idx == -1:
        raise HTTPException(404, "Not found")
    old_status = data["tickets"][idx].get("status")
    data["tickets"][idx] = {**data["tickets"][idx], **body, "id": tid}
    _save_gen_tickets(data)
    ticket = data["tickets"][idx]

    # Update active version when a ticket transitions to "done"
    if body.get("status") == "done" and old_status != "done" and ticket.get("slug"):
        slug = ticket["slug"]
        label = f"AI — GT-{tid:03d}"
        av_path = _active_version_path(slug)
        pp = _profile_path(slug)
        if pp.exists():
            # If an active version exists, update it in-place with the new AI content
            # (don't create a new version number — just stamp the existing snapshot with
            #  the AI label and the freshly-written profile data).
            active_id = None
            v_num = 1
            if av_path.exists():
                try:
                    av = json.loads(av_path.read_text(encoding="utf-8"))
                    active_id = av.get("id")
                    v_num = av.get("vNum", 1)
                except Exception:
                    pass
            if active_id:
                snap_path = _history_dir(slug) / (active_id + ".json")
                if snap_path.exists():
                    snap_path.write_text(json.dumps({
                        "saved_at": datetime.now(timezone.utc).isoformat(),
                        "label": label,
                        "profile": json.loads(pp.read_text(encoding="utf-8")),
                    }, indent=2), encoding="utf-8")
                    av_path.write_text(
                        json.dumps({"id": active_id, "label": label, "vNum": v_num}, indent=2),
                        encoding="utf-8")
                else:
                    # Snapshot file missing — create fresh
                    ts = _snapshot_profile(slug, label)
                    if ts:
                        h = _history_dir(slug)
                        files = sorted(h.glob("*.json"))
                        v_num = next((i + 1 for i, f in enumerate(files) if f.stem == ts), len(files))
                        av_path.write_text(
                            json.dumps({"id": ts, "label": label, "vNum": v_num}, indent=2),
                            encoding="utf-8")
            else:
                # No active version yet — create one
                ts = _snapshot_profile(slug, label)
                if ts:
                    h = _history_dir(slug)
                    files = sorted(h.glob("*.json"))
                    v_num = next((i + 1 for i, f in enumerate(files) if f.stem == ts), len(files))
                    av_path.write_text(
                        json.dumps({"id": ts, "label": label, "vNum": v_num}, indent=2),
                        encoding="utf-8")
            _broadcast("library_updated", {"slug": slug, "reason": "ticket_completed",
                                           "ticketId": tid, "label": label, "vNum": v_num})
            _broadcast("profile_updated", {"slug": slug, "reason": "ticket_completed",
                                           "label": label, "vNum": v_num})

    return ticket

@router.delete("/gen-tickets/{tid}")
def api_gen_tickets_delete(tid: int):
    data = _read_gen_tickets()
    data["tickets"] = [t for t in data["tickets"] if t["id"] != tid]
    _save_gen_tickets(data)
    return {"ok": True}

# ── Build from netlist ────────────────────────────────────────────────────────
def _nl_snap(v: float, grid: float = 20.0) -> float:
    return round(v / grid) * grid

def _nl_sym_type(slug: str, profile: dict | None) -> str:
    sl = slug.upper()
    if sl in ("VCC", "3V3", "5V", "+5V", "+3.3V"):
        return "vcc"
    if sl in ("GND", "AGND", "DGND", "PGND", "VSS"):
        return "gnd"
    if sl in ("RESISTOR", "RES"):
        return "resistor"
    if sl in ("CAPACITOR", "CAP"):
        return "capacitor"
    if sl in ("CAPACITOR_POL",):
        return "capacitor_pol"
    if sl in ("INDUCTOR", "IND"):
        return "inductor"
    if sl in ("DIODE",):
        return "diode"
    if sl in ("LED",):
        return "led"
    if profile:
        return profile.get("symbol_type", "ic")
    return "ic"

def _nl_ports(comp: dict, profile: dict | None) -> dict[str, dict]:
    """Return {pin_name: {x, y}} for a component."""
    pins = (profile or {}).get("pins", [])
    sym = comp.get("symType", "ic")
    cx, cy = comp.get("x", 400), comp.get("y", 400)

    if sym in ("vcc", "gnd"):
        return {"1": {"x": cx, "y": cy}}
    if sym in ("resistor", "capacitor", "capacitor_pol", "inductor"):
        return {"1": {"x": cx - 30, "y": cy}, "2": {"x": cx + 30, "y": cy}}
    if sym in ("diode", "led"):
        return {"A": {"x": cx - 30, "y": cy}, "K": {"x": cx + 30, "y": cy}}

    # IC: left side = odd pins, right side = even pins
    n = max(len(pins), 2)
    half = math.ceil(n / 2)
    result: dict[str, dict] = {}
    for i, pin in enumerate(pins):
        pname = str(pin.get("name") or pin.get("number") or i + 1)
        if i < half:
            result[pname] = {"x": cx - 60, "y": cy - (half - 1) * 20 + i * 40}
        else:
            j = i - half
            result[pname] = {"x": cx + 60, "y": cy - (half - 1) * 20 + j * 40}
    return result

def _nl_auto_place(comps: list[dict], nets: list[dict], profiles: dict) -> dict:
    """Simple grid auto-placement."""
    positions: dict[str, dict] = {}
    col, row = 0, 0
    W, H = 300, 200
    for c in comps:
        sym = _nl_sym_type(c["id"], profiles.get(c.get("slug", "")))
        c["symType"] = sym
        if sym == "vcc":
            positions[c["id"]] = {"x": 400 + col * W, "y": 80, "rotation": 0}
        elif sym == "gnd":
            positions[c["id"]] = {"x": 400 + col * W, "y": 600, "rotation": 0}
        else:
            positions[c["id"]] = {"x": 200 + col * W, "y": 200 + row * H, "rotation": 0}
            col += 1
            if col > 3:
                col = 0
                row += 1
    return positions

def _nl_resolve_pin(pin_ref: str, comp_map: dict, port_cache: dict) -> dict | None:
    parts = pin_ref.split(".")
    if len(parts) < 2:
        return None
    cid, pname = parts[0], ".".join(parts[1:])
    ports = port_cache.get(cid, {})
    return ports.get(pname) or ports.get(pname.upper()) or (list(ports.values())[0] if ports else None)

def _nl_route_net(pts: list[dict]) -> list[dict]:
    """Simple L-route wires for a net."""
    if len(pts) < 2:
        return []
    wires = []
    connected = [pts[0]]
    remaining = list(pts[1:])
    while remaining:
        best_i, best_d = 0, float("inf")
        for ri, r in enumerate(remaining):
            for c in connected:
                d = abs(r["x"] - c["x"]) + abs(r["y"] - c["y"])
                if d < best_d:
                    best_d, best_i = d, ri
        t = remaining.pop(best_i)
        c = connected[-1]
        # L-route: go horizontal then vertical
        mid = {"x": t["x"], "y": c["y"]}
        wires.append({"points": [{"x": c["x"], "y": c["y"]}, mid]})
        wires.append({"points": [mid, {"x": t["x"], "y": t["y"]}]})
        connected.append(t)
    return wires

@router.post("/build-from-netlist")
async def api_build_from_netlist(request: Request):
    import secrets
    body = await request.json()
    components = body.get("components", [])
    nets = body.get("nets", [])
    name = body.get("name", "Schematic")

    if not components:
        raise HTTPException(400, "components array required")
    if not nets:
        raise HTTPException(400, "nets array required")

    # Load profiles
    profiles: dict[str, dict] = {}
    for c in components:
        slug = c.get("slug", "")
        pp = _profile_path(slug)
        if pp.exists():
            try:
                profiles[slug] = json.loads(pp.read_text(encoding="utf-8"))
            except Exception:
                pass

    comps = [{**c,
              "symType": _nl_sym_type(c.get("slug", ""), profiles.get(c.get("slug", ""))),
              "designator": c.get("designator") or c["id"].upper(),
              "value": c.get("value") or c.get("slug", ""),
              "rotation": c.get("rotation", 0)} for c in components]

    positions = _nl_auto_place(comps, nets, profiles)
    for c in comps:
        c.update(positions.get(c["id"], {"x": 400, "y": 400, "rotation": 0}))

    comp_map = {c["id"]: c for c in comps}
    port_cache = {c["id"]: _nl_ports(c, profiles.get(c.get("slug", ""))) for c in comps}

    # Refine VCC/GND positions
    for c in comps:
        if c["symType"] not in ("vcc", "gnd"):
            continue
        is_vcc = c["symType"] == "vcc"
        my_nets = [n for n in nets if any(p.split(".")[0] == c["id"] for p in n.get("pins", []))]
        for net in my_nets:
            for pin_ref in net.get("pins", []):
                cid = pin_ref.split(".")[0]
                if cid == c["id"]:
                    continue
                resolved = _nl_resolve_pin(pin_ref, comp_map, port_cache)
                if resolved:
                    c["x"] = _nl_snap(resolved["x"])
                    c["y"] = _nl_snap(resolved["y"] - 80 if is_vcc else resolved["y"] + 80)
                    port_cache[c["id"]] = _nl_ports(c, profiles.get(c.get("slug", "")))
                    break

    # Route wires
    all_wires = []
    w_idx = 0
    for net in nets:
        pts = [_nl_resolve_pin(pr, comp_map, port_cache)
               for pr in net.get("pins", [])]
        pts = [p for p in pts if p]
        for w in _nl_route_net(pts):
            w_idx += 1
            all_wires.append({"id": f"w{w_idx}", "points": w["points"]})

    project = {
        "id": secrets.token_hex(6),
        "name": name,
        "components": [{k: v for k, v in c.items() if k != "symType"} for c in comps],
        "wires": all_wires,
        "labels": [],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    (PROJECTS_DIR / (project["id"] + ".json")).write_text(json.dumps(project, indent=2), encoding="utf-8")
    return {"id": project["id"], "project": project}

# ── Build project (AI agent) ──────────────────────────────────────────────────
_active_builds: set[int] = set()

def _spawn_build_agent(prompt_text: str, issue_id: int):
    if issue_id in _active_builds:
        return
    _active_builds.add(issue_id)

    # Find claude binary
    home = Path.home()
    candidates = [
        home / ".local" / "bin" / "claude",
        home / "AppData" / "Roaming" / "npm" / "claude.cmd",
        home / "AppData" / "Roaming" / "npm" / "claude",
        Path("claude"),
    ]
    claude_bin = next((str(c) for c in candidates if Path(str(c)).exists()), "claude")

    env = {**os.environ}
    env.pop("CLAUDECODE", None)

    def _run():
        try:
            proc = subprocess.Popen(
                [claude_bin, "--print", "--dangerously-skip-permissions"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                cwd=str(STATIC_DIR), env=env,
            )
            proc.stdin.write(prompt_text.encode())
            proc.stdin.close()
            _, stderr = proc.communicate(timeout=300)

            _active_builds.discard(issue_id)
            # Mark failed if agent didn't mark done itself
            try:
                data = _read_issues()
                iss = next((x for x in data["issues"] if x["id"] == issue_id), None)
                if iss and iss["status"] != "done":
                    iss["status"] = "cancelled"
                    iss["observations"] = stderr.decode()[-300:] if proc.returncode != 0 else "Agent completed."
                    iss["locked"] = False
                    _save_issues(data)
            except Exception:
                pass
        except Exception as e:
            _active_builds.discard(issue_id)
            try:
                data = _read_issues()
                iss = next((x for x in data["issues"] if x["id"] == issue_id), None)
                if iss and iss["status"] != "done":
                    iss["status"] = "cancelled"
                    iss["observations"] = f"Failed to spawn agent: {e}"
                    iss["locked"] = False
                    _save_issues(data)
            except Exception:
                pass

    import threading
    threading.Thread(target=_run, daemon=True).start()

@router.post("/build-project")
async def api_build_project(request: Request):
    body = await request.json()
    prompt = (body.get("prompt") or "").strip()
    if not prompt:
        raise HTTPException(400, "prompt required")

    # Read library for context
    lib_summary = ""
    try:
        idx = _read_index()
        user_parts = [v for v in idx.values() if not v.get("builtin") and v.get("status") != "pending_parse"]
        builtins = ["LM7805","AMS1117-3.3","NE555","LM358","L298N","DRV8833","ATMEGA328P",
                    "ESP32_WROOM_32","RESISTOR","CAPACITOR","INDUCTOR","DIODE","LED","BC547","IRF540N","VCC","GND"]
        lib_summary = "BUILTIN: " + ", ".join(builtins)
        if user_parts:
            lib_summary += "\nUSER LIBRARY: " + "; ".join(
                f"{p['slug']} ({p.get('part_number',p['slug'])} — {p.get('description','')[:60]})"
                for p in user_parts)
    except Exception:
        lib_summary = "Could not read library index."

    issues_data = _read_issues()
    now = datetime.now(timezone.utc).isoformat()
    issue = {"id": issues_data["nextId"], "title": f"AI Build: {prompt[:80]}",
             "description": prompt, "status": "backlog", "priority": "high",
             "category": "build", "locked": False, "created_at": now, "observations": ""}
    issues_data["nextId"] += 1
    issues_data["issues"].append(issue)
    _save_issues(issues_data)

    gen_data = _read_gen_tickets()
    gen_prompt = f"""AI PROJECT BUILDER REQUEST
Issue ID: {issue['id']}

USER PROMPT: {prompt}

AVAILABLE COMPONENTS:
{lib_summary}

INSTRUCTIONS:
Read PROJECT_BUILDER_GUIDE.md — it has the full workflow and pin reference syntax.

YOUR TASKS:
1. Register as an agent (id: "build-{issue['id']}-xxxx")
2. Lock issue {issue['id']} and set status to "inprogress"
3. Choose components and design the net connections
4. POST to http://localhost:8000/api/build-from-netlist
5. Update issue {issue['id']} observations with "PROJECT_ID:<the_id>" and status "done"
6. Unlock issue {issue['id']} and unregister agent"""

    gen_ticket = {"id": gen_data["nextId"], "type": "build-project", "slug": "",
                  "title": f"Build: {prompt[:60]}", "prompt": gen_prompt,
                  "status": "pending", "issue_id": issue["id"],
                  "created_at": now}
    gen_data["nextId"] += 1
    gen_data["tickets"].append(gen_ticket)
    _save_gen_tickets(gen_data)

    _spawn_build_agent(gen_prompt, issue["id"])
    return {"issueId": issue["id"], "genTicketId": gen_ticket["id"]}

# ── Generate Circuit ──────────────────────────────────────────────────────────
@router.post("/generate-circuit")
async def api_generate_circuit(request: Request):
    body = await request.json()
    prompt = (body.get("prompt") or "").strip()
    if not prompt:
        raise HTTPException(400, "prompt required")
    library_slugs: list[str] = body.get("library_slugs") or []

    # Look up profiles for requested slugs
    profiles: list[dict] = []
    for slug in library_slugs:
        try:
            p = _read_profile(slug)
            # Include a trimmed version without raw_text
            profiles.append({k: v for k, v in p.items() if k != "raw_text"})
        except Exception:
            pass

    # If no slugs specified, provide a summary of the full library
    if not profiles:
        try:
            idx = _read_index()
            profiles = list(idx.values())
        except Exception:
            pass

    profile_summary = json.dumps(profiles, indent=2)

    now = datetime.now(timezone.utc).isoformat()
    gen_prompt = f"""CIRCUIT GENERATOR REQUEST

USER DESCRIPTION:
{prompt}

AVAILABLE LIBRARY PROFILES:
{profile_summary}

INSTRUCTIONS:
You are a schematic layout generator. Create a complete circuit schematic based on the user description above.

1. Choose appropriate components from the library profiles provided.
2. Design the net connections between them (wires and net labels).
3. Create a new project by POSTing to http://localhost:8000/api/projects with Content-Type: application/json.

The project JSON must follow this format:
{{
  "name": "A descriptive circuit name",
  "components": [
    {{
      "id": "c1",
      "slug": "COMPONENT_SLUG",
      "symType": "ic",
      "designator": "U1",
      "value": "part number or value",
      "x": 200,
      "y": 200,
      "rotation": 0
    }}
  ],
  "wires": [
    {{
      "id": "w1",
      "points": [{{"x": 100, "y": 100}}, {{"x": 200, "y": 100}}]
    }}
  ],
  "labels": [
    {{
      "id": "l1",
      "name": "VCC",
      "x": 150,
      "y": 80,
      "rotation": 0
    }}
  ]
}}

LAYOUT RULES:
- Grid: 20 units. All x/y coordinates MUST be multiples of 20.
- Spread components 200-400 units apart horizontally, 150-300 units vertically.
- symType values: "ic", "resistor", "capacitor", "capacitor_pol", "inductor", "vcc", "gnd", "diode", "led", "npn", "nmos"
- For passives: use "resistor", "capacitor", etc. For ICs: use "ic". For power: use "vcc" or "gnd".
- Wires use polyline points — route through intermediate points for L-shapes.
- Use net labels for power rails (VCC, GND, 3V3) instead of wiring every component to the same rail.
- Place decoupling capacitors close (within 60 units) to their associated IC power pins.
- designator: R1, R2, C1, C2, U1, U2, etc. — unique per component.
- Only use slugs from the AVAILABLE LIBRARY PROFILES above.

After POSTing, the frontend will detect the new project and offer to open it.
Generate wiring that makes electrical sense for the described circuit.
Execute the POST request now."""

    gt = _read_gen_tickets()
    ticket = {
        "id": gt["nextId"],
        "type": "circuit",
        "slug": "",
        "title": f"Circuit: {prompt[:60]}",
        "prompt": gen_prompt,
        "status": "pending",
        "created_at": now,
    }
    gt["nextId"] += 1
    gt["tickets"].append(ticket)
    _save_gen_tickets(gt)
    return {"ticket_id": ticket["id"]}

# ── Export / Import library ───────────────────────────────────────────────────
@router.get("/export-library")
def api_export_library():
    bundle: dict[str, Any] = {}
    for slug_dir in LIBRARY_DIR.iterdir():
        if slug_dir.name.startswith("_") or not slug_dir.is_dir():
            continue
        pp = slug_dir / "profile.json"
        if not pp.exists():
            continue
        try:
            p = json.loads(pp.read_text(encoding="utf-8"))
            p.pop("raw_text", None)
            bundle[slug_dir.name] = p
        except Exception:
            pass
    return JSONResponse(
        content={"format": "schematic-library-v1",
                 "exported_at": datetime.now(timezone.utc).isoformat(),
                 "components": bundle},
        headers={"Content-Disposition": 'attachment; filename="library.json"'})

@router.post("/import-library")
async def api_import_library(request: Request):
    body = await request.json()
    if body.get("format") != "schematic-library-v1":
        raise HTTPException(400, "Unrecognised format — expected schematic-library-v1")
    installed, skipped = [], []
    for slug, profile in (body.get("components") or {}).items():
        pp = _profile_path(slug)
        if pp.exists():
            skipped.append(slug)
            continue
        (LIBRARY_DIR / slug).mkdir(parents=True, exist_ok=True)
        pp.write_text(json.dumps(profile, indent=2), encoding="utf-8")
        installed.append(slug)
    if installed:
        _update_index()
    return {"ok": True, "installed": installed, "skipped": skipped}

# ── Eagle .lbr import ─────────────────────────────────────────────────────────
import math
import xml.etree.ElementTree as ET

_EAGLE_PIN_DIRECTION_MAP = {
    "pas": "passive", "pwr": "power", "in": "input", "out": "output",
    "io": "bidirectional", "oc": "output", "hiz": "output", "sup": "power",
    "nc": "nc",
}
_EAGLE_PAD_SHAPE_MAP = {
    "square": "rect", "round": "circle", "octagon": "circle", "long": "rect",
}

def _eagle_mm(val_str: str) -> float:
    """Parse Eagle coordinate/dimension string to mm (Eagle .lbr uses mm natively)."""
    try:
        return round(float(val_str), 4)
    except (ValueError, TypeError):
        return 0.0

def _eagle_rot(rot_str: str | None) -> float:
    """Parse Eagle rotation string like 'R90', 'R180', 'MR270' → degrees."""
    if not rot_str:
        return 0.0
    s = rot_str.replace("M", "").replace("S", "")  # strip mirror/spin prefixes
    if s.startswith("R"):
        try:
            return float(s[1:])
        except ValueError:
            return 0.0
    return 0.0

def _parse_eagle_lbr(xml_bytes: bytes) -> tuple[list[dict], list[dict]]:
    """Parse an Eagle .lbr file. Returns (profiles, footprints)."""
    root = ET.fromstring(xml_bytes)

    # ── Collect packages (footprints) ──
    packages: dict[str, dict] = {}
    for pkg_el in root.iter("package"):
        name = pkg_el.get("name", "")
        if not name:
            continue
        pads = []
        for smd in pkg_el.iter("smd"):
            sx = _eagle_mm(smd.get("x", "0"))
            sy = _eagle_mm(smd.get("y", "0"))
            dx = _eagle_mm(smd.get("dx", "0"))
            dy = _eagle_mm(smd.get("dy", "0"))
            angle = _eagle_rot(smd.get("rot"))
            # Apply rotation to position and swap dimensions for 90/270°
            rad = math.radians(angle)
            cos_a, sin_a = round(math.cos(rad), 6), round(math.sin(rad), 6)
            rx = sx * cos_a - sy * sin_a
            ry = sx * sin_a + sy * cos_a
            # Swap dx/dy when rotated ~90° or ~270°
            if abs(angle % 360 - 90) < 1 or abs(angle % 360 - 270) < 1:
                dx, dy = dy, dx
            pads.append({
                "number": smd.get("name", ""),
                "x": round(rx, 4),
                "y": -round(ry, 4),  # flip Y
                "type": "smd",
                "shape": "rect",
                "size_x": dx,
                "size_y": dy,
            })
        for pad in pkg_el.iter("pad"):
            px = _eagle_mm(pad.get("x", "0"))
            py = _eagle_mm(pad.get("y", "0"))
            angle = _eagle_rot(pad.get("rot"))
            rad = math.radians(angle)
            cos_a, sin_a = round(math.cos(rad), 6), round(math.sin(rad), 6)
            rpx = px * cos_a - py * sin_a
            rpy = px * sin_a + py * cos_a
            drill = _eagle_mm(pad.get("drill", "0"))
            shape = _EAGLE_PAD_SHAPE_MAP.get(pad.get("shape", "round"), "circle")
            diameter = _eagle_mm(pad.get("diameter", "0")) or round(drill * 1.8, 4)
            pads.append({
                "number": pad.get("name", ""),
                "x": round(rpx, 4),
                "y": -round(rpy, 4),
                "type": "thru_hole",
                "shape": shape,
                "size_x": diameter,
                "size_y": diameter,
                "drill": drill,
            })
        # Compute courtyard from pad extents
        if pads:
            min_x = min(p["x"] - p.get("size_x", 0)/2 for p in pads)
            max_x = max(p["x"] + p.get("size_x", 0)/2 for p in pads)
            min_y = min(p["y"] - p.get("size_y", 0)/2 for p in pads)
            max_y = max(p["y"] + p.get("size_y", 0)/2 for p in pads)
            margin = 0.25
            courtyard = {
                "x": round(min_x - margin, 4),
                "y": round(min_y - margin, 4),
                "w": round(max_x - min_x + 2 * margin, 4),
                "h": round(max_y - min_y + 2 * margin, 4),
            }
        else:
            courtyard = {"x": 0, "y": 0, "w": 1, "h": 1}
        desc_el = pkg_el.find("description")
        packages[name] = {
            "name": name,
            "description": (desc_el.text or "").strip() if desc_el is not None else "",
            "pads": pads,
            "courtyard": courtyard,
        }

    # ── Collect symbols ──
    symbols: dict[str, list[dict]] = {}
    for sym_el in root.iter("symbol"):
        name = sym_el.get("name", "")
        if not name:
            continue
        pins = []
        for pin in sym_el.iter("pin"):
            direction = pin.get("direction", "io")
            pins.append({
                "name": pin.get("name", ""),
                "type": _EAGLE_PIN_DIRECTION_MAP.get(direction, "passive"),
            })
        symbols[name] = pins

    # ── Collect devicesets → create profiles ──
    profiles = []
    footprints_out = []
    for ds in root.iter("deviceset"):
        ds_name = ds.get("name", "")
        if not ds_name:
            continue
        desc_el = ds.find("description")
        description = (desc_el.text or "").strip() if desc_el is not None else ""
        # Remove HTML tags from description
        description = re.sub(r"<[^>]+>", "", description).strip()

        # Collect pins from gates → symbols
        all_pins = []
        pin_number = 1
        for gate in ds.iter("gate"):
            sym_name = gate.get("symbol", "")
            for p in symbols.get(sym_name, []):
                all_pins.append({
                    "number": pin_number,
                    "name": p["name"],
                    "type": p["type"],
                })
                pin_number += 1

        # Collect package variants
        package_names = []
        pin_maps: dict[str, dict[str, str]] = {}  # package_name → {gate_pin_name: pad_number}
        for dev in ds.iter("device"):
            pkg_name = dev.get("package", "")
            if pkg_name:
                package_names.append(pkg_name)
                # Parse connect elements for pin↔pad mapping
                pm = {}
                for conn in dev.iter("connect"):
                    pm[conn.get("pin", "")] = conn.get("pad", "")
                pin_maps[pkg_name] = pm

        # Apply pin numbers from first package mapping
        if package_names and pin_maps.get(package_names[0]):
            pm = pin_maps[package_names[0]]
            for pin in all_pins:
                pad = pm.get(pin["name"], "")
                if pad:
                    try:
                        pin["number"] = int(pad)
                    except ValueError:
                        pass  # keep sequential number

        # Determine symbol_type from pin types
        pin_types = {p["type"] for p in all_pins}
        if len(all_pins) <= 2 and "passive" in pin_types:
            sym_type = "resistor" if len(all_pins) == 2 else "passive"
        elif "nc" == pin_types or len(all_pins) == 0:
            sym_type = "ic"
        else:
            sym_type = "ic"

        profile = {
            "part_number": ds_name,
            "description": description,
            "manufacturer": "",
            "status": "parsed",
            "confidence": "MEDIUM",
            "symbol_type": sym_type,
            "package_types": package_names,
            "pins": all_pins,
            "source": "eagle_lbr",
        }
        profiles.append(profile)

        # Save footprints for each package
        for pkg_name in package_names:
            if pkg_name in packages:
                footprints_out.append(packages[pkg_name])

    return profiles, footprints_out


@router.post("/import-eagle")
async def api_import_eagle(file: UploadFile):
    if not file.filename or not file.filename.lower().endswith(".lbr"):
        raise HTTPException(400, "Eagle .lbr file required")

    xml_bytes = await file.read()
    try:
        profiles, footprints = _parse_eagle_lbr(xml_bytes)
    except ET.ParseError as e:
        raise HTTPException(400, f"Invalid XML in .lbr file: {e}")

    installed, skipped = [], []

    # Save footprints
    for fp in footprints:
        fp_path = FOOTPRINTS_DIR / f"{fp['name']}.json"
        if not fp_path.exists():
            fp_path.write_text(json.dumps(fp, indent=2), encoding="utf-8")

    # Save component profiles
    for profile in profiles:
        slug = re.sub(r"[^a-zA-Z0-9\-_]", "_", profile["part_number"]).upper()
        pp = _profile_path(slug)
        if pp.exists():
            skipped.append(slug)
            continue
        (LIBRARY_DIR / slug).mkdir(parents=True, exist_ok=True)
        profile["slug"] = slug
        # Auto-assign first package as footprint
        if profile.get("package_types") and not profile.get("footprint"):
            profile["footprint"] = profile["package_types"][0]
        pp.write_text(json.dumps(profile, indent=2), encoding="utf-8")
        installed.append(slug)

    if installed:
        _update_index()

    return {"ok": True, "installed": installed, "skipped": skipped,
            "footprints_added": len(footprints)}

# ── Agents ────────────────────────────────────────────────────────────────────
@router.get("/agents")
def api_agents_list():
    _prune_stale()
    return sorted(_agents.values(), key=lambda a: a.get("started_at", ""), reverse=True)

@router.post("/agents/register")
async def api_agents_register(request: Request):
    body = await request.json()
    if not body.get("id") or not body.get("name"):
        raise HTTPException(400, "id and name required")
    now = datetime.now(timezone.utc).isoformat()
    agent = {"id": body["id"], "name": body["name"], "task": body.get("task", ""),
             "ticket_id": body.get("ticket_id"), "worktree": body.get("worktree"),
             "model": body.get("model"), "status": "working",
             "started_at": now, "last_ping": now, "observations": "", "steps": []}
    _agents[body["id"]] = agent
    _broadcast("agent_registered", {"id": body["id"], "name": body["name"], "task": body.get("task")})
    return {"ok": True, "agent": agent}

@router.post("/agents/{aid}/ping")
async def api_agents_ping(aid: str, request: Request):
    agent = _agents.get(aid)
    if not agent:
        raise HTTPException(404, "Agent not registered")
    body = await request.json()
    agent["last_ping"] = datetime.now(timezone.utc).isoformat()
    if body.get("observations") is not None:
        agent["observations"] = body["observations"]
    if body.get("step"):
        agent["steps"].append({"t": agent["last_ping"], "msg": body["step"]})
    if body.get("status"):
        agent["status"] = body["status"]
    _broadcast("agent_ping", {"id": aid, "status": agent["status"], "observations": agent["observations"]})
    return {"ok": True}

@router.delete("/agents/{aid}")
async def api_agents_delete(aid: str, request: Request):
    agent = _agents.get(aid)
    if not agent:
        return {"ok": True}
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    agent["status"] = body.get("status", "done")
    if body.get("observations"):
        agent["observations"] = body["observations"]
    agent["last_ping"] = datetime.now(timezone.utc).isoformat()
    _broadcast("agent_done", {"id": aid, "status": agent["status"]})
    # Remove after 10 min
    async def _cleanup():
        await asyncio.sleep(600)
        _agents.pop(aid, None)
    asyncio.create_task(_cleanup())
    return {"ok": True}

# ── SSE events ────────────────────────────────────────────────────────────────
@router.get("/events")
async def api_events(request: Request):
    q: asyncio.Queue = asyncio.Queue(maxsize=100)
    _sse_queues.append(q)

    async def gen():
        try:
            # Send keepalive immediately
            yield ": keepalive\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=15.0)
                    yield msg
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            _sse_queues.remove(q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

# ── Footprint shape generators (ported from server.js) ───────────────────────
def _generate_footprint_rules(pkg: str, pins: list, part_name: str) -> dict | None:
    p = (pkg or "").upper().strip()
    n = len(pins) or 2

    m = re.match(r"SOIC-?(\d+)|SOP-?(\d+)|^SO-?(\d+)", p)
    if m:
        cnt = int(m.group(1) or m.group(2) or m.group(3)) or n
        return _make_dual_row_smd(cnt, pins, 1.27, 5.08, 0.65, 1.6, part_name)

    m = re.match(r"TSSOP-?(\d+)|SSOP-?(\d+)", p)
    if m:
        cnt = int(m.group(1) or m.group(2)) or n
        return _make_dual_row_smd(cnt, pins, 0.65, 4.4, 0.35, 1.5, part_name)

    m = re.match(r"DIP-?(\d+)", p)
    if m:
        cnt = int(m.group(1)) or n
        row = 15.24 if cnt > 16 else 7.62
        return _make_dual_row_tht(cnt, pins, 2.54, row, part_name)

    m = re.match(r"PDIP-?(\d+)", p)
    if m:
        cnt = int(m.group(1)) or n
        return _make_dual_row_tht(cnt, pins, 2.54, 7.62, part_name)

    m = re.match(r"(?:T|L)?QFP-?(\d+)", p)
    if m:
        cnt = int(m.group(1)) or n
        pitch = 0.4 if "0.4" in p else 0.5 if "0.5" in p else 0.8
        return _make_quad_flat_smd(cnt, pins, pitch, None, part_name)

    m = re.match(r"(?:QFN|DFN|WSON|LFCSP)-?(\d+)", p)
    if m:
        cnt = int(m.group(1)) or n
        bm = re.search(r"(\d+(?:\.\d+)?)X\d", p)
        body = float(bm.group(1)) if bm else max(3.0, math.ceil(cnt / 4) * 0.65 + 1.5)
        return _make_quad_flat_smd(cnt, pins, None, body, part_name)

    if re.match(r"SOT-?23-?3?$", p) or (re.match(r"SOT-?23", p) and n <= 3):
        return _make_sot23(pins, part_name)
    if re.match(r"SOT-?23-?5", p):
        return _make_sot235(pins, part_name)
    if re.match(r"SOT-?223", p):
        return _make_sot223(pins, part_name)
    if re.match(r"SOT-?89", p):
        return _make_sot89(pins, part_name)
    if re.match(r"TO-?92", p):
        return _make_to92(pins, part_name)
    if re.match(r"TO-?220", p):
        return _make_to220(pins, part_name)
    if re.match(r"SOD-?123", p):
        return _make_sod123(pins, part_name)
    if re.match(r"SMA|DO-?214AC", p):
        return _make_sma(pins, part_name)
    if "0402" in p:
        return _make_passive("0402", pins, 1.0, 0.6, 0.6, part_name)
    if "0603" in p:
        return _make_passive("0603", pins, 1.6, 1.0, 0.8, part_name)
    if "0805" in p:
        return _make_passive("0805", pins, 2.0, 1.25, 1.0, part_name)
    if "1206" in p:
        return _make_passive("1206", pins, 3.2, 1.75, 1.2, part_name)
    if re.match(r"LED.*5MM|5MM.*LED|T-?1", p):
        return _make_led5mm(pins, part_name)
    if re.match(r"CP.*5MM|5MM.*CAP|RADIAL|ELEC", p):
        return _make_cap_pol5mm(pins, part_name)
    return None

def _make_dual_row_smd(cnt, pins, pitch, row_spacing, pad_w, pad_h, part_name):
    half = cnt // 2
    total_h = (half - 1) * pitch
    pads = []
    for i in range(half):
        y = -total_h / 2 + i * pitch
        pads.append({"number": str(i + 1), "x": -row_spacing / 2, "y": y,
                     "type": "smd", "shape": "rect", "size_x": pad_h, "size_y": pad_w})
    for i in range(cnt - half):
        y = total_h / 2 - i * pitch
        pads.append({"number": str(half + i + 1), "x": row_spacing / 2, "y": y,
                     "type": "smd", "shape": "rect", "size_x": pad_h, "size_y": pad_w})
    cy_w, cy_h = row_spacing + pad_h + 0.5, total_h + pad_w + 1.0
    return {"name": f"{part_name}_SOIC-{cnt}", "description": f"{cnt}-pin dual-row SMD",
            "pin_count": cnt, "pitch": pitch, "row_spacing": row_spacing, "pads": pads,
            "courtyard": {"x": -cy_w / 2, "y": -cy_h / 2, "w": cy_w, "h": cy_h}}

def _make_dual_row_tht(cnt, pins, pitch, row_spacing, part_name):
    half = cnt // 2
    total_h = (half - 1) * pitch
    pads = []
    for i in range(half):
        pads.append({"number": str(i + 1), "x": -row_spacing / 2, "y": -total_h / 2 + i * pitch,
                     "type": "thru_hole", "shape": "rect" if i == 0 else "circle",
                     "size_x": 1.6, "size_y": 1.6, "drill": 0.8})
    for i in range(cnt - half):
        pads.append({"number": str(half + i + 1), "x": row_spacing / 2, "y": total_h / 2 - i * pitch,
                     "type": "thru_hole", "shape": "circle", "size_x": 1.6, "size_y": 1.6, "drill": 0.8})
    cy_w, cy_h = row_spacing + 2.5, total_h + 3.0
    return {"name": f"{part_name}_DIP-{cnt}", "description": f"DIP-{cnt} through-hole",
            "pin_count": cnt, "pitch": pitch, "row_spacing": row_spacing, "pads": pads,
            "courtyard": {"x": -cy_w / 2, "y": -cy_h / 2, "w": cy_w, "h": cy_h}}

def _make_quad_flat_smd(cnt, pins, pitch, body_mm, part_name):
    per_side = cnt // 4
    rem = cnt % 4
    sides = [per_side + (1 if rem > i else 0) for i in range(4)]
    is_qfn = body_mm is not None
    p = pitch or 0.5
    if is_qfn:
        body, pad_w, pad_h = body_mm, 0.3, 1.0
        pad_offset = body / 2 + pad_h / 2 - 0.1
    else:
        body = sides[0] * p + 1.0
        pad_w, pad_h = p * 0.55, 1.5
        pad_offset = body / 2 + pad_h / 2 + 0.3
    pads, pad_num = [], 1
    for i in range(sides[0]):
        y = (sides[0] - 1) / 2 * p - i * p
        pads.append({"number": str(pad_num), "x": -pad_offset, "y": y,
                     "type": "smd", "shape": "rect", "size_x": pad_h, "size_y": pad_w})
        pad_num += 1
    for i in range(sides[1]):
        x = -(sides[1] - 1) / 2 * p + i * p
        pads.append({"number": str(pad_num), "x": x, "y": -pad_offset,
                     "type": "smd", "shape": "rect", "size_x": pad_w, "size_y": pad_h})
        pad_num += 1
    for i in range(sides[2]):
        y = -(sides[2] - 1) / 2 * p + i * p
        pads.append({"number": str(pad_num), "x": pad_offset, "y": y,
                     "type": "smd", "shape": "rect", "size_x": pad_h, "size_y": pad_w})
        pad_num += 1
    for i in range(sides[3]):
        x = (sides[3] - 1) / 2 * p - i * p
        pads.append({"number": str(pad_num), "x": x, "y": pad_offset,
                     "type": "smd", "shape": "rect", "size_x": pad_w, "size_y": pad_h})
        pad_num += 1
    if is_qfn:
        ep = body * 0.65
        pads.append({"number": str(pad_num), "x": 0, "y": 0,
                     "type": "smd", "shape": "rect", "size_x": ep, "size_y": ep})
    total = pad_offset * 2 + pad_h + 0.5
    tag = "QFN" if is_qfn else "TQFP"
    return {"name": f"{part_name}_{tag}-{cnt}", "description": f"{cnt}-pin {tag}",
            "pin_count": cnt + (1 if is_qfn else 0), "pitch": p, "pads": pads,
            "courtyard": {"x": -total / 2, "y": -total / 2, "w": total, "h": total}}

def _make_sot23(pins, n): return {"name": f"{n}_SOT-23","description":"SOT-23 3-pin SMD","pin_count":3,"pads":[{"number":"1","x":-0.95,"y":1.0,"type":"smd","shape":"rect","size_x":0.6,"size_y":0.9},{"number":"2","x":0.95,"y":1.0,"type":"smd","shape":"rect","size_x":0.6,"size_y":0.9},{"number":"3","x":0.0,"y":-1.0,"type":"smd","shape":"rect","size_x":0.6,"size_y":0.9}],"courtyard":{"x":-1.6,"y":-1.7,"w":3.2,"h":3.4}}
def _make_sot235(pins, n): return {"name": f"{n}_SOT-23-5","description":"SOT-23-5 5-pin SMD","pin_count":5,"pads":[{"number":"1","x":-1.5,"y":1.3,"type":"smd","shape":"rect","size_x":0.6,"size_y":0.7},{"number":"2","x":-0.5,"y":1.3,"type":"smd","shape":"rect","size_x":0.6,"size_y":0.7},{"number":"3","x":0.5,"y":1.3,"type":"smd","shape":"rect","size_x":0.6,"size_y":0.7},{"number":"4","x":0.95,"y":-1.3,"type":"smd","shape":"rect","size_x":0.6,"size_y":0.9},{"number":"5","x":-0.95,"y":-1.3,"type":"smd","shape":"rect","size_x":0.6,"size_y":0.9}],"courtyard":{"x":-1.8,"y":-2.0,"w":3.6,"h":4.0}}
def _make_sot223(pins, n): return {"name": f"{n}_SOT-223","description":"SOT-223 4-pin SMD","pin_count":4,"pads":[{"number":"1","x":-2.3,"y":1.6,"type":"smd","shape":"rect","size_x":0.7,"size_y":1.5},{"number":"2","x":0.0,"y":1.6,"type":"smd","shape":"rect","size_x":0.7,"size_y":1.5},{"number":"3","x":2.3,"y":1.6,"type":"smd","shape":"rect","size_x":0.7,"size_y":1.5},{"number":"4","x":0.0,"y":-1.6,"type":"smd","shape":"rect","size_x":3.5,"size_y":2.2}],"courtyard":{"x":-3.5,"y":-2.9,"w":7.0,"h":5.8}}
def _make_sot89(pins, n): return {"name": f"{n}_SOT-89","description":"SOT-89 3-pin SMD","pin_count":3,"pads":[{"number":"1","x":-1.5,"y":1.5,"type":"smd","shape":"rect","size_x":0.7,"size_y":1.3},{"number":"2","x":0.0,"y":-1.0,"type":"smd","shape":"rect","size_x":1.5,"size_y":2.5},{"number":"3","x":1.5,"y":1.5,"type":"smd","shape":"rect","size_x":0.7,"size_y":1.3}],"courtyard":{"x":-2.5,"y":-2.5,"w":5.0,"h":5.0}}
def _make_to92(pins, n): return {"name": f"{n}_TO-92","description":"TO-92 3-pin THT","pin_count":3,"pads":[{"number":"1","x":-1.27,"y":0,"type":"thru_hole","shape":"rect","size_x":1.6,"size_y":1.6,"drill":0.8},{"number":"2","x":0.0,"y":0,"type":"thru_hole","shape":"circle","size_x":1.6,"size_y":1.6,"drill":0.8},{"number":"3","x":1.27,"y":0,"type":"thru_hole","shape":"circle","size_x":1.6,"size_y":1.6,"drill":0.8}],"courtyard":{"x":-2.5,"y":-2.5,"w":5.0,"h":5.0}}
def _make_to220(pins, n): return {"name": f"{n}_TO-220","description":"TO-220 3-pin THT","pin_count":3,"pads":[{"number":"1","x":-2.54,"y":0,"type":"thru_hole","shape":"rect","size_x":1.8,"size_y":1.8,"drill":1.0},{"number":"2","x":0.0,"y":0,"type":"thru_hole","shape":"circle","size_x":1.8,"size_y":1.8,"drill":1.0},{"number":"3","x":2.54,"y":0,"type":"thru_hole","shape":"circle","size_x":1.8,"size_y":1.8,"drill":1.0}],"courtyard":{"x":-4.0,"y":-6.5,"w":8.0,"h":10.0}}
def _make_sod123(pins, n): return {"name": f"{n}_SOD-123","description":"SOD-123 2-pin SMD","pin_count":2,"pads":[{"number":"1","x":-1.6,"y":0,"type":"smd","shape":"rect","size_x":1.1,"size_y":1.1},{"number":"2","x":1.6,"y":0,"type":"smd","shape":"rect","size_x":1.1,"size_y":1.1}],"courtyard":{"x":-2.5,"y":-1.0,"w":5.0,"h":2.0}}
def _make_sma(pins, n): return {"name": f"{n}_SMA","description":"SMA / DO-214AC 2-pin SMD","pin_count":2,"pads":[{"number":"1","x":-2.0,"y":0,"type":"smd","shape":"rect","size_x":1.5,"size_y":2.3},{"number":"2","x":2.0,"y":0,"type":"smd","shape":"rect","size_x":1.5,"size_y":2.3}],"courtyard":{"x":-3.2,"y":-1.5,"w":6.4,"h":3.0}}
def _make_passive(pkg, pins, spacing, pw, ph, n): return {"name": f"{n}_{pkg}","description":f"{pkg} 2-pad SMD passive","pin_count":2,"pads":[{"number":"1","x":-spacing/2,"y":0,"type":"smd","shape":"rect","size_x":pw,"size_y":ph},{"number":"2","x":spacing/2,"y":0,"type":"smd","shape":"rect","size_x":pw,"size_y":ph}],"courtyard":{"x":-(spacing/2+pw/2+0.2),"y":-(ph/2+0.2),"w":spacing+pw+0.4,"h":ph+0.4}}
def _make_led5mm(pins, n): return {"name": f"{n}_LED-5mm","description":"LED 5mm THT","pin_count":2,"pads":[{"number":"1","x":-1.27,"y":0,"type":"thru_hole","shape":"rect","size_x":1.8,"size_y":1.8,"drill":0.8},{"number":"2","x":1.27,"y":0,"type":"thru_hole","shape":"circle","size_x":1.8,"size_y":1.8,"drill":0.8}],"courtyard":{"x":-3.5,"y":-3.5,"w":7.0,"h":7.0}}
def _make_cap_pol5mm(pins, n): return {"name": f"{n}_CAP-POL-5mm","description":"Polarized cap radial 5mm","pin_count":2,"pads":[{"number":"1","x":-2.5,"y":0,"type":"thru_hole","shape":"rect","size_x":1.8,"size_y":1.8,"drill":0.8},{"number":"2","x":2.5,"y":0,"type":"thru_hole","shape":"circle","size_x":1.8,"size_y":1.8,"drill":0.8}],"courtyard":{"x":-4.5,"y":-4.5,"w":9.0,"h":9.0}}

# ── Version history helpers ───────────────────────────────────────────────────
def _history_dir(slug: str) -> Path:
    return LIBRARY_DIR / slug / "history"

def _active_version_path(slug: str) -> Path:
    return LIBRARY_DIR / slug / "active_version.json"

def _clear_active_version(slug: str):
    try:
        _active_version_path(slug).unlink()
    except FileNotFoundError:
        pass

def _stable_str(obj) -> str:
    if obj is None or not isinstance(obj, (dict, list)):
        return json.dumps(obj)
    if isinstance(obj, list):
        return "[" + ",".join(_stable_str(i) for i in obj) + "]"
    return "{" + ",".join(json.dumps(k) + ":" + _stable_str(obj[k]) for k in sorted(obj)) + "}"

def _profile_already_saved(slug: str, profile: dict) -> bool:
    h = _history_dir(slug)
    if not h.exists():
        return False
    needle = _stable_str(profile)
    for f in h.glob("*.json"):
        try:
            snap = json.loads(f.read_text(encoding="utf-8"))
            if _stable_str(snap.get("profile")) == needle:
                return True
        except Exception:
            pass
    return False

def _snapshot_profile(slug: str, label: str = "") -> str | None:
    pp = _profile_path(slug)
    if not pp.exists():
        return None
    h = _history_dir(slug)
    h.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    ts = now.isoformat().replace(":", "-").replace(".", "-")
    snap = {"saved_at": now.isoformat(), "label": label,
            "profile": json.loads(pp.read_text(encoding="utf-8"))}
    (h / (ts + ".json")).write_text(json.dumps(snap, indent=2), encoding="utf-8")
    # Keep last 20 snapshots
    files = sorted(h.glob("*.json"))
    for old in files[:-20]:
        try:
            old.unlink()
        except Exception:
            pass
    return ts


# ── History API ───────────────────────────────────────────────────────────────
@router.get("/library/{slug}/history")
def api_history_list(slug: str):
    h = _history_dir(slug)
    if not h.exists():
        return []
    result = []
    for f in sorted(h.glob("*.json"), reverse=True):
        try:
            snap = json.loads(f.read_text(encoding="utf-8"))
            result.append({"id": f.stem, "saved_at": snap["saved_at"], "label": snap.get("label", "")})
        except Exception:
            pass
    return result

@router.post("/library/{slug}/history/save")
async def api_history_save(slug: str, request: Request):
    body = await request.json()
    ts = _snapshot_profile(slug, body.get("label", ""))
    if not ts:
        raise HTTPException(404, "Component not found")
    return {"ok": True, "id": ts}

@router.post("/library/{slug}/history/{hid}/activate")
def api_history_activate(slug: str, hid: str):
    snap_path = _history_dir(slug) / (hid + ".json")
    if not snap_path.exists():
        raise HTTPException(404, "Not found")
    snap = json.loads(snap_path.read_text(encoding="utf-8"))
    if not snap.get("profile"):
        raise HTTPException(400, "Invalid snapshot")
    h = _history_dir(slug)
    files = sorted(h.glob("*.json"))
    v_num = next((i + 1 for i, f in enumerate(files) if f.stem == hid), 0)
    _write_profile(slug, snap["profile"])
    _active_version_path(slug).write_text(
        json.dumps({"id": hid, "label": snap.get("label", ""), "vNum": v_num}, indent=2),
        encoding="utf-8")
    _broadcast("library_updated", {"slug": slug, "reason": "version_activated"})
    return {"ok": True, "label": snap.get("label", ""), "vNum": v_num}

@router.post("/library/{slug}/history/{hid}/set-active")
def api_history_set_active(slug: str, hid: str):
    """Mark a snapshot as the active version WITHOUT overwriting profile.json.
    Use this after creating a new snapshot from the current profile — no data loss risk."""
    snap_path = _history_dir(slug) / (hid + ".json")
    if not snap_path.exists():
        raise HTTPException(404, "Not found")
    snap = json.loads(snap_path.read_text(encoding="utf-8"))
    h = _history_dir(slug)
    files = sorted(h.glob("*.json"))
    v_num = next((i + 1 for i, f in enumerate(files) if f.stem == hid), 0)
    _active_version_path(slug).write_text(
        json.dumps({"id": hid, "label": snap.get("label", ""), "vNum": v_num}, indent=2),
        encoding="utf-8")
    return {"ok": True, "label": snap.get("label", ""), "vNum": v_num}

@router.get("/library/{slug}/active_version")
def api_active_version(slug: str):
    avp = _active_version_path(slug)
    if not avp.exists():
        return None
    try:
        return json.loads(avp.read_text(encoding="utf-8"))
    except Exception:
        return None

@router.put("/library/{slug}/history/{hid}")
def api_history_update(slug: str, hid: str):
    """Overwrite an existing snapshot in place with the current profile.json.
    active_version continues pointing at the same hid."""
    snap_path = _history_dir(slug) / (hid + ".json")
    if not snap_path.exists():
        raise HTTPException(404, "Not found")
    pp = _profile_path(slug)
    if not pp.exists():
        raise HTTPException(404, "Profile not found")
    old_snap = json.loads(snap_path.read_text(encoding="utf-8"))
    old_label = old_snap.get("label", "")
    now = datetime.now(timezone.utc)
    snap_path.write_text(json.dumps({
        "saved_at": now.isoformat(),
        "label": old_label,
        "profile": json.loads(pp.read_text(encoding="utf-8")),
    }, indent=2), encoding="utf-8")
    return {"ok": True, "id": hid}

@router.get("/library/{slug}/history/{hid}")
def api_history_get(slug: str, hid: str):
    """Return a single history snapshot (including its full profile)."""
    snap_path = _history_dir(slug) / (hid + ".json")
    if not snap_path.exists():
        raise HTTPException(404, "Not found")
    snap = json.loads(snap_path.read_text(encoding="utf-8"))
    return {"id": hid, "saved_at": snap.get("saved_at"), "label": snap.get("label", ""), "profile": snap.get("profile")}

@router.delete("/library/{slug}/history/{hid}")
def api_history_delete(slug: str, hid: str):
    snap_path = _history_dir(slug) / (hid + ".json")
    if snap_path.exists():
        snap_path.unlink()
    # If this was the active version, clear the pointer so Save creates a fresh one
    avp = _active_version_path(slug)
    if avp.exists():
        try:
            av = json.loads(avp.read_text(encoding="utf-8"))
            if av.get("id") == hid:
                avp.unlink()
        except Exception:
            pass
    return {"ok": True}

# ── PUT /api/library/:slug — full profile write ───────────────────────────────
@router.put("/library/{slug}")
async def api_library_put(slug: str, request: Request):
    body = await request.json()
    pp = _profile_path(slug)
    if not pp.exists():
        raise HTTPException(404, "Not found")
    # Never auto-create ghost versions on Generate — active_version stays as-is so
    # the user can click Save afterward to update their current named version.
    current = json.loads(pp.read_text(encoding="utf-8"))
    new_profile = {**body, "human_corrections": current.get("human_corrections", [])}
    # Preserve layout_example — user-placed component positions must survive a Generate/rebuild
    if "layout_example" not in new_profile and current.get("layout_example"):
        new_profile["layout_example"] = current["layout_example"]
    new_profile.pop("_label", None)
    _write_profile(slug, new_profile)
    return {"ok": True}

# ── DELETE /api/library/:slug/layout_example ──────────────────────────────────
@router.delete("/library/{slug}/layout_example")
def api_library_layout_example_delete(slug: str):
    profile = _read_profile(slug)
    profile.pop("layout_example", None)
    _write_profile(slug, profile)
    return {"ok": True}

# ── GET /api/library/:slug/layout_example ─────────────────────────────────────
@router.get("/library/{slug}/layout_example")
def api_library_layout_example_get(slug: str):
    profile = _read_profile(slug)
    return profile.get("layout_example") or {}

# ── POST /api/gen-tickets/:id/retract ────────────────────────────────────────
@router.post("/gen-tickets/{tid}/retract")
def api_gen_tickets_retract(tid: int):
    data = _read_gen_tickets()
    ticket = next((t for t in data["tickets"] if t["id"] == tid), None)
    if not ticket:
        raise HTTPException(404, "Ticket not found")
    if not ticket.get("slug"):
        raise HTTPException(400, "Ticket has no slug")
    h = _history_dir(ticket["slug"])
    if not h.exists():
        raise HTTPException(400, "No version history for this component")
    created_ms = _parse_ts(ticket.get("created_at", ""))
    snap_file = None
    for f in sorted(h.glob("*.json")):
        try:
            snap = json.loads(f.read_text(encoding="utf-8"))
            if _parse_ts(snap.get("saved_at", "")) >= created_ms:
                snap_file = f
                break
        except Exception:
            pass
    if not snap_file:
        raise HTTPException(400, "No snapshot found for this ticket window")
    snap = json.loads(snap_file.read_text(encoding="utf-8"))
    _snapshot_profile(ticket["slug"], f"auto — before retract of GT-{tid}")
    _write_profile(ticket["slug"], snap["profile"])
    idx = next(i for i, t in enumerate(data["tickets"]) if t["id"] == tid)
    data["tickets"][idx]["status"] = "retracted"
    data["tickets"][idx]["retracted_at"] = datetime.now(timezone.utc).isoformat()
    _save_gen_tickets(data)
    return {"ok": True, "restored_snapshot": snap_file.stem}


# ── Pipeline Agents ──────────────────────────────────────────────────────────
# In-memory state: agent_name → {status, last_run, log, run_id}
# ── Pipeline Agents (chat-capable, multi-turn, session-resumable) ─────────────
_PIPELINE_AGENTS = {
    "orchestrator":      {"label": "Orchestrator",       "icon": "🎯",  "desc": "Run the full 9-step EDA pipeline end-to-end from datasheet to PCB"},
    "datasheet-parser":  {"label": "Datasheet Parser",   "icon": "📄",  "desc": "Parse component datasheets into structured JSON profiles"},
    "library":           {"label": "Library GUI",         "icon": "📚",  "desc": "Manage component library: fix profiles, version history, component save/load bugs"},
    "component":         {"label": "Component Designer", "icon": "🔷",  "desc": "Generate KiCad symbol and rich component profile from parsed data"},
    "footprint":         {"label": "Footprint Designer", "icon": "🔲",  "desc": "Generate PCB footprint JSON from component package information"},
    "example-schematic": {"label": "Example Schematic",  "icon": "📐",  "desc": "Build a reference application schematic for the component"},
    "layout-example":    {"label": "Layout Example",     "icon": "🖼️",  "desc": "Generate PCB layout example for a component's typical application circuit"},
    "schematic":         {"label": "Schematic Agent",    "icon": "🗺️",  "desc": "Design the full project schematic from a brief description"},
    "schematic-gui":     {"label": "Schematic GUI",      "icon": "✏️",  "desc": "Fix bugs in the schematic canvas editor: wires, symbols, nets, labels, undo"},
    "connectivity":      {"label": "Connectivity Check", "icon": "🔗",  "desc": "Verify net connectivity, detect orphans, floating pins, loops"},
    "autoplace":         {"label": "Auto Placer",        "icon": "🧩",  "desc": "Place components on the PCB canvas using heuristic clustering"},
    "autoroute":         {"label": "Auto Router",        "icon": "〰️",  "desc": "Route PCB traces between component pads"},
    "layout":            {"label": "Layout Assembler",   "icon": "📋",  "desc": "Assemble the final PCB layout JSON and KiCad PCB file"},
    "layout-gui":        {"label": "Layout GUI",         "icon": "🖥️",  "desc": "Fix bugs in the PCB editor: routing, DRC, layer panel, ratsnest, selection"},
    "import-export":     {"label": "Import / Export",    "icon": "📤",  "desc": "Fix Gerber export, KiCad export, schematic import, BOM export issues"},
}

PROJECT_ROOT_SA = Path(__file__).parent.parent
_PA_STATE_DIR = PROJECT_ROOT_SA / "data" / "agents"
_PA_STATE_DIR.mkdir(parents=True, exist_ok=True)

# _agent_instances: starts as copy of _PIPELINE_AGENTS, grows with spawned clones.
# Clone names are "{base}-2", "{base}-3", etc.
_agent_instances: dict[str, dict] = dict(_PIPELINE_AGENTS)

# per-agent state
_pipeline_state: dict[str, dict] = {
    name: {"status": "idle", "last_run": None, "run_id": None}
    for name in _PIPELINE_AGENTS
}
# per-agent chat history: list of {id, role, content, ts}
_pipeline_chat: dict[str, list] = {name: [] for name in _PIPELINE_AGENTS}
# per-agent claude-agent-sdk session id (for multi-turn resumption)
_pipeline_sessions: dict[str, str | None] = {name: None for name in _PIPELINE_AGENTS}
_pipeline_tasks: dict[str, asyncio.Task] = {}
_pa_msg_counter: list[int] = [0]


def _pa_base_name(name: str) -> str:
    """Strip trailing '-N' clone suffix to get the template name."""
    m = re.match(r'^(.+)-(\d+)$', name)
    if m and m.group(1) in _PIPELINE_AGENTS:
        return m.group(1)
    return name


def _spawn_instance(base_name: str) -> str:
    """Create a numbered clone of base_name and return the new instance name."""
    n = 2
    while f"{base_name}-{n}" in _agent_instances:
        n += 1
    new_name = f"{base_name}-{n}"
    tmpl = _PIPELINE_AGENTS[base_name]
    _agent_instances[new_name] = {
        "label": f"{tmpl['label']} #{n}",
        "icon": tmpl["icon"],
        "desc": tmpl["desc"],
        "base": base_name,
        "is_clone": True,
    }
    _pipeline_state[new_name] = {"status": "idle", "last_run": None, "run_id": None}
    _pipeline_chat[new_name] = []
    _pipeline_sessions[new_name] = None
    return new_name


def _get_free_instance(base_name: str) -> str:
    """
    Return a free (non-running) instance for base_name.
    Checks base first, then existing clones, spawns a new one if all are busy.
    """
    candidates = [base_name] + [
        n for n in _agent_instances if _agent_instances[n].get("base") == base_name
    ]
    for c in candidates:
        if _pipeline_state.get(c, {}).get("status") != "running":
            return c
    # All busy — spawn a new clone
    return _spawn_instance(base_name)


def _prune_idle_clones() -> None:
    """Remove clones that are idle and have no chat history."""
    to_remove = [
        n for n, meta in list(_agent_instances.items())
        if meta.get("is_clone")
        and _pipeline_state.get(n, {}).get("status") != "running"
        and not _pipeline_chat.get(n)
    ]
    for n in to_remove:
        _agent_instances.pop(n, None)
        _pipeline_state.pop(n, None)
        _pipeline_chat.pop(n, None)
        _pipeline_sessions.pop(n, None)


def _pa_next_id() -> int:
    _pa_msg_counter[0] += 1
    return _pa_msg_counter[0]


def _pa_save(name: str) -> None:
    """Persist chat history, session ID, and status for an agent to disk."""
    try:
        state = _pipeline_state.get(name, {})
        (_PA_STATE_DIR / f"{name}.json").write_text(
            json.dumps({
                "session_id": _pipeline_sessions[name],
                "messages": _pipeline_chat[name],
                "msg_counter": _pa_msg_counter[0],
                "status": state.get("status", "idle"),
                "last_run": state.get("last_run"),
            }, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:
        pass


def _pa_load_all() -> None:
    """Load persisted state for all agents on startup."""
    for name in _PIPELINE_AGENTS:
        p = _PA_STATE_DIR / f"{name}.json"
        if not p.exists():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            _pipeline_sessions[name] = data.get("session_id")
            _pipeline_chat[name] = data.get("messages", [])
            # Restore msg counter to avoid ID collisions
            saved_counter = data.get("msg_counter", 0)
            if saved_counter > _pa_msg_counter[0]:
                _pa_msg_counter[0] = saved_counter
            # Restore status and last_run so unread indicator survives restarts
            saved_status = data.get("status", "idle")
            if saved_status in ("done", "error"):
                _pipeline_state[name]["status"] = saved_status
                _pipeline_state[name]["last_run"] = data.get("last_run")
        except Exception:
            pass


_pa_load_all()


def _pa_system_prompt(name: str) -> str:
    """Build the initial system context for a pipeline agent."""
    claude_md = PROJECT_ROOT_SA / "agents" / name / "CLAUDE.md"
    root_claude_md = PROJECT_ROOT_SA / "CLAUDE.md"
    ctx = ""
    if root_claude_md.exists():
        ctx += f"\n\n# Project CLAUDE.md\n{root_claude_md.read_text(encoding='utf-8')}"
    if claude_md.exists():
        ctx += f"\n\n# Agent CLAUDE.md ({name})\n{claude_md.read_text(encoding='utf-8')}"

    lib_dir = PROJECT_ROOT_SA / "frontend" / "static" / "library"
    slugs = [d.name for d in lib_dir.iterdir() if d.is_dir()] if lib_dir.exists() else []

    return (
        f"You are the '{name}' EDA pipeline agent for the auto-eda project.\n"
        f"Project root: {PROJECT_ROOT_SA}\n"
        f"Output dir:   {PROJECT_ROOT_SA / 'data' / 'outputs'}\n"
        f"Library dir:  {lib_dir}  ({len(slugs)} components: {', '.join(slugs[:10])}{'…' if len(slugs)>10 else ''})\n"
        f"Projects dir: {PROJECT_ROOT_SA / 'frontend' / 'static' / 'projects'}\n"
        f"Uploads dir:  {PROJECT_ROOT_SA / 'data' / 'uploads'}\n"
        f"\nIMPORTANT: You have full read/write access to the project files. "
        f"When the user asks you to change the design, directly edit the relevant JSON files in the library/, projects/, or data/ directories. "
        f"After writing files, briefly summarise what changed.\n"
        f"\n## How to handle tasks\n"
        f"For any non-trivial task (more than a single quick answer), follow this pattern:\n"
        f"\n**Step 1 — Post a plan first.** Before doing any work, write your plan as a markdown checklist:\n"
        f"```\n"
        f"## Plan\n"
        f"- [ ] Step 1: <what you will do>\n"
        f"- [ ] Step 2: <what you will do>\n"
        f"- [ ] Step 3: <what you will do>\n"
        f"```\n"
        f"Keep steps small and concrete. 3–7 steps is ideal. Post this plan as your FIRST output before touching any files.\n"
        f"\n**Step 2 — Execute step by step.** After posting the plan, work through each step. "
        f"After completing each step, post a brief update re-printing the checklist with that step marked `[x]` and the next step marked `→`:\n"
        f"```\n"
        f"## Progress\n"
        f"- [x] Step 1: <done>\n"
        f"- → Step 2: <working on this now>\n"
        f"- [ ] Step 3: <upcoming>\n"
        f"```\n"
        f"\n**For simple conversational messages** (greetings, quick questions, clarifications) — just reply naturally, no plan needed.\n"
        f"\n**If the user sends a message while you are mid-task** — finish the current step first, then address the user's message before continuing. Never abandon a task silently.\n"
        f"\n## MANDATORY completion rule\n"
        f"After every task — no matter how many tool calls you made — you MUST write a final human-readable reply that:\n"
        f"1. States what was done (files written, endpoints called, values changed)\n"
        f"2. Shows the result or key output (a snippet, a count, a diff summary)\n"
        f"3. Mentions any caveats or follow-up actions needed\n"
        f"\nDo NOT end silently on a tool call. Your last output must always be a text message to the user.\n"
        f"If you have nothing to say, write: 'Done — no changes were needed.'\n"
        f"{ctx}"
    )


def _pa_cwd(name: str) -> str:
    agent_dir = PROJECT_ROOT_SA / "agents" / name
    return str(agent_dir) if agent_dir.exists() else str(PROJECT_ROOT_SA)


@router.get("/pipeline/agents")
def api_pipeline_agents_list():
    """Return all pipeline agents (including active clones) with current state."""
    _prune_idle_clones()
    result = []
    for name, meta in _agent_instances.items():
        state = _pipeline_state.get(name, {"status": "idle", "last_run": None})
        msgs = _pipeline_chat.get(name, [])
        last_msg = msgs[-1]["content"][:120] if msgs else ""
        result.append({
            "name": name,
            "label": meta["label"],
            "icon": meta["icon"],
            "desc": meta["desc"],
            "status": state["status"],
            "last_run": state["last_run"],
            "msg_count": len(msgs),
            "last_msg": last_msg,
            "has_session": bool(_pipeline_sessions.get(name)),
            "is_clone": meta.get("is_clone", False),
            "base": meta.get("base", name),
        })
    return result


_PA_SUMMARIES_DIR = PROJECT_ROOT_SA / "data" / "agents" / "summaries"

@router.get("/pipeline/agents/summaries")
def api_pipeline_agent_summaries():
    """Return all agent summaries grouped by agent name, newest first."""
    result: dict[str, list] = {}
    if not _PA_SUMMARIES_DIR.exists():
        return result
    for agent_dir in sorted(_PA_SUMMARIES_DIR.iterdir()):
        if not agent_dir.is_dir():
            continue
        entries = []
        for f in sorted(agent_dir.glob("*.md"), reverse=True):
            try:
                body = f.read_text(encoding="utf-8").strip()
                lines = body.splitlines()
                title = lines[0].lstrip("#").strip() if lines else f.stem
                entries.append({
                    "id": f.stem,
                    "ts": f.stem,
                    "title": title,
                    "body": body,
                })
            except Exception:
                pass
        if entries:
            result[agent_dir.name] = entries
    return result


@router.get("/pipeline/agents/{name}/history")
def api_pipeline_agent_history(name: str):
    """Return full chat history for an agent."""
    if name not in _agent_instances:
        raise HTTPException(404, f"Unknown agent: {name}")
    return {
        "name": name,
        "status": _pipeline_state[name]["status"],
        "messages": _pipeline_chat[name],
        "has_session": bool(_pipeline_sessions[name]),
    }


@router.delete("/pipeline/agents/{name}/history")
def api_pipeline_agent_clear(name: str):
    """Clear chat history and session for an agent."""
    if name not in _agent_instances:
        raise HTTPException(404, f"Unknown agent: {name}")
    task = _pipeline_tasks.get(name)
    if task and not task.done():
        task.cancel()
    _pipeline_chat[name] = []
    _pipeline_sessions[name] = None
    _pipeline_state[name].update({"status": "idle", "run_id": None})
    _pa_save(name)
    _broadcast("pipeline_agent_done", {"name": name, "status": "cleared"})
    return {"ok": True}


async def _agent_query(prompt: str, opts):
    """Run claude-agent-sdk in a dedicated thread with ProactorEventLoop.

    On Windows, uvicorn forces SelectorEventLoop which cannot spawn subprocesses.
    We work around this by running the SDK inside a fresh thread that owns a
    ProactorEventLoop, bridging results back to the uvicorn loop via a queue.
    """
    result_q: asyncio.Queue = asyncio.Queue()
    main_loop = asyncio.get_running_loop()
    errors: list = []

    def _thread():
        async def _inner():
            try:
                from claude_agent_sdk import query  # type: ignore
                async for msg in query(prompt=prompt, options=opts):
                    main_loop.call_soon_threadsafe(result_q.put_nowait, msg)
            except Exception as exc:
                errors.append(exc)
            finally:
                main_loop.call_soon_threadsafe(result_q.put_nowait, None)  # sentinel

        if sys.platform == "win32":
            loop = asyncio.ProactorEventLoop()
        else:
            loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(_inner())
        finally:
            loop.close()

    threading.Thread(target=_thread, daemon=True).start()

    while True:
        msg = await result_q.get()
        if msg is None:
            break
        yield msg

    if errors:
        raise errors[0]


# ── Ticket type → agent that handles it ──────────────────────────────────────
_TICKET_AGENT_MAP: dict[str, str] = {
    "datasheet":     "datasheet-parser",
    "example":       "example-schematic",
    "footprint":     "footprint",
    "layout":        "layout-example",
    "build-project": "orchestrator",
    "circuit":       "orchestrator",
}


async def _agent_send(
    name: str,
    user_msg: str,
    model_id: str = "claude-sonnet-4-6",
    use_thinking: bool = False,
) -> dict:
    """Internal helper: route a message to a pipeline agent and start the background task.

    Used by both the HTTP chat endpoint and the ticket dispatcher so the logic
    lives in exactly one place.
    """
    base = _pa_base_name(name)
    routing_base = base if base in _PIPELINE_AGENTS else name
    if _pipeline_state.get(name, {}).get("status") == "running":
        name = _get_free_instance(routing_base)
    elif name not in _agent_instances:
        name = routing_base

    msg_id = _pa_next_id()
    now = datetime.now(timezone.utc).isoformat()
    _pipeline_chat[name].append({"id": msg_id, "role": "user", "content": user_msg, "ts": now})
    _pa_save(name)

    run_id = f"{name}-{int(time.time())}"
    _pipeline_state[name].update({"status": "running", "last_run": now, "run_id": run_id})
    _broadcast("pipeline_agent_started", {
        "name": name, "run_id": run_id,
        "label": _agent_instances[name]["label"],
        "msg": {"id": msg_id, "role": "user", "content": user_msg, "ts": now},
    })

    existing_session = _pipeline_sessions[name]

    async def _run():
        resp_id = _pa_next_id()
        resp_ts = datetime.now(timezone.utc).isoformat()
        resp_parts: list[str] = []
        text_parts: list[str] = []   # only actual assistant text, for dedup

        # Placeholder for streaming assistant message
        _pipeline_chat[name].append({"id": resp_id, "role": "assistant", "content": "", "ts": resp_ts, "streaming": True})
        _broadcast("pipeline_agent_msg_start", {"name": name, "msg": {"id": resp_id, "role": "assistant", "content": "", "ts": resp_ts, "streaming": True}})

        try:
            from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, SystemMessage, AssistantMessage  # type: ignore
            from claude_agent_sdk.types import ThinkingConfigAdaptive  # type: ignore

            # First message: prepend system context; subsequent: just the user message
            # For clones, use base agent name for cwd/system prompt resolution
            _agent_base = _pa_base_name(name)
            if existing_session:
                prompt = user_msg
            else:
                prompt = f"{_pa_system_prompt(_agent_base)}\n\n---\nUser: {user_msg}"

            stderr_lines: list[str] = []

            def _capture_stderr(line: str) -> None:
                stderr_lines.append(line)
                _broadcast("pipeline_agent_chunk", {"name": name, "msg_id": resp_id,
                                                    "chunk": f"\n[stderr] {line}", "is_tool": True})

            def _fmt_tool(tool_name: str, inp: dict) -> str:
                """Format a tool call with full detail."""
                if tool_name == "Bash":
                    return f"$ {inp.get('command', '')}"
                if tool_name == "Read":
                    path = inp.get('file_path', '')
                    extra = ""
                    if inp.get('offset'): extra += f"  offset={inp['offset']}"
                    if inp.get('limit'):  extra += f"  limit={inp['limit']}"
                    return f"Read: {path}{extra}"
                if tool_name == "Write":
                    path    = inp.get('file_path', '')
                    content = inp.get('content', '')
                    return f"Write: {path}\n{content}"
                if tool_name == "Edit":
                    path    = inp.get('file_path', '')
                    old_str = inp.get('old_string', '')
                    new_str = inp.get('new_string', '')
                    return (f"Edit: {path}\n"
                            f"--- remove:\n{old_str}\n"
                            f"+++ insert:\n{new_str}")
                if tool_name == "Grep":
                    flags = ""
                    if inp.get('-i'): flags += " -i"
                    return f"Grep{flags} {inp.get('pattern','')!r} in {inp.get('path', '.')}"
                if tool_name == "Glob":
                    return f"Glob {inp.get('pattern','')} in {inp.get('path','.')}"
                if tool_name == "WebFetch":
                    return f"Fetch: {inp.get('url','')}"
                if tool_name == "WebSearch":
                    return f"Search: {inp.get('query','')}"
                # fallback: full JSON
                return f"{tool_name}: {json.dumps(inp, indent=2)}"

            def _make_opts(session_id):
                return ClaudeAgentOptions(
                    cwd=_pa_cwd(_agent_base),
                    allowed_tools=["Bash", "Read", "Write", "Edit", "Glob", "Grep", "WebFetch", "WebSearch"],
                    permission_mode="bypassPermissions",
                    max_turns=40,
                    resume=session_id,
                    env={"IS_SANDBOX": "1", "CLAUDECODE": ""},
                    stderr=_capture_stderr,
                    thinking=ThinkingConfigAdaptive(type="adaptive") if use_thinking else None,
                    model=model_id,
                )

            # Exit codes that mean the OS killed the process — safe to retry
            # NOTE: exit code 1 is NOT included — it means a real CLI error (auth, config, etc.)
            _TRANSIENT_EXIT_CODES = {
                3221225786,  # 0xC000013A STATUS_CONTROL_C_EXIT (Windows killed process)
                3221225477,  # 0xC0000005 ACCESS_VIOLATION (rare crash)
            }

            def _is_transient(exc: Exception) -> bool:
                msg = str(exc)
                for code in _TRANSIENT_EXIT_CODES:
                    if str(code) in msg:
                        return True
                return False

            async def _stream(session_id, attempts_left=3):
                try:
                    async for msg in _agent_query(prompt=prompt, opts=_make_opts(session_id)):
                        yield msg
                except Exception as e:
                    if _is_transient(e) and attempts_left > 1:
                        wait = 2 ** (3 - attempts_left)  # 1s, 2s, 4s backoff
                        _broadcast("pipeline_agent_chunk", {"name": name, "msg_id": resp_id,
                                                            "chunk": f"\n[process killed by OS, retrying in {wait}s… ({attempts_left-1} left)]\n",
                                                            "is_tool": True})
                        await asyncio.sleep(wait)
                        async for msg in _stream(session_id, attempts_left - 1):
                            yield msg
                    elif session_id and attempts_left > 1 and "session" in str(e).lower():
                        # Session expired or invalid — retry fresh
                        _pipeline_sessions[name] = None
                        _broadcast("pipeline_agent_chunk", {"name": name, "msg_id": resp_id,
                                                            "chunk": "\n[session expired, restarting…]\n", "is_tool": True})
                        async for msg in _stream(None, attempts_left - 1):
                            yield msg
                    else:
                        # Surface stderr so the user can see why the CLI failed
                        stderr_txt = getattr(e, "stderr", None) or ""
                        if stderr_txt:
                            _broadcast("pipeline_agent_chunk", {"name": name, "msg_id": resp_id,
                                                                "chunk": f"\n[CLI stderr]: {stderr_txt}\n", "is_tool": True})
                        raise

            async for sdk_msg in _stream(existing_session):
                if isinstance(sdk_msg, SystemMessage) and sdk_msg.subtype == "init":
                    _pipeline_sessions[name] = sdk_msg.data.get("session_id")
                    _pa_save(name)  # persist session ID immediately
                elif isinstance(sdk_msg, AssistantMessage):
                    for block in sdk_msg.content:
                        block_type = getattr(block, "type", "")
                        if block_type == "thinking":
                            # Show thinking as a collapsible thought — send full text
                            thought = getattr(block, "thinking", "") or ""
                            chunk = f"\n💭 {thought}\n"
                            resp_parts.append(chunk)
                            full = "".join(resp_parts)
                            for m in _pipeline_chat[name]:
                                if m["id"] == resp_id:
                                    m["content"] = full
                                    break
                            _broadcast("pipeline_agent_chunk", {"name": name, "msg_id": resp_id,
                                                                "chunk": chunk, "is_thinking": True})
                        elif block_type == "text":
                            chunk = block.text
                            resp_parts.append(chunk)
                            text_parts.append(chunk)
                            full = "".join(resp_parts)
                            for m in _pipeline_chat[name]:
                                if m["id"] == resp_id:
                                    m["content"] = full
                                    break
                            _broadcast("pipeline_agent_chunk", {"name": name, "msg_id": resp_id, "chunk": chunk})
                        elif block_type == "tool_use":
                            formatted = _fmt_tool(block.name, block.input or {})
                            tool_line = f"\n▶ {formatted}\n"
                            resp_parts.append(tool_line)
                            full = "".join(resp_parts)
                            for m in _pipeline_chat[name]:
                                if m["id"] == resp_id:
                                    m["content"] = full
                                    break
                            _broadcast("pipeline_agent_chunk", {"name": name, "msg_id": resp_id,
                                                                "chunk": tool_line, "is_tool": True,
                                                                "tool_name": block.name})
                        elif block_type == "tool_result":
                            # Show full result output
                            result_text = ""
                            for part in (getattr(block, "content", None) or []):
                                if getattr(part, "type", "") == "text":
                                    result_text += part.text
                            if result_text:
                                result_line = f"◀ {result_text.strip()}\n"
                                resp_parts.append(result_line)
                                full = "".join(resp_parts)
                                for m in _pipeline_chat[name]:
                                    if m["id"] == resp_id:
                                        m["content"] = full
                                        break
                                _broadcast("pipeline_agent_chunk", {"name": name, "msg_id": resp_id,
                                                                    "chunk": result_line, "is_result": True})
                elif isinstance(sdk_msg, ResultMessage):
                    if sdk_msg.result and sdk_msg.result not in "".join(text_parts):
                        resp_parts.append(f"\n{sdk_msg.result}")
                        text_parts.append(sdk_msg.result)
                        _broadcast("pipeline_agent_chunk", {"name": name, "msg_id": resp_id, "chunk": f"\n{sdk_msg.result}"})

                # Periodically flush to disk every 5 messages so progress survives restarts
                if len(resp_parts) % 5 == 0:
                    _pa_save(name)

            final = "".join(resp_parts)
            for m in _pipeline_chat[name]:
                if m["id"] == resp_id:
                    m["content"] = final
                    m["streaming"] = False
                    break
            _pipeline_state[name]["status"] = "done"
            _pa_save(name)
            _broadcast("pipeline_agent_done", {"name": name, "run_id": run_id, "status": "done",
                                               "msg_id": resp_id, "session_id": _pipeline_sessions[name]})
            # Notify the UI to refresh library/projects in case agent wrote files
            _broadcast("library_updated", {"reason": f"agent:{name}"})
        except asyncio.CancelledError:
            _pipeline_state[name]["status"] = "idle"
            _pa_save(name)
            _broadcast("pipeline_agent_done", {"name": name, "run_id": run_id, "status": "cancelled", "msg_id": resp_id})
        except Exception as exc:
            import traceback as _tb
            _full = _tb.format_exc()
            print(f"[agent-error] {type(exc).__name__}: {exc}\n{_full}", flush=True)
            err = f"\n[error] {type(exc).__name__}: {exc}\n{_full}"
            for m in _pipeline_chat[name]:
                if m["id"] == resp_id:
                    m["content"] += err
                    m["streaming"] = False
                    break
            _pipeline_state[name]["status"] = "error"
            # Clear session so next message starts fresh instead of resuming a broken session
            _pipeline_sessions[name] = None
            _pa_save(name)
            _broadcast("pipeline_agent_done", {"name": name, "run_id": run_id, "status": "error",
                                               "msg_id": resp_id, "error": str(exc)})

    task = asyncio.create_task(_run())
    _pipeline_tasks[name] = task
    return {"ok": True, "run_id": run_id, "msg_id": msg_id, "actual_name": name}


@router.post("/pipeline/agents/{name}/chat")
async def api_pipeline_agent_chat(name: str, request: Request):
    """Send a message to a pipeline agent — auto-routes to a free instance if busy."""
    base = _pa_base_name(name)
    if base not in _PIPELINE_AGENTS and name not in _agent_instances:
        raise HTTPException(404, f"Unknown agent: {name}")
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    user_msg = (body.get("message") or body.get("input") or "").strip()
    if not user_msg:
        raise HTTPException(400, "message required")
    return await _agent_send(
        name,
        user_msg,
        model_id=body.get("model") or "claude-sonnet-4-6",
        use_thinking=bool(body.get("think", False)),
    )


# ── Background ticket dispatcher ──────────────────────────────────────────────
async def _ticket_dispatcher():
    """Poll gen_tickets.json every 3 s, claim pending tickets, and send them
    as prompt messages to the appropriate pipeline agent."""
    await asyncio.sleep(6)  # let the app finish starting before first poll
    while True:
        await asyncio.sleep(3)
        try:
            data = _read_gen_tickets()
            pending = [t for t in data["tickets"] if t.get("status") == "pending"]
            for ticket in pending:
                agent_name = _TICKET_AGENT_MAP.get(ticket.get("type", ""))
                prompt = (ticket.get("prompt") or "").strip()

                # Reject tickets with obviously broken prompts before dispatching
                if not agent_name or agent_name not in _PIPELINE_AGENTS:
                    continue
                if not prompt or "[object Promise]" in prompt or prompt.startswith("WHEN DONE"):
                    idx = next((i for i, t in enumerate(data["tickets"]) if t["id"] == ticket["id"]), -1)
                    if idx != -1:
                        data["tickets"][idx]["status"] = "error"
                        data["tickets"][idx]["error"] = "bad prompt (unresolved Promise or empty)"
                        _save_gen_tickets(data)
                    print(f"[ticket-dispatcher] GT-{ticket['id']:03d} rejected: bad prompt", flush=True)
                    continue

                # Claim atomically before dispatching so a second poll cycle
                # doesn't pick up the same ticket again
                idx = next((i for i, t in enumerate(data["tickets"]) if t["id"] == ticket["id"]), -1)
                if idx == -1:
                    continue
                data["tickets"][idx]["status"] = "running"
                data["tickets"][idx]["started_at"] = datetime.now(timezone.utc).isoformat()
                data["tickets"][idx]["agent"] = agent_name
                _save_gen_tickets(data)
                print(f"[ticket-dispatcher] GT-{ticket['id']:03d} ({ticket.get('type')}) → {agent_name}", flush=True)
                try:
                    result = await _agent_send(agent_name, ticket["prompt"])
                    actual_name = result.get("actual_name", agent_name)
                    agent_task = _pipeline_tasks.get(actual_name)
                    ticket_id = ticket["id"]

                    async def _ticket_watcher(tid=ticket_id, t=agent_task):
                        if t is None:
                            return
                        try:
                            await t
                            d = _read_gen_tickets()
                            for i, tk in enumerate(d["tickets"]):
                                if tk["id"] == tid and tk.get("status") == "running":
                                    d["tickets"][i]["status"] = "done"
                                    d["tickets"][i]["finished_at"] = datetime.now(timezone.utc).isoformat()
                                    _save_gen_tickets(d)
                                    _broadcast("gen_ticket_updated", {"id": tid, "status": "done"})
                                    print(f"[ticket-dispatcher] GT-{tid:03d} → done", flush=True)
                                    break
                        except asyncio.CancelledError:
                            d = _read_gen_tickets()
                            for i, tk in enumerate(d["tickets"]):
                                if tk["id"] == tid and tk.get("status") == "running":
                                    d["tickets"][i]["status"] = "error"
                                    d["tickets"][i]["error"] = "agent cancelled"
                                    _save_gen_tickets(d)
                                    _broadcast("gen_ticket_updated", {"id": tid, "status": "error"})
                                    break
                        except Exception as watcher_err:
                            d = _read_gen_tickets()
                            for i, tk in enumerate(d["tickets"]):
                                if tk["id"] == tid and tk.get("status") == "running":
                                    d["tickets"][i]["status"] = "error"
                                    d["tickets"][i]["error"] = str(watcher_err)
                                    _save_gen_tickets(d)
                                    _broadcast("gen_ticket_updated", {"id": tid, "status": "error"})
                                    break

                    asyncio.create_task(_ticket_watcher())
                except Exception as dispatch_err:
                    print(f"[ticket-dispatcher] GT-{ticket['id']:03d} dispatch failed: {dispatch_err}", flush=True)
                    data = _read_gen_tickets()
                    idx2 = next((i for i, t in enumerate(data["tickets"]) if t["id"] == ticket["id"]), -1)
                    if idx2 != -1:
                        data["tickets"][idx2]["status"] = "error"
                        data["tickets"][idx2]["error"] = str(dispatch_err)
                        _save_gen_tickets(data)
        except Exception as e:
            print(f"[ticket-dispatcher] poll error: {e}", flush=True)


@router.post("/pipeline/agents/{name}/stop")
async def api_pipeline_agent_stop(name: str):
    """Cancel a running pipeline agent task."""
    if name not in _agent_instances:
        raise HTTPException(404, f"Unknown agent: {name}")
    task = _pipeline_tasks.get(name)
    if task and not task.done():
        task.cancel()
        _pipeline_state[name]["status"] = "idle"
        _broadcast("pipeline_agent_done", {"name": name, "status": "cancelled"})
    return {"ok": True}

# ── ic_layout, build_netlist, _SYMDEFS, etc. → backend.engines.netlist ────────
# ── run_drc → backend.engines.drc ────────────────────────────────────────────
# ── run_autoroute, _autoroute_* → backend.engines.autoroute ──────────────────
# ── run_autoplace, _load_schematic_hints, _positions_are_spread → backend.engines.autoplace
# ── import_schematic, _pick_footprint, _match_package_to_footprint → backend.engines.importer
#
# All core algorithms extracted to backend/engines/ — endpoints below are thin wrappers.


# ─────────────────────────────────────────────────────────────────────────────


# ── POST /api/netlist ──────────────────────────────────────────────────────────
@router.post("/netlist")
async def extract_netlist(request: Request):
    body = await request.json()
    components  = body.get("components", [])
    wires       = body.get("wires", [])
    labels      = body.get("labels", [])
    no_connects = body.get("noConnects", [])
    result = build_netlist(components, wires, labels, no_connects, library_dir=LIBRARY_DIR)
    return result


# ── GET /api/library/{slug}/layout ────────────────────────────────────────────
@router.get("/library/{slug}/layout")
async def get_ic_layout(slug: str):
    profile_path = LIBRARY_DIR / slug / "profile.json"
    if not profile_path.exists():
        raise HTTPException(404, "Profile not found")
    profile = json.loads(profile_path.read_text("utf-8"))
    return ic_layout(profile.get("pins", []))


# ── GET /api/projects/{pid}/bom ───────────────────────────────────────────────
@router.get("/projects/{pid}/bom")
async def get_project_bom(pid: str):
    fpath = PROJECTS_DIR / f"{pid}.json"
    if not fpath.exists():
        raise HTTPException(404, "Project not found")
    project = json.loads(fpath.read_text("utf-8"))

    # Aggregate by (slug, value)
    groups: dict[tuple, dict] = {}
    for comp in project.get("components", []):
        slug      = comp.get("slug", "")
        value     = comp.get("value", "")
        sym_type  = comp.get("symType", "")
        des       = comp.get("designator", comp.get("id", ""))
        key       = (slug, value)
        if key not in groups:
            # Try to get description from profile
            desc = ""
            pp = LIBRARY_DIR / slug / "profile.json"
            if pp.exists():
                try:
                    desc = json.loads(pp.read_text("utf-8")).get("description", "")
                except Exception:
                    pass
            groups[key] = {
                "designators": [],
                "symType":     sym_type,
                "value":       value,
                "slug":        slug,
                "description": desc,
                "quantity":    0,
            }
        groups[key]["designators"].append(des)
        groups[key]["quantity"] += 1

    bom = []
    for (slug, value), row in sorted(groups.items()):
        bom.append({
            "designator":  ", ".join(sorted(row["designators"])),
            "symType":     row["symType"],
            "value":       row["value"],
            "slug":        row["slug"],
            "description": row["description"],
            "quantity":    row["quantity"],
        })
    return bom


# ── POST /api/pcb/{bid}/drc ───────────────────────────────────────────────────
@router.post("/pcb/{bid}/drc")
async def drc_board_id(bid: str):
    fpath = PCB_BOARDS_DIR / f"{bid}.json"
    if not fpath.exists():
        raise HTTPException(404, "Not found")
    board = json.loads(fpath.read_text("utf-8"))
    return run_drc(board)


@router.post("/pcb/drc")
async def drc_board_direct(request: Request):
    board = await request.json()
    return run_drc(board)


# ── POST /api/pcb/{bid}/autoroute ─────────────────────────────────────────────
@router.post("/pcb/{bid}/autoroute")
async def autoroute_board_id(bid: str):
    fpath = PCB_BOARDS_DIR / f"{bid}.json"
    if not fpath.exists():
        raise HTTPException(404, "Not found")
    board = json.loads(fpath.read_text("utf-8"))
    result = run_autoroute(board, projects_dir=PROJECTS_DIR, library_dir=LIBRARY_DIR)
    # Save updated board
    autoroute_meta = result.pop("_autoroute", {})
    fpath.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return {
        "routed": autoroute_meta.get("routed", 0),
        "total":  autoroute_meta.get("total", 0),
        "via_count": autoroute_meta.get("vias", 0),
        "traces": result.get("traces", []),
        "vias":   result.get("vias", []),
        "violations": autoroute_meta.get("violations", []),
        "failed_nets": autoroute_meta.get("failed_nets", []),
        "kept_existing": autoroute_meta.get("kept_existing", 0),
    }


@router.post("/pcb/autoroute")
async def autoroute_direct(request: Request):
    body  = await request.json()
    board = body.get("board", body)
    result = run_autoroute(board, projects_dir=PROJECTS_DIR, library_dir=LIBRARY_DIR)
    autoroute_meta = result.pop("_autoroute", {})
    return {
        "routed": autoroute_meta.get("routed", 0),
        "total":  autoroute_meta.get("total", 0),
        "via_count": autoroute_meta.get("vias", 0),
        "traces": result.get("traces", []),
        "vias":   result.get("vias", []),
        "violations": autoroute_meta.get("violations", []),
        "failed_nets": autoroute_meta.get("failed_nets", []),
        "kept_existing": autoroute_meta.get("kept_existing", 0),
    }


@router.post("/pcb/{bid}/autoplace")
async def autoplace_board_id(bid: str, clearance_mm: float = 1.0):
    fpath = PCB_BOARDS_DIR / f"{bid}.json"
    if not fpath.exists():
        raise HTTPException(404, "Board not found")
    board = json.loads(fpath.read_text("utf-8"))
    _output_dir = Path(os.getenv("OUTPUT_DIR", str(PROJECT_ROOT_SA / "data" / "outputs")))
    result = run_autoplace(board, min_clearance_mm=max(0.0, clearance_mm),
                           projects_dir=PROJECTS_DIR, output_dir=_output_dir)
    fpath.write_text(json.dumps(result, indent=2), "utf-8")
    resp: dict[str, Any] = {"components": result.get("components", []), "placed": len(result.get("components", []))}
    return resp


@router.post("/pcb/autoplace")
async def autoplace_direct(request: Request):
    body = await request.json()
    board = body.get("board", body)
    clearance_mm = float(body.get("clearance_mm", 1.0))
    _output_dir = Path(os.getenv("OUTPUT_DIR", str(PROJECT_ROOT_SA / "data" / "outputs")))
    result = run_autoplace(board, min_clearance_mm=max(0.0, clearance_mm),
                           projects_dir=PROJECTS_DIR, output_dir=_output_dir)
    resp: dict[str, Any] = {"components": result.get("components", []), "placed": len(result.get("components", []))}
    return resp


@router.post("/pcb/import-schematic")
async def api_pcb_import_schematic(request: Request):
    """Convert a schematic project JSON into an initial PCB board layout."""
    body = await request.json()
    project: dict = body.get("project", {})
    netlist: dict = body.get("netlist") or {}
    bw = float(body.get("boardW", 100.0))
    bh = float(body.get("boardH", 80.0))
    return import_schematic(
        project, netlist, bw, bh,
        library_dir=LIBRARY_DIR,
        footprints_dir=FOOTPRINTS_DIR,
        pcb_boards_dir=PCB_BOARDS_DIR,
    )


# ── Gerber export helper ───────────────────────────────────────────────────────

def gerber_for_board(board: dict) -> bytes:
    """Generate a ZIP of Gerber files for the board."""
    bw = float(board.get("board", {}).get("width",  board.get("width",  200)))
    bh = float(board.get("board", {}).get("height", board.get("height", 200)))

    def _mm(v: float) -> str:
        return str(int(round(v * 1_000_000))).zfill(7)

    # F_Cu.gtl  — copper traces
    def _f_cu() -> str:
        lines = [
            "%FSLAX66Y66*%",
            "%MOMM*%",
            "%LPD*%",
            "%ADD10C,0.25*%",  # default aperture
            "D10*",
        ]
        for trace in board.get("traces", []):
            layer = trace.get("layer", "F.Cu")
            if layer not in ("F.Cu", ""):
                continue
            w = float(trace.get("width", 0.25))
            w_um = int(round(w * 1_000_000))
            lines.append(f"%ADD11C,{w / 1:.6f}*%")
            lines.append("D11*")
            for seg in trace.get("segments", []):
                x0 = float(seg.get("start", {}).get("x", 0))
                y0 = float(seg.get("start", {}).get("y", 0))
                x1 = float(seg.get("end",   {}).get("x", 0))
                y1 = float(seg.get("end",   {}).get("y", 0))
                lines.append(f"X{_mm(x0)}Y{_mm(y0)}D02*")
                lines.append(f"X{_mm(x1)}Y{_mm(y1)}D01*")
        lines.append("M02*")
        return "\n".join(lines)

    # Edge_Cuts.gml — board outline
    def _edge_cuts() -> str:
        lines = [
            "%FSLAX66Y66*%",
            "%MOMM*%",
            "%LPD*%",
            "%ADD10C,0.05*%",
            "D10*",
            f"X{_mm(0)}Y{_mm(0)}D02*",
            f"X{_mm(bw)}Y{_mm(0)}D01*",
            f"X{_mm(bw)}Y{_mm(bh)}D01*",
            f"X{_mm(0)}Y{_mm(bh)}D01*",
            f"X{_mm(0)}Y{_mm(0)}D01*",
            "M02*",
        ]
        return "\n".join(lines)

    # Excellon drill file
    def _drill() -> str:
        lines = [
            "M48",
            "METRIC,TZ",
            "T1C0.800",
            "%",
            "G90",
            "G05",
            "T1",
        ]
        for comp in board.get("components", []):
            cx = float(comp.get("x", 0))
            cy = float(comp.get("y", 0))
            for pad in comp.get("pads", []):
                if pad.get("type") == "thru_hole" and pad.get("drill"):
                    px = cx + float(pad.get("x", 0))
                    py = cy + float(pad.get("y", 0))
                    lines.append(f"X{px * 1000:.0f}Y{py * 1000:.0f}")
        lines.append("T0")
        lines.append("M30")
        return "\n".join(lines)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("F_Cu.gtl",        _f_cu())
        zf.writestr("Edge_Cuts.gml",   _edge_cuts())
        zf.writestr("drill.drl",        _drill())
    return buf.getvalue()


# ── GET /api/pcb/{bid}/export/gerber ─────────────────────────────────────────
@router.get("/pcb/{bid}/export/gerber")
async def export_gerber(bid: str):
    fpath = PCB_BOARDS_DIR / f"{bid}.json"
    if not fpath.exists():
        raise HTTPException(404, "Not found")
    board = json.loads(fpath.read_text("utf-8"))
    data  = gerber_for_board(board)
    name  = board.get("name", "board")
    safe  = re.sub(r"[^a-zA-Z0-9_\-]", "_", name)
    return StreamingResponse(
        iter([data]),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{safe}_gerbers.zip"'},
    )


# ── KiCad export helper ────────────────────────────────────────────────────────

def kicad_for_board(board: dict) -> str:
    """Generate KiCad .kicad_pcb S-expression content."""
    lines: list[str] = []
    lines.append("(kicad_pcb (version 20221018) (generator auto-eda)")

    # Net index
    all_nets = [""]  # index 0 = no net
    for net_entry in board.get("nets", []):
        n = net_entry.get("name", "")
        if n and n not in all_nets:
            all_nets.append(n)
    net_idx = {n: i for i, n in enumerate(all_nets)}

    lines.append("  (net 0 \"\")")
    for i, n in enumerate(all_nets[1:], 1):
        lines.append(f'  (net {i} "{n}")')

    # Board outline
    bw = float(board.get("board", {}).get("width",  board.get("width",  200)))
    bh = float(board.get("board", {}).get("height", board.get("height", 200)))
    lines.append(f'  (gr_rect (start 0 0) (end {bw} {bh}) (layer "Edge.Cuts") (width 0.05))')

    # Footprints
    for comp in board.get("components", []):
        ref = comp.get("ref", comp.get("id", ""))
        val = comp.get("value", "")
        cx  = float(comp.get("x", 0))
        cy  = float(comp.get("y", 0))
        rot = float(comp.get("rotation", 0))
        fp_name = comp.get("footprint", "")
        lines.append(f'  (footprint "{fp_name}" (layer "F.Cu") (at {cx} {cy} {rot})')
        lines.append(f'    (fp_text reference "{ref}" (at 0 -1) (layer "F.SilkS"))')
        lines.append(f'    (fp_text value "{val}" (at 0 1) (layer "F.Fab"))')
        for pad in comp.get("pads", []):
            pnum  = pad.get("number", "1")
            px    = float(pad.get("x", 0))
            py    = float(pad.get("y", 0))
            ptype = "thru_hole" if pad.get("type") == "thru_hole" else "smd"
            pshp  = pad.get("shape", "circle")
            sx    = float(pad.get("size_x", 1.6))
            sy    = float(pad.get("size_y", 1.6))
            pnet  = pad.get("net", "")
            nidx  = net_idx.get(pnet, 0)
            drill_str = ""
            if ptype == "thru_hole" and pad.get("drill"):
                drill_str = f' (drill {pad["drill"]})'
            lines.append(
                f'    (pad "{pnum}" {ptype} {pshp} (at {px} {py}) (size {sx} {sy}){drill_str}'
                f' (layers "*.Cu" "*.Mask") (net {nidx} "{pnet}"))'
            )
        lines.append("  )")

    # Traces
    for trace in board.get("traces", []):
        layer    = trace.get("layer", "F.Cu")
        width    = float(trace.get("width", 0.25))
        net_name = trace.get("net", "")
        nidx     = net_idx.get(net_name, 0)
        for seg in trace.get("segments", []):
            x0 = float(seg.get("start", {}).get("x", 0))
            y0 = float(seg.get("start", {}).get("y", 0))
            x1 = float(seg.get("end",   {}).get("x", 0))
            y1 = float(seg.get("end",   {}).get("y", 0))
            lines.append(
                f'  (segment (start {x0} {y0}) (end {x1} {y1})'
                f' (width {width}) (layer "{layer}") (net {nidx}))'
            )

    # Vias
    for via in board.get("vias", []):
        x    = float(via.get("x", 0))
        y    = float(via.get("y", 0))
        size = float(via.get("size", 0.8))
        drill = float(via.get("drill", 0.4))
        vnet = via.get("net", "")
        nidx = net_idx.get(vnet, 0)
        lines.append(
            f'  (via (at {x} {y}) (size {size}) (drill {drill})'
            f' (layers "F.Cu" "B.Cu") (net {nidx}))'
        )

    lines.append(")")
    return "\n".join(lines)


# ── GET /api/pcb/{bid}/export/kicad ──────────────────────────────────────────
@router.get("/pcb/{bid}/export/kicad")
async def export_kicad(bid: str):
    fpath = PCB_BOARDS_DIR / f"{bid}.json"
    if not fpath.exists():
        raise HTTPException(404, "Not found")
    board   = json.loads(fpath.read_text("utf-8"))
    content = kicad_for_board(board)
    name    = board.get("name", "board")
    safe    = re.sub(r"[^a-zA-Z0-9_\-]", "_", name)
    return StreamingResponse(
        iter([content.encode()]),
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{safe}.kicad_pcb"'},
    )


# ── POST /api/pcb/compute-ratsnest ────────────────────────────────────────────
@router.post("/pcb/compute-ratsnest")
async def compute_ratsnest(request: Request):
    body    = await request.json()
    board   = body.get("board", body)
    # netlist unused for now — we derive from board.nets + board.components
    # Build pad position lookup
    pad_pos: dict[str, tuple[float, float]] = {}
    for comp in board.get("components", []):
        ref = comp.get("ref", comp.get("id", ""))
        cx  = float(comp.get("x", 0))
        cy  = float(comp.get("y", 0))
        for pad in comp.get("pads", []):
            key = f"{ref}.{pad.get('number', pad.get('name', '?'))}"
            pad_pos[key] = (cx + float(pad.get("x", 0)), cy + float(pad.get("y", 0)))

    # Collect routed connections from traces (per net, which pad-pairs are connected)
    # Simplified: if a net has traces, consider it fully routed
    routed_nets: set[str] = set()
    for trace in board.get("traces", []):
        net = trace.get("net", "")
        if net:
            routed_nets.add(net)

    ratsnest: list[dict] = []
    for net_entry in board.get("nets", []):
        net_name = net_entry.get("name", "")
        pads_in  = [p for p in net_entry.get("pads", []) if p in pad_pos]
        if len(pads_in) < 2 or net_name in routed_nets:
            continue
        # Generate minimum spanning pairs (chain)
        remaining = list(pads_in)
        connected = [remaining.pop(0)]
        while remaining:
            best_i, best_d = 0, float("inf")
            for i, cand in enumerate(remaining):
                cx2, cy2 = pad_pos[cand]
                for src in connected:
                    sx, sy = pad_pos[src]
                    d = (cx2 - sx) ** 2 + (cy2 - sy) ** 2
                    if d < best_d:
                        best_d = d
                        best_i = i
                        best_src = src
            x1, y1 = pad_pos[best_src]
            x2, y2 = pad_pos[remaining[best_i]]
            ratsnest.append({"net": net_name, "x1": x1, "y1": y1, "x2": x2, "y2": y2})
            connected.append(remaining.pop(best_i))

    return {"ratsnest": ratsnest, "count": len(ratsnest)}


# ── POST /api/gen-tickets/build-prompt ────────────────────────────────────────
@router.post("/gen-tickets/build-prompt")
async def build_gen_ticket_prompt(request: Request):
    body        = await request.json()
    ticket_type = body.get("type", "")
    slug        = body.get("slug", "")
    profile     = body.get("profile", {})
    raw_text    = body.get("rawText", "") or ""

    part  = profile.get("part_number", slug)
    pkgs  = profile.get("package_types") or ["unknown"]
    pkg   = pkgs[0]
    desc  = profile.get("description", "")
    pins  = profile.get("pins", [])

    # Full profile as compact JSON (gives the agent everything in the profile)
    profile_json = json.dumps(profile, indent=2)

    # All pins as a readable table
    def _pin_table(pins):
        lines = []
        for p in pins:
            num  = p.get("number", "?")
            name = p.get("name", "?")
            typ  = p.get("type", "")
            desc2 = p.get("description", "")
            lines.append(f"  {num:>4}  {name:<20} {typ:<12} {desc2}")
        return "\n".join(lines) if lines else "  (none)"

    pin_table = _pin_table(pins)

    # Load existing sub-data from disk for context
    def _existing(key):
        v = profile.get(key)
        return json.dumps(v, indent=2) if v else "(not yet generated)"

    existing_footprint      = _existing("footprint")
    existing_example        = _existing("example_circuit")
    existing_layout         = _existing("layout_example")

    # Raw text — give as much as reasonably fits
    raw_snippet = raw_text[:12000] if raw_text else "(no raw datasheet text available)"
    raw_truncated = len(raw_text) > 12000

    if ticket_type == "footprint":
        prompt = f"""Generate a PCB footprint for **{part}**.

## Component profile
{profile_json}

## All pins ({len(pins)})
  NUM   NAME                 TYPE         DESCRIPTION
{pin_table}

## Existing footprint (for reference / improvement)
{existing_footprint}

## Task
Produce a complete, accurate footprint JSON for the {pkg} package.
The footprint must include:
- `name`: "{part}"
- `description`: one-line description
- `pads[]`: one entry per pin — each with `number`, `name`, `x`, `y`,
  `type` (smd|thru_hole), `shape` (circle|rect|oval), `size_x`, `size_y`,
  `drill` (thru_hole only), `layers[]`
- `courtyard`: {{x, y, w, h}} bounding box in mm
- `fab`: {{x, y, w, h}} fab outline

Use real datasheet dimensions for the {pkg} package.

## Output
PUT /api/library/{slug}/footprint
Body: the footprint JSON object above.
"""

    elif ticket_type == "example":
        prompt = f"""Build an example application circuit for **{part}**.

## Component profile
{profile_json}

## All pins ({len(pins)})
  NUM   NAME                 TYPE         DESCRIPTION
{pin_table}

## Existing example circuit (for reference / improvement)
{existing_example}

## Raw datasheet excerpt{' (truncated at 12000 chars)' if raw_truncated else ''}
{raw_snippet}

## Task
Produce a realistic, working example schematic that shows {part} in a
typical application. Include decoupling capacitors, pull-ups/pull-downs,
and any required passives. Refer to the datasheet typical application section.

The schematic JSON must include:
- `components[]`: each with `id`, `ref`, `slug`, `value`, `x`, `y`, `rotation`
- `wires[]`: each with `x1`,`y1`,`x2`,`y2`
- `labels[]` (optional): net labels with `x`,`y`,`text`
- Use standard slugs: RESISTOR, CAPACITOR, VCC, GND, plus the component slug "{slug}"

## Output
PUT /api/library/{slug}/example_circuit
Body: {{components, wires, labels}}
"""

    elif ticket_type == "layout":
        # Build library summary — slug → footprint + package info
        lib_summary_lines = []
        try:
            idx = _read_index()
            for s, meta in idx.items():
                fp = meta.get("footprint") or {}
                pkg = (meta.get("package_types") or ["?"])[0]
                pads_count = len(fp.get("pads", [])) if isinstance(fp, dict) else "?"
                lib_summary_lines.append(f"  {s:<40} pkg={pkg}  pads={pads_count}")
        except Exception:
            lib_summary_lines = ["  (could not read library index)"]
        lib_summary = "\n".join(lib_summary_lines)

        prompt = f"""Build a PCB layout example for **{part}**.

## Component profile
{profile_json}

## All pins ({len(pins)})
  NUM   NAME                 TYPE         DESCRIPTION
{pin_table}

## Existing footprint for this component
{existing_footprint}

## Existing example circuit (schematic — use these components)
{existing_example}

## Existing layout example (for reference / improvement)
{existing_layout}

## Available library components (ONLY use slugs from this list)
{lib_summary}

## Task
Produce a compact, routable PCB layout that matches the example circuit above.

**CRITICAL RULES:**
1. Every component slug used MUST exist in the library list above — never invent slugs.
2. For each component, read its footprint from `GET /api/library/<slug>` — use the
   actual pad positions, sizes, and drill values from the stored footprint. Do NOT
   make up pad data.
3. Place components sensibly (decoupling caps close to power pins, etc.)
4. Pre-route as many traces as possible.

## Workflow
1. Read `GET /api/library/{slug}` to get the main component footprint.
2. For each passive/support component in the example circuit, read `GET /api/library/<slug>`
   to get its footprint too.
3. Assemble the layout JSON using real footprint data from step 1–2.
4. PUT to the output endpoint.

The layout JSON must include:
- `components[]`: each with `id`, `ref`, `slug`, `x`, `y`, `rotation`, `layer` (F|B), `pads[]`
  — pads come from the library footprint, NOT invented
- `traces[]`: each with `net`, `layer` (F.Cu|B.Cu), `width`, `segments[]`
  where each segment has `start{{x,y}}`, `end{{x,y}}`
- `nets[]`: each with `name`, `pads[]` (list of "REF.padnum" strings)
- `board`: {{width, height, units:"mm"}}
- `vias[]` (optional): each with `x`,`y`,`net`,`drill`,`size`

## Output
PUT /api/library/{slug}/layout_example
Body: the layout JSON object above.
"""

    elif ticket_type == "datasheet":
        prompt = f"""Rebuild the component profile for **{part}** from its datasheet.

## Current profile (what is already stored — improve / complete it)
{profile_json}

## Raw datasheet text{' (truncated at 12000 chars)' if raw_truncated else ''}
{raw_snippet}

## Task
Produce a complete, accurate profile JSON. Fix any wrong or missing fields.
Required fields:
- `part_number`: the SHORT part number ONLY — e.g. "MAX2870", "LM358", "STM32F103C8".
  NO descriptions, NO series names, NO suffixes like " - 23.5MHz Synthesizer".
  If the datasheet shows "MAX2870ETJ+" use "MAX2870". Strip everything after the base number.
- `description`: one-line description (this is where the long text goes, NOT part_number)
- `symbol_type`: one of ic|npn|pnp|nmos|pmos|resistor|capacitor|inductor|diode|led|opamp|amplifier
- `package_types[]`: list of package strings (e.g. ["DIP-8", "SOIC-8"])
- `pins[]`: every pin — `number`, `name`, `type` (input|output|power|gnd|io|passive|nc), `description`
- `supply_voltage_range`: e.g. "3.0V – 5.5V"
- `max_current_ma`: number
- `required_passives[]`: {{type, value, placement}} for caps/resistors always needed
- `datasheet_url`: if known
- Any other fields already present should be preserved or corrected.

## Output
PUT /api/library/{slug}
Body: the complete profile JSON object.
"""

    else:
        prompt = f"Generate {ticket_type} for {slug}.\n\nProfile:\n{profile_json}"

    return {"prompt": prompt}


# ── Startup: rebuild index ─────────────────────────────────────────────────────
_update_index()
