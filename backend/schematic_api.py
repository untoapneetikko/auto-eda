"""
backend/schematic_api.py — Full port of schematic_designer server.js to FastAPI.

Mounts all /api/* endpoints used by index.html and pcb.html:
  /api/library, /api/footprints, /api/projects, /api/pcb-boards,
  /api/issues, /api/gen-tickets, /api/agents, /api/events,
  /api/upload, /api/build-from-netlist, /api/build-project,
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
        "part_number": slug,
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
                    permission_mode="default",
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
                    await _agent_send(agent_name, ticket["prompt"])
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

# ── IC Layout helper ──────────────────────────────────────────────────────────

def ic_layout(pins: list) -> dict:
    """Compute IC symbol geometry from a list of pin dicts (port of JS _icLayout)."""
    PIN_STUB = 40
    ROW_H    = 20
    PAD_Y    = 16
    BOX_W    = 120

    sorted_pins = sorted(pins, key=lambda p: (
        int(p.get("number", 0)) if str(p.get("number", "")).isdigit() else 0
    ))

    half = math.ceil(len(sorted_pins) / 2) if sorted_pins else 2
    left_pins  = sorted_pins[:half]
    right_pins = list(reversed(sorted_pins[half:]))

    max_rows = max(len(left_pins), len(right_pins), 1)
    BOX_H    = max_rows * ROW_H + 2 * PAD_Y

    ports = []
    for i, pin in enumerate(left_pins):
        dx = -(BOX_W / 2 + PIN_STUB)
        dy = -(BOX_H / 2 - PAD_Y) + i * ROW_H
        ports.append({"name": pin.get("name", str(pin.get("number", i + 1))), "dx": dx, "dy": dy})
    for i, pin in enumerate(right_pins):
        dx = +(BOX_W / 2 + PIN_STUB)
        dy = -(BOX_H / 2 - PAD_Y) + i * ROW_H
        ports.append({"name": pin.get("name", str(pin.get("number", i + 1))), "dx": dx, "dy": dy})

    return {
        "BOX_W":      BOX_W,
        "BOX_H":      BOX_H,
        "PIN_STUB":   PIN_STUB,
        "ROW_H":      ROW_H,
        "PAD_Y":      PAD_Y,
        "leftPins":   left_pins,
        "rightPins":  right_pins,
        "ports":      ports,
        "w":          BOX_W + 2 * PIN_STUB,
        "h":          BOX_H,
    }


# ── Netlist extraction helpers ─────────────────────────────────────────────────

_SNAP_GRID = 12

def _snap(v: float) -> int:
    return round(v / _SNAP_GRID) * _SNAP_GRID

def _pt_key(x, y) -> str:
    return f"{_snap(x)},{_snap(y)}"

# SYMDEFS port offsets (dx, dy) for each symbol type at rotation 0
_SYMDEFS: dict[str, list[tuple[str, int, int]]] = {
    # Port names MUST match the frontend SYMDEFS in index-navigation.js exactly so
    # that netlist pin references are consistent with what the canvas displays.
    "resistor":      [("P1", -30, 0), ("P2", 30, 0)],
    "capacitor":     [("P1", 0, -20), ("P2", 0, 20)],
    "capacitor_pol": [("+"  , 0, -20), ("-"  , 0, 20)],
    "inductor":      [("P1", -40, 0), ("P2", 40, 0)],
    "vcc":           [("VCC", 0, 20)],
    "gnd":           [("GND", 0, -20)],
    "diode":         [("A", -30, 0), ("K", 30, 0)],
    "led":           [("A", -30, 0), ("K", 30, 0)],
    "npn":           [("B", -30, 0), ("C", 20, -25), ("E", 20, 25)],
    "pnp":           [("B", -30, 0), ("E", 20, -25), ("C", 20, 25)],
    "nmos":          [("G", -30, 0), ("D", 20, -25), ("S", 20, 25)],
    "pmos":          [("G", -30, 0), ("D", 20, -25), ("S", 20, 25)],
    "amplifier":     [("IN", -50, 0), ("OUT", 50, 0), ("GND", 0, 40)],
    "opamp":         [("+"  , -50, -20), ("-"  , -50, 20), ("OUT", 50, 0)],
}

def _rotate_offset(dx: int, dy: int, r: int) -> tuple[int, int]:
    for _ in range(r % 4):
        dx, dy = dy, -dx
    return dx, dy

_POWER_NAMES = {"VCC", "VDD", "GND", "GND", "3V3", "5V", "12V", "AGND", "DGND", "PGND", "VSS"}

def build_netlist(components: list, wires: list, labels: list, no_connects: list | None = None) -> dict:
    """Extract netlist from schematic JSON using union-find."""

    # Union-Find
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        if parent.setdefault(x, x) != x:
            parent[x] = find(parent[x])
        return parent[x]

    def union(a: str, b: str):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    def ensure(x: str):
        parent.setdefault(x, x)

    # Load IC profiles for port computation
    def _comp_ports(comp: dict) -> list[tuple[str, int, int]]:
        """Return list of (node_id, wx, wy) for each port of a component."""
        cid  = comp.get("id", "")
        cx   = comp.get("x", 0)
        cy   = comp.get("y", 0)
        r    = comp.get("rotation", 0)
        sym  = comp.get("symType", "ic")

        offsets = _SYMDEFS.get(sym)
        if offsets:
            result = []
            for name, dx, dy in offsets:
                rdx, rdy = _rotate_offset(dx, dy, r)
                node_id = f"port::{cid}::{name}"
                result.append((node_id, cx + rdx, cy + rdy))
            return result

        # IC: load layout from profile
        slug = comp.get("slug", "")
        profile_path = LIBRARY_DIR / slug / "profile.json"
        pins: list = []
        if profile_path.exists():
            try:
                profile = json.loads(profile_path.read_text("utf-8"))
                pins = profile.get("pins", [])
            except Exception:
                pass

        layout = ic_layout(pins)
        result = []
        for port in layout["ports"]:
            rdx, rdy = _rotate_offset(int(port["dx"]), int(port["dy"]), r)
            node_id = f"port::{cid}::{port['name']}"
            result.append((node_id, cx + rdx, cy + rdy))
        return result

    # 1. Build portNodes and ptMap
    portNodes: list[tuple[str, int, int]] = []  # (node_id, wx, wy)
    ptMap: dict[str, list[str]] = {}            # key -> [node_ids]

    for comp in components:
        for node_id, wx, wy in _comp_ports(comp):
            ensure(node_id)
            portNodes.append((node_id, wx, wy))
            k = _pt_key(wx, wy)
            ptMap.setdefault(k, []).append(node_id)

    # 2. Wire nodes
    for wire in wires:
        wid = wire.get("id", id(wire))
        pts = wire.get("points", [])
        for pi, pt in enumerate(pts):
            node_id = f"wire::{wid}::{pi}"
            ensure(node_id)
            k = _pt_key(pt["x"], pt["y"])
            ptMap.setdefault(k, []).append(node_id)

    # 3. Union all nodes sharing same pt_key
    for key, nodes in ptMap.items():
        for ni in nodes[1:]:
            union(nodes[0], ni)

    # 4. Wire chain: adjacent points within each wire
    for wire in wires:
        wid = wire.get("id", id(wire))
        pts = wire.get("points", [])
        for pi in range(len(pts) - 1):
            union(f"wire::{wid}::{pi}", f"wire::{wid}::{pi + 1}")

    # 5. T-junction: wire endpoint on interior segment of another wire
    def _pt_on_segment(px, py, ax, ay, bx, by) -> bool:
        if ax == bx:  # vertical
            if _snap(px) != _snap(ax):
                return False
            miny, maxy = (ay, by) if ay < by else (by, ay)
            return miny < _snap(py) < maxy
        if ay == by:  # horizontal
            if _snap(py) != _snap(ay):
                return False
            minx, maxx = (ax, bx) if ax < bx else (bx, ax)
            return minx < _snap(px) < maxx
        return False

    wire_segs: list[tuple[str, str, int, int, int, int, int, int]] = []
    for wire in wires:
        wid = wire.get("id", id(wire))
        pts = wire.get("points", [])
        for pi in range(len(pts) - 1):
            p0, p1 = pts[pi], pts[pi + 1]
            wire_segs.append((
                wid, f"wire::{wid}::{pi}", f"wire::{wid}::{pi + 1}",
                _snap(p0["x"]), _snap(p0["y"]),
                _snap(p1["x"]), _snap(p1["y"]),
            ))

    for wire2 in wires:
        wid2 = wire2.get("id", id(wire2))
        pts2 = wire2.get("points", [])
        # check each endpoint of wire2 against interior of other wires
        for pi, pt in enumerate(pts2):
            if pi not in (0, len(pts2) - 1):
                continue  # only endpoints
            ep_node = f"wire::{wid2}::{pi}"
            px, py = _snap(pt["x"]), _snap(pt["y"])
            for seg in wire_segs:
                seg_wid = seg[0]
                if seg_wid == wid2:
                    continue
                n0, n1, ax, ay, bx, by = seg[1], seg[2], seg[3], seg[4], seg[5], seg[6]
                if _pt_on_segment(px, py, ax, ay, bx, by):
                    union(ep_node, n0)
                    union(ep_node, n1)

    # 6. Port-on-wire: comp port on interior of a wire segment
    for node_id, wx, wy in portNodes:
        px, py = _snap(wx), _snap(wy)
        for seg in wire_segs:
            n0, n1, ax, ay, bx, by = seg[1], seg[2], seg[3], seg[4], seg[5], seg[6]
            if _pt_on_segment(px, py, ax, ay, bx, by):
                union(node_id, n0)
                union(node_id, n1)

    # 7. Labels: union with wire/port at same position + same-name labels
    label_nodes: dict[str, str] = {}  # label_id -> node_id
    label_name_to_nodes: dict[str, list[str]] = {}

    for lbl in labels:
        lid = lbl.get("id", id(lbl))
        lx  = lbl.get("x", 0)
        ly  = lbl.get("y", 0)
        lname = lbl.get("name", lbl.get("text", ""))
        lnode = f"lbl::{lid}"
        ensure(lnode)
        label_nodes[str(lid)] = lnode
        # union with coincident wire/port nodes
        k = _pt_key(lx, ly)
        for other in ptMap.get(k, []):
            union(lnode, other)
        label_name_to_nodes.setdefault(lname, []).append(lnode)

    # Union all nodes with same label name
    for lname, lnodes in label_name_to_nodes.items():
        for ni in lnodes[1:]:
            union(lnodes[0], ni)

    # 8. Group portNodes by union root → nets
    root_to_ports: dict[str, list[str]] = {}
    for node_id, wx, wy in portNodes:
        r = find(node_id)
        root_to_ports.setdefault(r, []).append(node_id)

    # 9. Name nets
    def _port_designator(node_id: str, comp_map: dict) -> str:
        """Convert port::compId::pinName → DESIGNATOR.pinName"""
        parts = node_id.split("::")
        if len(parts) < 3:
            return node_id
        cid   = parts[1]
        pname = parts[2]
        comp  = comp_map.get(cid, {})
        des   = comp.get("designator") or comp.get("id", cid)
        return f"{des}.{pname}"

    comp_map = {c.get("id", ""): c for c in components}
    lbl_name_map: dict[str, str] = {}
    for lbl in labels:
        lid   = str(lbl.get("id", id(lbl)))
        lname = lbl.get("name", lbl.get("text", ""))
        lnode = label_nodes.get(lid, "")
        if lnode:
            lbl_name_map[find(lnode)] = lname

    net_counter = [0]
    named_nets: list[dict] = []
    root_to_net: dict[str, str] = {}
    used_names: set[str] = set()

    def _assign(root: str, name: str):
        root_to_net[root] = name
        used_names.add(name)

    # Priority 1: VCC/GND symbol types
    # Port names come from _SYMDEFS["vcc"] = [("VCC", …)] and _SYMDEFS["gnd"] = [("GND", …)]
    for comp in components:
        sym = comp.get("symType", "")
        cid = comp.get("id", "")
        if sym == "vcc":
            r = find(f"port::{cid}::VCC") if f"port::{cid}::VCC" in parent else None
            if r and r not in root_to_net:
                # Use comp value (e.g. "3V3", "5V") if set, otherwise "VCC"
                net_label = comp.get("value") or "VCC"
                _assign(r, net_label)
        elif sym == "gnd":
            r = find(f"port::{cid}::GND") if f"port::{cid}::GND" in parent else None
            if r and r not in root_to_net:
                _assign(r, "GND")

    # Priority 2: named power pins
    for comp in components:
        cid = comp.get("id", "")
        for node_id, wx, wy in portNodes:
            if not node_id.startswith(f"port::{cid}::"):
                continue
            pname = node_id.split("::")[-1].upper()
            if pname in _POWER_NAMES:
                r = find(node_id)
                if r not in root_to_net:
                    net_name = pname if pname not in used_names else f"{pname}_{cid}"
                    _assign(r, net_name)

    # Priority 3: wire labels
    for root, lname in lbl_name_map.items():
        if root not in root_to_net and lname:
            _assign(root, lname)

    # Priority 3b: No-Connect markers — force these ports to net "NC"
    if no_connects:
        nc_positions: set[str] = {_pt_key(nc.get("x", 0), nc.get("y", 0)) for nc in no_connects}
        for node_id, wx, wy in portNodes:
            if _pt_key(wx, wy) in nc_positions:
                r = find(node_id)
                if r not in root_to_net:
                    _assign(r, "NC")

    # Priority 4: NC check (single port, name NC or pin type nc)
    for root, ports_in in root_to_ports.items():
        if root in root_to_net:
            continue
        if len(ports_in) == 1:
            pname = ports_in[0].split("::")[-1].upper()
            if pname in ("NC", "NO_CONNECT"):
                _assign(root, "NC")

    # Priority 5: passive 1-hop propagation
    for root, ports_in in root_to_ports.items():
        if root in root_to_net:
            continue
        for pnode in ports_in:
            parts = pnode.split("::")
            if len(parts) < 3:
                continue
            cid   = parts[1]
            pname = parts[2]
            comp  = comp_map.get(cid, {})
            sym   = comp.get("symType", "ic")
            if sym not in ("resistor", "capacitor", "capacitor_pol", "inductor", "diode", "led"):
                continue
            # Check other port of same passive
            other_port = "P2" if pname == "P1" else "P1"
            other_node = f"port::{cid}::{other_port}"
            if other_node not in parent:
                other_node = f"port::{cid}::cathode" if pname == "anode" else f"port::{cid}::anode"
            if other_node in parent:
                other_root = find(other_node)
                inherited = root_to_net.get(other_root)
                if inherited:
                    _assign(root, f"{inherited}_{pname}")

    # Priority 6: auto-name remaining port-bearing nets N1, N2 …
    for root in root_to_ports:
        if root not in root_to_net:
            net_counter[0] += 1
            _assign(root, f"N{net_counter[0]}")

    # Priority 7: wire-only clusters (no component ports) – also get N-names
    for wire in wires:
        wid = wire.get("id", id(wire))
        node0 = f"wire::{wid}::0"
        if node0 not in parent:
            continue
        r = find(node0)
        if r not in root_to_net:
            net_counter[0] += 1
            _assign(r, f"N{net_counter[0]}")

    # 10. Build wireToNet – every wire now has a net name
    wire_to_net: dict[str, str] = {}
    for wire in wires:
        wid = wire.get("id", id(wire))
        node0 = f"wire::{wid}::0"
        if node0 in parent:
            r = find(node0)
            net_name = root_to_net.get(r, "")
            if net_name:
                wire_to_net[str(wid)] = net_name

    # Build namedNets (pin-reference format for PCB/downstream tools)
    for root, ports_in in root_to_ports.items():
        net_name = root_to_net.get(root, f"N?_{root[:6]}")
        pins_out = [_port_designator(n, comp_map) for n in ports_in]
        named_nets.append({"name": net_name, "pins": pins_out})

    named_nets.sort(key=lambda n: n["name"])

    # Build frontend-friendly nets list: [{name, ports:[{x,y,symType,portName}]}]
    # Used by the schematic net overlay and net panel.
    root_to_port_coords: dict[str, list[dict]] = {}
    for node_id, wx, wy in portNodes:
        r = find(node_id)
        parts = node_id.split("::")
        cid = parts[1] if len(parts) > 1 else ""
        comp = comp_map.get(cid, {})
        port_name = parts[2] if len(parts) > 2 else ""
        root_to_port_coords.setdefault(r, []).append({
            "x": int(wx), "y": int(wy),
            "symType": comp.get("symType", "ic"),
            "portName": port_name,
            "nodeId": f"{cid}::{port_name}",
        })

    nets_out: list[dict] = []
    emitted: set[str] = set()
    for root, net_name in root_to_net.items():
        if root in emitted:
            continue
        emitted.add(root)
        ports_coords = root_to_port_coords.get(root, [])
        nets_out.append({"name": net_name, "ports": ports_coords})
    nets_out.sort(key=lambda n: n["name"])

    return {"namedNets": named_nets, "nets": nets_out, "wireToNet": wire_to_net}


# ── POST /api/netlist ──────────────────────────────────────────────────────────
@router.post("/netlist")
async def extract_netlist(request: Request):
    body = await request.json()
    components  = body.get("components", [])
    wires       = body.get("wires", [])
    labels      = body.get("labels", [])
    no_connects = body.get("noConnects", [])
    result = build_netlist(components, wires, labels, no_connects)
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


# ── DRC helper ────────────────────────────────────────────────────────────────

def run_drc(board: dict) -> dict:  # noqa: C901
    """Comprehensive Design Rule Check with x/y coordinates for each violation."""
    import math
    dr              = board.get("designRules", {})
    min_clearance   = float(dr.get("clearance", 0.2))
    min_trace_w     = float(dr.get("minTraceWidth", 0.15))
    edge_clearance  = float(dr.get("edgeClearance", 0.5))
    via_size_def    = float(dr.get("viaSize", 1.0))
    via_drill_def   = float(dr.get("viaDrill", 0.6))
    min_annular     = float(dr.get("minAnnularRing", 0.15))
    bw = float(board.get("board", {}).get("width",  board.get("width",  200)))
    bh = float(board.get("board", {}).get("height", board.get("height", 200)))

    violations: list[dict] = []
    comps  = board.get("components", [])
    traces = board.get("traces", [])
    vias_l = board.get("vias", [])
    nets   = board.get("nets", [])

    # ── Helpers ───────────────────────────────────────────────────────────────
    def _pw(comp, pad):
        r = math.radians(float(comp.get("rotation", 0)))
        px, py = float(pad.get("x", 0)), float(pad.get("y", 0))
        return (round(px*math.cos(r)-py*math.sin(r)+float(comp.get("x",0)),4),
                round(px*math.sin(r)+py*math.cos(r)+float(comp.get("y",0)),4))

    def _ptsd(px, py, ax, ay, bx, by):
        dx, dy = bx-ax, by-ay
        lsq = dx*dx+dy*dy
        if lsq < 1e-8: return math.hypot(px-ax, py-ay)
        t = max(0.0, min(1.0, ((px-ax)*dx+(py-ay)*dy)/lsq))
        return math.hypot(px-(ax+t*dx), py-(ay+t*dy))

    def _ssd(a, b):
        ax1,ay1,ax2,ay2 = a; bx1,by1,bx2,by2 = b
        best, rx, ry = float('inf'), (ax1+bx1)/2, (ay1+by1)/2
        for px,py,sx1,sy1,sx2,sy2 in [
            (ax1,ay1,bx1,by1,bx2,by2),(ax2,ay2,bx1,by1,bx2,by2),
            (bx1,by1,ax1,ay1,ax2,ay2),(bx2,by2,ax1,ay1,ax2,ay2)]:
            d = _ptsd(px,py,sx1,sy1,sx2,sy2)
            if d < best: best,rx,ry = d,px,py
        for t in (0.25, 0.5, 0.75):
            mx2,my2 = ax1+t*(ax2-ax1), ay1+t*(ay2-ay1)
            d = _ptsd(mx2,my2,bx1,by1,bx2,by2)
            if d < best: best,rx,ry = d,mx2,my2
        return best, rx, ry

    # ── Build pad lookup ──────────────────────────────────────────────────────
    pad_lk: dict[str, dict] = {}
    all_pads: list[dict] = []
    for comp in comps:
        cref = comp.get("ref", comp.get("id", ""))
        clyr = "F" if comp.get("layer","F") in ("F","F.Cu") else "B"
        for pad in comp.get("pads", []):
            wx, wy = _pw(comp, pad)
            sx = float(pad.get("size_x", pad.get("width", 1.0)))
            sy = float(pad.get("size_y", pad.get("height", 1.0)))
            e = {"x":wx,"y":wy,"net":pad.get("net",""),
                 "ref":cref,"pad":str(pad.get("number","")),
                 "layer":clyr,"th":pad.get("type","smd")=="through_hole",
                 "r":max(sx,sy)/2}
            pad_lk[f"{cref}.{e['pad']}"] = e
            all_pads.append(e)

    # ── Flatten segments ──────────────────────────────────────────────────────
    all_segs: list[dict] = []
    for tr in traces:
        tn = tr.get("net",""); tl = tr.get("layer","F.Cu")
        tw = float(tr.get("width",0.25))
        for seg in tr.get("segments", []):
            s,e2 = seg.get("start",{}), seg.get("end",{})
            all_segs.append({"x1":float(s.get("x",0)),"y1":float(s.get("y",0)),
                             "x2":float(e2.get("x",0)),"y2":float(e2.get("y",0)),
                             "net":tn,"layer":tl,"width":tw})

    # ══ 1: Trace width ═════════════════════════════════════════════════════
    for sg in all_segs:
        if 0 < sg["width"] < min_trace_w:
            violations.append({"type":"TRACE_WIDTH","cat":"trace","sev":"ERROR",
                "msg":f"Trace '{sg['net']}' width {sg['width']:.3f}mm < min {min_trace_w}mm",
                "x":round((sg["x1"]+sg["x2"])/2,2),"y":round((sg["y1"]+sg["y2"])/2,2),"net":sg["net"]})

    # ══ 2: Out of bounds ═══════════════════════════════════════════════════
    for sg in all_segs:
        for px,py in [(sg["x1"],sg["y1"]),(sg["x2"],sg["y2"])]:
            if px<0 or py<0 or px>bw or py>bh:
                violations.append({"type":"OUT_OF_BOUNDS","cat":"bounds","sev":"ERROR",
                    "msg":f"Trace '{sg['net']}' outside board at ({px:.2f},{py:.2f})",
                    "x":round(px,2),"y":round(py,2),"net":sg["net"]})
    for v in vias_l:
        vx,vy = float(v.get("x",0)),float(v.get("y",0))
        if vx<0 or vy<0 or vx>bw or vy>bh:
            violations.append({"type":"OUT_OF_BOUNDS","cat":"bounds","sev":"ERROR",
                "msg":f"Via outside board at ({vx:.2f},{vy:.2f})",
                "x":round(vx,2),"y":round(vy,2),"net":v.get("net","")})

    # ══ 3: Edge clearance ═════════════════════════════════════════════════
    for sg in all_segs:
        hw = sg["width"]/2
        for px,py in [(sg["x1"],sg["y1"]),(sg["x2"],sg["y2"])]:
            de = min(px, py, bw-px, bh-py) - hw
            if 0<=px<=bw and 0<=py<=bh and de < edge_clearance:
                violations.append({"type":"EDGE_CLEARANCE","cat":"clearance","sev":"WARNING",
                    "msg":f"Trace '{sg['net']}' {de:.2f}mm from edge (min {edge_clearance}mm)",
                    "x":round(px,2),"y":round(py,2),"net":sg["net"]})
                break

    # ══ 4: Unconnected nets (union-find, same as frontend ratsnest) ═════
    npads: dict[str,list[dict]] = {}
    for ne in nets:
        nn = ne.get("name","")
        if not nn: continue
        pl = [pad_lk[str(pk)] for pk in ne.get("pads",[]) if str(pk) in pad_lk]
        if len(pl)>=2: npads[nn]=pl

    EPS = 0.05
    for nn, pl in npads.items():
        n = len(pl)
        tn_nodes: list[tuple] = []; tp_arr: list[int] = []; ni_map: dict[str,int] = {}
        def _tf2(i, _tp=tp_arr):
            while _tp[i]!=i: _tp[i]=_tp[_tp[i]]; i=_tp[i]
            return i
        def _tu2(a,b, _tp=tp_arr): _tp[_tf2(a,_tp)]=_tf2(b,_tp)
        def _ga2(x,y, _ni=ni_map, _tn=tn_nodes, _tp=tp_arr):
            k=f"{x:.4f},{y:.4f}"
            if k not in _ni: _ni[k]=len(_tn); _tn.append((x,y)); _tp.append(len(_tn)-1)
            return _ni[k]
        for sg in all_segs:
            if sg["net"]!=nn: continue
            _tu2(_ga2(sg["x1"],sg["y1"]), _ga2(sg["x2"],sg["y2"]))
        for v in vias_l:
            if v.get("net")!=nn: continue
            _ga2(float(v.get("x",0)),float(v.get("y",0)))
        tot = n+len(tn_nodes)
        ap2 = list(range(tot))
        def _af2(i, _ap=ap2):
            while _ap[i]!=i: _ap[i]=_ap[_ap[i]]; i=_ap[i]
            return i
        def _aun2(a,b, _ap=ap2): _ap[_af2(a,_ap)]=_af2(b,_ap)
        for i in range(len(tn_nodes)):
            r2=_tf2(i)
            if r2!=i: _aun2(n+i,n+r2)
        for pi in range(n):
            for ti in range(len(tn_nodes)):
                if math.hypot(pl[pi]["x"]-tn_nodes[ti][0],pl[pi]["y"]-tn_nodes[ti][1])<=EPS:
                    _aun2(pi,n+ti)
        cl2: dict[int,list[int]] = {}
        for i in range(n): cl2.setdefault(_af2(i),[]).append(i)
        cls2 = list(cl2.values())
        if len(cls2)<=1: continue
        inm2=[False]*len(cls2); inm2[0]=True
        for _ in range(len(cls2)-1):
            bd2,ba2,bb2,bc3=float('inf'),-1,-1,-1
            for ci in range(len(cls2)):
                if not inm2[ci]: continue
                for cj in range(len(cls2)):
                    if inm2[cj]: continue
                    for ia in cls2[ci]:
                        for ib in cls2[cj]:
                            d2=math.hypot(pl[ia]["x"]-pl[ib]["x"],pl[ia]["y"]-pl[ib]["y"])
                            if d2<bd2: bd2,ba2,bb2,bc3=d2,ia,ib,cj
            if bc3!=-1: inm2[bc3]=True
            if ba2!=-1:
                pa,pb=pl[ba2],pl[bb2]
                violations.append({"type":"UNCONNECTED","cat":"unconnected","sev":"ERROR",
                    "msg":f"Net '{nn}': {pa['ref']}.{pa['pad']} \u2194 {pb['ref']}.{pb['pad']} not connected",
                    "x":round((pa["x"]+pb["x"])/2,2),"y":round((pa["y"]+pb["y"])/2,2),"net":nn,
                    "x1":round(pa["x"],2),"y1":round(pa["y"],2),
                    "x2":round(pb["x"],2),"y2":round(pb["y"],2)})

    # ══ 5: Copper clearance — different-net traces ═════════════════════════
    seen_p: set = set()
    for i,sa in enumerate(all_segs):
        for j in range(i+1,len(all_segs)):
            sb=all_segs[j]
            if sa["layer"]!=sb["layer"]: continue
            if sa["net"] and sb["net"] and sa["net"]==sb["net"]: continue
            mg=min_clearance+max(sa["width"],sb["width"])
            if (min(sa["x1"],sa["x2"])-mg>max(sb["x1"],sb["x2"]) or
                max(sa["x1"],sa["x2"])+mg<min(sb["x1"],sb["x2"]) or
                min(sa["y1"],sa["y2"])-mg>max(sb["y1"],sb["y2"]) or
                max(sa["y1"],sa["y2"])+mg<min(sb["y1"],sb["y2"])): continue
            d3,mx3,my3=_ssd((sa["x1"],sa["y1"],sa["x2"],sa["y2"]),
                            (sb["x1"],sb["y1"],sb["x2"],sb["y2"]))
            rq=min_clearance+sa["width"]/2+sb["width"]/2
            if d3<rq:
                pk2=(min(i,j),max(i,j))
                if pk2 in seen_p: continue
                seen_p.add(pk2)
                violations.append({"type":"CLEARANCE","cat":"clearance","sev":"ERROR",
                    "msg":f"Traces '{sa['net']}'/'{sb['net']}' clearance {d3:.3f}mm < {rq:.3f}mm",
                    "x":round(mx3,2),"y":round(my3,2)})

    # ══ 6: Trace-to-pad clearance (different nets) ═════════════════════════
    for sg in all_segs:
        sl="F" if sg["layer"].startswith("F") else "B"
        for pad in all_pads:
            if not pad["th"] and pad["layer"]!=sl: continue
            if sg["net"] and pad["net"] and sg["net"]==pad["net"]: continue
            d4=_ptsd(pad["x"],pad["y"],sg["x1"],sg["y1"],sg["x2"],sg["y2"])
            rq2=min_clearance+sg["width"]/2+pad["r"]
            if d4<rq2:
                violations.append({"type":"PAD_CLEARANCE","cat":"clearance","sev":"ERROR",
                    "msg":f"Trace '{sg['net']}' too close to {pad['ref']}.{pad['pad']} ('{pad['net']}') \u2014 {d4:.3f}mm",
                    "x":round(pad["x"],2),"y":round(pad["y"],2)})

    # ══ 7: Via annular ring ════════════════════════════════════════════════
    for v in vias_l:
        vs=float(v.get("size",via_size_def)); vd=float(v.get("drill",via_drill_def))
        ring=(vs-vd)/2
        if ring<min_annular:
            violations.append({"type":"ANNULAR_RING","cat":"via","sev":"ERROR",
                "msg":f"Via annular ring {ring:.3f}mm < min {min_annular}mm",
                "x":round(float(v.get("x",0)),2),"y":round(float(v.get("y",0)),2),
                "net":v.get("net","")})

    # ══ 8: Duplicate references ════════════════════════════════════════════
    rseen: dict[str,bool] = {}
    for comp in comps:
        ref=comp.get("ref",comp.get("id",""))
        if ref in rseen:
            violations.append({"type":"DUPLICATE_REF","cat":"refs","sev":"ERROR",
                "msg":f"Duplicate reference '{ref}'",
                "x":round(float(comp.get("x",0)),2),"y":round(float(comp.get("y",0)),2)})
        else: rseen[ref]=True

    # ══ 9: Unassigned pins ═════════════════════════════════════════════════
    asgn: set[str] = set()
    for ne in nets:
        for pk3 in ne.get("pads",[]): asgn.add(str(pk3))
    for pad in all_pads:
        pk4=f"{pad['ref']}.{pad['pad']}"
        if pk4 not in asgn and not pad["net"]:
            violations.append({"type":"UNASSIGNED_PIN","cat":"unconnected","sev":"WARNING",
                "msg":f"Pin {pk4} has no net assignment",
                "x":round(pad["x"],2),"y":round(pad["y"],2)})

    # ══ 10: Net conflict — different-net pads overlapping ══════════════════
    for i,pa in enumerate(all_pads):
        for j in range(i+1,len(all_pads)):
            pb=all_pads[j]
            if not pa["th"] and not pb["th"] and pa["layer"]!=pb["layer"]: continue
            if pa["net"] and pb["net"] and pa["net"]!=pb["net"]:
                d5=math.hypot(pa["x"]-pb["x"],pa["y"]-pb["y"])
                if d5<pa["r"]+pb["r"]:
                    violations.append({"type":"NET_CONFLICT","cat":"net","sev":"ERROR",
                        "msg":f"{pa['ref']}.{pa['pad']} ({pa['net']}) / {pb['ref']}.{pb['pad']} ({pb['net']}) overlap",
                        "x":round((pa["x"]+pb["x"])/2,2),"y":round((pa["y"]+pb["y"])/2,2)})

    return {"violations": violations, "passed": len(violations) == 0}


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


# ── Autoroute helper ──────────────────────────────────────────────────────────

def _autoroute_skip_net(net_name: str, route_gnd: bool = False) -> bool:
    """
    Return True for nets that must NOT be routed as individual traces.
    - GND and all variants (AGND, PGND, DGND, GND_*) → handled by copper pour
      UNLESS route_gnd=True (no copper pour exists for GND)
    - NC / no-connect nets → intentionally floating
    - Empty net name
    """
    if not net_name:
        return True
    upper = net_name.upper().lstrip("/\\~ ")
    for prefix in ("NC", "PWR_FLAG"):
        if upper == prefix or upper.startswith(prefix + "_") or upper.startswith(prefix + "-"):
            return True
    for sub in ("UNCONNECTED", "NOCONNECT", "NO_CONNECT"):
        if sub in upper:
            return True
    # GND: skip only if copper pour exists for it
    if not route_gnd:
        for sub in ("GND",):
            if sub in upper:
                return True
    return False


def _autoroute_trace_width(net_name: str, dr: dict) -> float:
    """Return trace width in mm for a given net (power nets get wider traces)."""
    default_w = float(dr.get("traceWidth", dr.get("minTraceWidth", 0.25)))
    upper = net_name.upper()
    # Power rails get at minimum 0.4 mm (IPC-2221 ~1 A)
    power_keywords = ("VCC", "VDD", "VIN", "VBAT", "VBUS", "3V3", "5V", "12V",
                      "24V", "AVCC", "DVCC", "VPW", "VMOT", "VPWR", "PWR")
    if any(kw in upper for kw in power_keywords):
        pwr_w = float(dr.get("powerTraceWidth", 0.4))
        return max(default_w, pwr_w)
    return default_w


def run_autoroute(board: dict) -> dict:
    dr   = board.get("designRules", {})
    GRID = max(0.1, float(dr.get("routingGrid", 0.25)))  # mm routing grid
    bw   = float(board.get("board", {}).get("width",  board.get("width",  200)))

    # ── Detect whether GND has a copper pour / zone ────────────────────────
    # If no zone covers GND, route it as normal traces instead of skipping.
    _gnd_has_zone = False
    for _zone in board.get("zones", board.get("areas", [])):
        _zn = (_zone.get("net", "") or "").upper()
        if "GND" in _zn:
            _gnd_has_zone = True
            break
    _route_gnd = not _gnd_has_zone
    bh   = float(board.get("board", {}).get("height", board.get("height", 200)))
    cu_clearance = float(dr.get("clearance", 0.2))  # copper-to-copper clearance
    edge_clearance = float(dr.get("routeEdgeClearance", dr.get("edgeClearance", 0.5)))

    # ── Occupancy grid: mark cells covered by existing traces ────────────────
    grid_w = int(bw / GRID) + 2
    grid_h = int(bh / GRID) + 2
    occupied: set[tuple[int, int]] = set()

    # Trace clearance expansion: each trace line is expanded by
    # half-trace-width + copper clearance on each side.
    _default_trace_w = float(dr.get("traceWidth", 0.25))
    _trace_expand = max(1, int(math.ceil((_default_trace_w / 2 + cu_clearance) / GRID)))

    def _mark_segment(x0: float, y0: float, x1: float, y1: float,
                      occ: set[tuple[int, int]] | None = None) -> None:
        """Mark grid cells along a segment, expanded by trace clearance."""
        target = occ if occ is not None else occupied
        steps = max(int(abs(x1 - x0) / GRID), int(abs(y1 - y0) / GRID)) + 1
        for s in range(steps + 1):
            t = s / max(steps, 1)
            gx = int(round((x0 + t * (x1 - x0)) / GRID))
            gy = int(round((y0 + t * (y1 - y0)) / GRID))
            for ex in range(-_trace_expand, _trace_expand + 1):
                for ey in range(-_trace_expand, _trace_expand + 1):
                    target.add((gx + ex, gy + ey))

    for trace in board.get("traces", []):
        for seg in trace.get("segments", []):
            _mark_segment(
                seg.get("start", {}).get("x", 0), seg.get("start", {}).get("y", 0),
                seg.get("end",   {}).get("x", 0), seg.get("end",   {}).get("y", 0),
            )

    # ── Block board edges (route edge clearance) ──────────────────────────────
    edge_cells = max(1, int(math.ceil(edge_clearance / GRID)))
    for gx in range(grid_w):
        for gy in range(edge_cells):
            occupied.add((gx, gy))                     # top edge
            occupied.add((gx, grid_h - 1 - gy))        # bottom edge
    for gy in range(grid_h):
        for gx in range(edge_cells):
            occupied.add((gx, gy))                     # left edge
            occupied.add((grid_w - 1 - gx, gy))        # right edge

    # ── Overwrite pad.net from board.nets[] array (authoritative source) ─────
    # The nets[] array contains the real net connectivity from the schematic.
    # Pad.net values may be stale generic names (N1, N2…) that don't reflect
    # the actual grouping.  Always prefer the nets[] array mapping.
    _pad_ref_to_net_ar: dict[str, str] = {}  # "L1.1" → "RF_IN"
    for _net_entry in board.get("nets", []):
        _nn = (_net_entry.get("name", "") or "").upper()
        if not _nn:
            continue
        for _pr in _net_entry.get("pads", []):
            _pad_ref_to_net_ar[_pr.upper()] = _nn

    # ── Fallback: compute netlist from schematic project if board has no nets ──
    # When a board was imported without a netlist, pad.net and board.nets[]
    # are both empty.  Load the schematic project and run build_netlist()
    # to recover net connectivity from schematic wires.
    if not _pad_ref_to_net_ar:
        project_id = board.get("projectId", "")
        if project_id:
            _proj_path = Path(os.getenv("PROJECTS_DIR",
                              str(PROJECT_ROOT_SA / "frontend" / "static" / "projects"))
                             ) / f"{project_id}.json"
            if _proj_path.exists():
                try:
                    _proj = json.loads(_proj_path.read_text("utf-8"))
                    _netlist_result = build_netlist(
                        _proj.get("components", []),
                        _proj.get("wires", []),
                        _proj.get("labels", []),
                        _proj.get("noConnects", []),
                    )
                    # build_netlist returns {namedNets: [{name, pins: ["C1.P1", ...]}, ...]}
                    # Netlist pins use schematic pin names ("U1.RF-IN", "C1.P2").
                    # Board pads use different names ("+" / "-" / "A" / "B") and numbers (1, 2).
                    # Build multiple lookup paths to translate:
                    #   1. Pad name match: "U1.RF-IN" → board U1 pad named "RF-IN" → "U1.2"
                    #   2. Pn→number: "C1.P2" → "C1.2" (for generic schematic pin names)
                    #   3. Direct number: "C1.2" → "C1.2"
                    _pin_to_board_key: dict[str, str] = {}  # netlist pin ref → board "REF.NUM"
                    for _bc in board.get("components", []):
                        _bc_ref = _bc.get("ref", "")
                        for _bp in _bc.get("pads", []):
                            _bp_name = (_bp.get("name", "") or "").upper()
                            _bp_num  = str(_bp.get("number", ""))
                            _board_key = f"{_bc_ref}.{_bp_num}".upper()
                            # Map by pad name (e.g. "U1.RF-IN" → "U1.2")
                            if _bp_name and _bc_ref:
                                _pin_to_board_key[f"{_bc_ref}.{_bp_name}"] = _board_key
                                # Also try cleaned version (RF-IN → RFIN)
                                _clean = _bp_name.replace("-", "").replace(" ", "").replace("(", "").replace(")", "").replace("!", "").replace("/", "")
                                if _clean != _bp_name:
                                    _pin_to_board_key[f"{_bc_ref}.{_clean}"] = _board_key
                            # Map Pn notation: "C1.P1"→"C1.1", "C1.P2"→"C1.2"
                            _pin_to_board_key[f"{_bc_ref}.P{_bp_num}"] = _board_key
                            # Direct number
                            _pin_to_board_key[_board_key] = _board_key

                    _named_nets = _netlist_result.get("namedNets", [])
                    if isinstance(_named_nets, list):
                        for _net_entry in _named_nets:
                            _nn_name = str(_net_entry.get("name", "")).upper()
                            if not _nn_name:
                                continue
                            for _pin_ref in _net_entry.get("pins", []):
                                _pr_upper = str(_pin_ref).upper()
                                # Skip power/GND symbol pins (e.g. "GND.GND", "VDD.VCC")
                                _pr_parts = _pr_upper.split(".", 1)
                                if len(_pr_parts) == 2 and _pr_parts[0] in (
                                    "GND", "GND1", "GND2", "VDD", "VCC", "PWR"):
                                    continue
                                _board_key = _pin_to_board_key.get(_pr_upper)
                                if _board_key:
                                    _pad_ref_to_net_ar[_board_key] = _nn_name
                except Exception:
                    pass  # silently continue without project nets

    # Build old→new net name map so we can also fix existing trace net names
    _old_to_new_net: dict[str, str] = {}
    if _pad_ref_to_net_ar:
        for comp in board.get("components", []):
            ref = comp.get("ref", comp.get("id", ""))
            for pad in comp.get("pads", []):
                pnum = pad.get("number", pad.get("name", ""))
                pkey = f"{ref}.{pnum}".upper()
                if pkey in _pad_ref_to_net_ar:
                    old_net = (pad.get("net", "") or "").upper()
                    new_net = _pad_ref_to_net_ar[pkey]
                    if old_net and old_net != new_net:
                        _old_to_new_net[old_net] = new_net
                    # Always overwrite — nets[] array is authoritative over
                    # stale pad.net values like "N1", "N2" etc.
                    pad["net"] = new_net

        # Also normalize existing trace net names (e.g. "N1" → "RF_IN")
        for trace in board.get("traces", []):
            tn = (trace.get("net", "") or "").upper()
            if tn in _old_to_new_net:
                trace["net"] = _old_to_new_net[tn]

    # ── Build net → [(x, y)] map from board.components[].pads[].net ──────────
    # Pad world position accounts for component rotation.
    # Pads with name "NC" (No Connect) are excluded — they must not be routed.
    # net_pads stores (x, y, layer_index) — layer_index: 0=F.Cu, 1=B.Cu
    net_pads: dict[str, list[tuple[float, float, int]]] = {}
    nc_pad_positions: list[tuple[float, float]] = []  # blocked zones around NC pins
    nc_nets: set[str] = set()  # nets that contain at least one NC-named pad
    _net_has_real_pad: set[str] = set()  # nets that have at least one non-NC pad
    _NC_EXACT = {"NC", "N/C", "N.C.", "NOCONNECT", "NO_CONNECT", "NO CONNECT"}
    _NC_PREFIXES = ("NC", "N/C", "NOCONNECT", "NO_CONNECT")
    def _is_nc(name: str, net: str) -> bool:
        """Check if a pad is No-Connect by name or net."""
        n = name.upper().strip()
        t = net.upper().strip()
        if n in _NC_EXACT or t in _NC_EXACT:
            return True
        # Also match names like "NC (float!)", "NC_1", etc.
        for pfx in _NC_PREFIXES:
            if n.startswith(pfx + " ") or n.startswith(pfx + "(") or n.startswith(pfx + "_"):
                return True
            if t.startswith(pfx + " ") or t.startswith(pfx + "(") or t.startswith(pfx + "_"):
                return True
        return False

    # Track NC pad cells per component (for unblocking when routing through component)
    _nc_cells_by_comp: dict[str, set[tuple[int, int]]] = {}  # comp_ref → NC pad grid cells
    _comp_nets: dict[str, set[str]] = {}  # comp_ref → set of nets on this component

    for comp in board.get("components", []):
        cx  = float(comp.get("x", 0))
        cy  = float(comp.get("y", 0))
        rot = float(comp.get("rotation", 0)) * math.pi / 180
        cos_r, sin_r = math.cos(rot), math.sin(rot)
        comp_layer = 1 if comp.get("layer", "F") == "B" else 0  # 0=F.Cu, 1=B.Cu
        comp_ref = comp.get("ref", comp.get("id", ""))
        for pad in comp.get("pads", []):
            net = (pad.get("net", "") or "").upper()
            # Rotate local pad offset into world space
            lx = float(pad.get("x", 0))
            ly = float(pad.get("y", 0))
            px = cx + lx * cos_r - ly * sin_r
            py = cy + lx * sin_r + ly * cos_r
            pad_name = (pad.get("name", "") or "").upper().strip()
            is_nc_pad = _is_nc(pad_name, net)
            if is_nc_pad:
                # Mark NC pad position as blocked — no trace may pass through
                nc_pad_positions.append((px, py))
                # Track NC cells per component for selective unblocking
                if comp_ref:
                    _nc_cells_by_comp.setdefault(comp_ref, set())
                if net:
                    nc_nets.add(net)
                continue  # NEVER add NC pads to net_pads
            # Track which nets each component has (for NC unblocking)
            if net and comp_ref:
                _comp_nets.setdefault(comp_ref, set()).add(net)
            if not net:
                continue
            # Through-hole pads exist on both layers; SMD pads on their component's layer
            pad_layer = comp_layer
            if pad.get("type") == "thru_hole":
                pad_layer = 0  # through-hole: accessible from F.Cu (BFS starts here)
            _net_has_real_pad.add(net)
            net_pads.setdefault(net, []).append((px, py, pad_layer))
    # Nets where ALL pads are NC should be skipped
    nc_only_nets = nc_nets - _net_has_real_pad

    # ── Block occupancy around ALL pad positions ─────────────────────────────
    # Traces must not pass through pads of other nets.  For each net being
    # routed, we temporarily unblock its own pads before calling BFS.
    # Block zone = pad physical extent + copper clearance.

    def _pad_cells(px: float, py: float,
                   half_w: float = 0.0, half_h: float = 0.0) -> set[tuple[int, int]]:
        """Grid cells blocked by a pad at (px,py).
        Expansion = pad half-extent + copper clearance + trace half-width,
        so the trace EDGE (not just centerline) maintains clearance from the pad EDGE."""
        rx = max(1, int(math.ceil((half_w + cu_clearance + _default_trace_w / 2) / GRID)))
        ry = max(1, int(math.ceil((half_h + cu_clearance + _default_trace_w / 2) / GRID)))
        gc_x = int(round(px / GRID))
        gc_y = int(round(py / GRID))
        cells: set[tuple[int, int]] = set()
        for dx in range(-rx, rx + 1):
            for dy in range(-ry, ry + 1):
                cells.add((gc_x + dx, gc_y + dy))
        return cells

    # Collect pad sizes for accurate blocking
    _pad_hw_map: dict[tuple[float, float], tuple[float, float]] = {}  # (px,py) -> (hw, hh)
    for comp in board.get("components", []):
        cx  = float(comp.get("x", 0))
        cy  = float(comp.get("y", 0))
        rot = float(comp.get("rotation", 0)) * math.pi / 180
        cos_r, sin_r = math.cos(rot), math.sin(rot)
        for pad in comp.get("pads", []):
            lx = float(pad.get("x", 0))
            ly = float(pad.get("y", 0))
            px = cx + lx * cos_r - ly * sin_r
            py = cy + lx * sin_r + ly * cos_r
            sx = float(pad.get("size_x", pad.get("sizeX", 0.5)))
            sy = float(pad.get("size_y", pad.get("sizeY", 0.5)))
            # Rotate pad extents
            if abs(rot) > 0.01:
                hw = max(abs(sx * cos_r), abs(sy * sin_r)) / 2
                hh = max(abs(sx * sin_r), abs(sy * cos_r)) / 2
            else:
                hw, hh = sx / 2, sy / 2
            _pad_hw_map[(round(px, 4), round(py, 4))] = (hw, hh)

    def _get_pad_hw(px: float, py: float) -> tuple[float, float]:
        return _pad_hw_map.get((round(px, 4), round(py, 4)), (0.25, 0.25))

    # ── Block component bodies (the rectangle spanning all pads of each part) ──
    # Traces must not route through the body area between pads of a component.
    # For each component, compute the bounding box of all its pads (+ clearance)
    # and block those cells.  Cells are added to a per-net dict so they can be
    # temporarily unblocked when routing that net (same logic as individual pads).
    _comp_body_cells: dict[str, set[tuple[int, int]]] = {}  # net_name → cells from comp bodies
    for comp in board.get("components", []):
        cx  = float(comp.get("x", 0))
        cy  = float(comp.get("y", 0))
        rot = float(comp.get("rotation", 0)) * math.pi / 180
        cos_r, sin_r = math.cos(rot), math.sin(rot)
        comp_pads = comp.get("pads", [])
        if len(comp_pads) < 2:
            continue
        # Compute world positions + extents of all pads
        pad_positions: list[tuple[float, float, float, float]] = []  # (wx, wy, hw, hh)
        comp_net_names: set[str] = set()
        for pad in comp_pads:
            lx = float(pad.get("x", 0))
            ly = float(pad.get("y", 0))
            wx = cx + lx * cos_r - ly * sin_r
            wy = cy + lx * sin_r + ly * cos_r
            sx = float(pad.get("size_x", pad.get("sizeX", 0.5)))
            sy = float(pad.get("size_y", pad.get("sizeY", 0.5)))
            if abs(rot) > 0.01:
                hw = max(abs(sx * cos_r), abs(sy * sin_r)) / 2
                hh = max(abs(sx * sin_r), abs(sy * cos_r)) / 2
            else:
                hw, hh = sx / 2, sy / 2
            pad_positions.append((wx, wy, hw, hh))
            net = (pad.get("net", "") or "").upper()
            if net:
                comp_net_names.add(net)
        # Bounding box of all pad edges
        min_x = min(wx - hw for wx, wy, hw, hh in pad_positions)
        max_x = max(wx + hw for wx, wy, hw, hh in pad_positions)
        min_y = min(wy - hh for wx, wy, hw, hh in pad_positions)
        max_y = max(wy + hh for wx, wy, hw, hh in pad_positions)
        # Expand by clearance + trace half-width
        exp = cu_clearance + _default_trace_w / 2
        min_x -= exp; max_x += exp; min_y -= exp; max_y += exp
        # Convert to grid cells
        gx0 = int(math.floor(min_x / GRID))
        gx1 = int(math.ceil(max_x / GRID))
        gy0 = int(math.floor(min_y / GRID))
        gy1 = int(math.ceil(max_y / GRID))
        body_cells: set[tuple[int, int]] = set()
        for gx in range(gx0, gx1 + 1):
            for gy in range(gy0, gy1 + 1):
                body_cells.add((gx, gy))
        # Add body cells to each net this component touches, so they get
        # unblocked when routing that net (BFS needs to reach the pads)
        for net in comp_net_names:
            _comp_body_cells.setdefault(net, set()).update(body_cells)
        # Block on F.Cu occupancy
        occupied |= body_cells

    # Block NC pads on F.Cu (B.Cu blocking added after occupied_b is created)
    _nc_blocked_cells: set[tuple[int, int]] = set()
    for ncx, ncy in nc_pad_positions:
        hw, hh = _get_pad_hw(ncx, ncy)
        nc_cells = _pad_cells(ncx, ncy, hw, hh)
        _nc_blocked_cells |= nc_cells
    occupied |= _nc_blocked_cells

    # Populate NC cells per component (now that _pad_cells is defined)
    # Re-scan components to get per-comp NC cell sets
    for comp in board.get("components", []):
        cx  = float(comp.get("x", 0))
        cy  = float(comp.get("y", 0))
        rot = float(comp.get("rotation", 0)) * math.pi / 180
        cos_r, sin_r = math.cos(rot), math.sin(rot)
        comp_ref = comp.get("ref", comp.get("id", ""))
        for pad in comp.get("pads", []):
            pad_name = (pad.get("name", "") or "").upper().strip()
            net = (pad.get("net", "") or "").upper()
            if _is_nc(pad_name, net):
                lx = float(pad.get("x", 0))
                ly = float(pad.get("y", 0))
                px = cx + lx * cos_r - ly * sin_r
                py = cy + lx * sin_r + ly * cos_r
                hw, hh = _get_pad_hw(px, py)
                nc_cells = _pad_cells(px, py, hw, hh)
                _nc_cells_by_comp.setdefault(comp_ref, set()).update(nc_cells)

    # Build net → set of NC cells that can be unblocked (from same-component NC pads)
    _nc_unblock_for_net: dict[str, set[tuple[int, int]]] = {}
    for comp_ref, comp_net_set in _comp_nets.items():
        nc_cells_comp = _nc_cells_by_comp.get(comp_ref, set())
        if nc_cells_comp:
            for net_name_cn in comp_net_set:
                _nc_unblock_for_net.setdefault(net_name_cn, set()).update(nc_cells_comp)

    # Block all routable pads (will be temporarily unblocked per-net)
    all_pad_cells: dict[str, set[tuple[int, int]]] = {}
    # NO-VIA zone: vias must NEVER be placed on any pad (even same-net pads)
    _no_via_cells: set[tuple[int, int]] = set()
    for net_name_p, pads_p in net_pads.items():
        cells: set[tuple[int, int]] = set()
        for pad_tuple in pads_p:
            px, py = pad_tuple[0], pad_tuple[1]
            hw, hh = _get_pad_hw(px, py)
            pad_cells = _pad_cells(px, py, hw, hh)
            cells |= pad_cells
            _no_via_cells |= pad_cells
        all_pad_cells[net_name_p] = cells
        occupied |= cells
    # Also block NC pad positions for vias
    for ncx, ncy in nc_pad_positions:
        hw, hh = _get_pad_hw(ncx, ncy)
        _no_via_cells |= _pad_cells(ncx, ncy, hw, hh)
    # Also block component body areas for vias
    for _body_net, _body_set in _comp_body_cells.items():
        _no_via_cells |= _body_set

    # ── Multi-layer A* with via support ──────────────────────────────────────
    # Layer 0 = F.Cu, Layer 1 = B.Cu.  A via transition costs VIA_PENALTY
    # extra grid steps to discourage unnecessary layer changes.
    # Cardinal + diagonal directions (diagonal cost = 3 ≈ 2*√2, cardinal = 2)
    DIRS = [(1, 0, 2), (-1, 0, 2), (0, 1, 2), (0, -1, 2),
            (1, 1, 3), (1, -1, 3), (-1, 1, 3), (-1, -1, 3)]
    LAYERS = ("F.Cu", "B.Cu")
    occupied_b: set[tuple[int, int]] = set()  # B.Cu occupancy (starts empty)
    # Block NC pads and all routable pads on B.Cu too
    occupied_b |= _nc_blocked_cells
    occupied_b |= _no_via_cells  # all pad positions blocked on B.Cu as well
    _occupied_by_layer = [occupied, occupied_b]
    via_size_mm = float(dr.get("viaSize", 1.0))
    via_drill_mm = float(dr.get("viaDrill", 0.6))
    VIA_PENALTY = max(4, int(via_size_mm / GRID))  # discourage gratuitous vias
    allow_vias = bool(dr.get("allowVias", True))

    def _bfs(sx: float, sy: float, ex: float, ey: float,
             force_single_layer: bool = False,
             start_layer: int = 0,
             relaxed: bool = False,
             ) -> tuple[list[tuple[float, float, int]], bool]:
        """A* on 2-layer grid. Returns (path with layer info, used_via).

        Each path element is (world_x, world_y, layer_index).
        If allow_vias is False or force_single_layer is True, only searches
        the start_layer (no layer transitions).
        If relaxed=True, ignores component body blocking (for tight layouts).
        """
        sg = (int(round(sx / GRID)), int(round(sy / GRID)), start_layer)
        # End must be reachable on either layer (pads are through-hole or SMD on F.Cu)
        eg_xy = (int(round(ex / GRID)), int(round(ey / GRID)))
        if sg[:2] == eg_xy:
            return [(sx, sy, start_layer), (ex, ey, start_layer)], False

        # A* heuristic: octile distance (accounts for diagonal moves)
        def _h(gx: int, gy: int) -> int:
            dx_h = abs(gx - eg_xy[0])
            dy_h = abs(gy - eg_xy[1])
            # Cardinal cost=2, diagonal cost=3: h = 2*max + 1*min (octile)
            return 2 * max(dx_h, dy_h) + 1 * min(dx_h, dy_h)

        g_cost: dict[tuple[int, int, int], int] = {sg: 0}
        prev: dict[tuple[int, int, int], tuple[int, int, int]] = {}
        pq: list = [(_h(sg[0], sg[1]), 0, sg)]  # (f, g, node)
        n_layers = 2 if (allow_vias and not force_single_layer) else 1
        _max_visits = grid_w * grid_h * n_layers * 2  # safety cap

        visited = 0
        while pq:
            f, g, cur = heapq.heappop(pq)
            visited += 1
            if visited > _max_visits:
                break
            if g > g_cost.get(cur, 10**9):
                continue
            if cur[0] == eg_xy[0] and cur[1] == eg_xy[1]:
                # Reached target — reconstruct path
                path: list[tuple[float, float, int]] = []
                node = cur
                while node in prev:
                    path.append((node[0] * GRID, node[1] * GRID, node[2]))
                    node = prev[node]
                path.append((sg[0] * GRID, sg[1] * GRID, sg[2]))
                path.reverse()
                if path:
                    path[0] = (sx, sy, path[0][2])
                    path.append((ex, ey, path[-1][2]))
                used_via = len(set(p[2] for p in path)) > 1
                return path, used_via

            layer = cur[2]
            occ = _occupied_by_layer[layer]

            # Cardinal + diagonal moves on same layer
            for dx, dy, cost in DIRS:
                nxt = (cur[0] + dx, cur[1] + dy, layer)
                if nxt[0] < 0 or nxt[1] < 0 or nxt[0] >= grid_w or nxt[1] >= grid_h:
                    continue
                if (nxt[0], nxt[1]) in occ:
                    continue
                ng = g + cost
                if ng < g_cost.get(nxt, 10**9):
                    g_cost[nxt] = ng
                    prev[nxt] = cur
                    heapq.heappush(pq, (ng + _h(nxt[0], nxt[1]), ng, nxt))

            # Via transition to other layer (NEVER on a pad)
            if n_layers > 1:
                other = 1 - layer
                nxt_via = (cur[0], cur[1], other)
                if ((cur[0], cur[1]) not in _occupied_by_layer[other]
                        and (cur[0], cur[1]) not in _no_via_cells):
                    ng = g + VIA_PENALTY
                    if ng < g_cost.get(nxt_via, 10**9):
                        g_cost[nxt_via] = ng
                        prev[nxt_via] = cur
                        heapq.heappush(pq, (ng + _h(nxt_via[0], nxt_via[1]), ng, nxt_via))

        return [], False  # no path found

    # ── Identify already-routed nets: check if ALL pads are connected ────────
    # Use union-find on trace segment endpoints to determine pad connectivity.
    # Only skip a net if every pad in it is reachable from every other pad
    # through existing traces.
    def _trace_connectivity(net_name_tc: str, pads_tc: list[tuple[float, float, int]]) -> bool:
        """Return True if all pads in this net are connected by existing traces."""
        if len(pads_tc) < 2:
            return True
        # Snap threshold: 2 grid cells
        snap_th = GRID * 2
        # Collect trace endpoints for this net
        endpoints: list[tuple[float, float]] = []
        segs: list[tuple[float, float, float, float]] = []
        for _et in board.get("traces", []):
            _en = (_et.get("net", "") or "").upper()
            if _en != net_name_tc:
                continue
            for _seg in _et.get("segments", []):
                sx_t = float(_seg.get("start", {}).get("x", 0))
                sy_t = float(_seg.get("start", {}).get("y", 0))
                ex_t = float(_seg.get("end", {}).get("x", 0))
                ey_t = float(_seg.get("end", {}).get("y", 0))
                segs.append((sx_t, sy_t, ex_t, ey_t))
                endpoints.append((sx_t, sy_t))
                endpoints.append((ex_t, ey_t))
        if not segs:
            return False
        # Union-find
        parent_tc: dict[int, int] = {}
        def _find_tc(a: int) -> int:
            while parent_tc.get(a, a) != a:
                parent_tc[a] = parent_tc.get(parent_tc[a], parent_tc[a])
                a = parent_tc[a]
            return a
        def _union_tc(a: int, b: int) -> None:
            ra, rb = _find_tc(a), _find_tc(b)
            if ra != rb:
                parent_tc[ra] = rb
        # All points: pads + trace endpoints
        all_pts: list[tuple[float, float]] = [(p[0], p[1]) for p in pads_tc] + endpoints
        n_pads = len(pads_tc)
        # Union points that are close together
        for i in range(len(all_pts)):
            for j in range(i + 1, len(all_pts)):
                if abs(all_pts[i][0] - all_pts[j][0]) < snap_th and abs(all_pts[i][1] - all_pts[j][1]) < snap_th:
                    _union_tc(i, j)
        # Union trace segment endpoints
        idx_base = n_pads
        for si, (sx_t, sy_t, ex_t, ey_t) in enumerate(segs):
            _union_tc(idx_base + si * 2, idx_base + si * 2 + 1)
        # Check all pads are in same group
        roots = set(_find_tc(i) for i in range(n_pads))
        return len(roots) == 1

    _existing_routed_nets: set[str] = set()
    # Will be populated after net_pads is built (moved below)

    # Keep all existing traces and vias — autoroute only adds new ones
    new_traces: list[dict] = list(board.get("traces", []))
    all_vias: list[dict] = list(board.get("vias", []))

    # ── Route each UNROUTED net with greedy nearest-neighbour MST ─────────
    routed = 0
    total  = 0
    skipped_existing = 0
    failed_nets: list[str] = []   # nets where at least one segment couldn't route

    # Sort nets: power first, then alphabetical for determinism
    def _net_priority(name: str) -> int:
        upper = name.upper()
        power_keywords = ("VCC", "VDD", "VIN", "VBAT", "VBUS", "3V3", "5V",
                          "12V", "24V", "AVCC", "DVCC", "VPW", "VMOT", "VPWR")
        return 0 if any(kw in upper for kw in power_keywords) else 1

    # Populate _existing_routed_nets using proper connectivity check
    for _check_net, _check_pads in net_pads.items():
        if len(_check_pads) >= 2 and _trace_connectivity(_check_net, _check_pads):
            _existing_routed_nets.add(_check_net)

    for net_name in sorted(net_pads.keys(), key=lambda n: (_net_priority(n), n)):
        if _autoroute_skip_net(net_name, route_gnd=_route_gnd) or net_name in nc_only_nets:
            continue
        pads_xy = net_pads[net_name]
        if len(pads_xy) < 2:
            continue
        total += 1

        # Skip nets where ALL pads are already connected by existing traces
        if net_name in _existing_routed_nets:
            skipped_existing += 1
            routed += 1
            continue

        width_mm = _autoroute_trace_width(net_name, dr)

        # Temporarily unblock this net's own pad cells AND component body cells
        # so BFS can reach pads inside component bodies.
        # Also unblock NC pads on same components (so traces can escape through
        # gaps between NC pads, e.g. QFN thermal paddle surrounded by NC pins).
        # Also unblock _no_via_cells for own pads so thermal vias can be placed.
        own_cells = all_pad_cells.get(net_name, set())
        own_body_cells = _comp_body_cells.get(net_name, set())
        own_nc_cells = _nc_unblock_for_net.get(net_name, set())
        occupied -= own_cells
        occupied -= own_body_cells
        occupied -= own_nc_cells
        occupied_b -= own_cells
        occupied_b -= own_body_cells
        occupied_b -= own_nc_cells
        _no_via_cells -= own_cells  # allow vias on own pads (thermal vias)

        # Greedy nearest-neighbour MST: always connect closest unconnected pad
        # pads_xy are (x, y, layer_index) tuples
        remaining: list[tuple[float, float, int]] = list(pads_xy)
        connected: list[tuple[float, float, int]] = [remaining.pop(0)]
        per_layer_segs: dict[str, list[dict]] = {}
        vias_list: list[dict] = []
        all_routed = True

        # RF nets must stay on the pad's layer — no layer transitions allowed
        _is_rf_net = net_name.upper().startswith("RF")

        while remaining:
            best_i    = 0
            best_d    = float("inf")
            best_src  = connected[0]
            for i, cand in enumerate(remaining):
                for src in connected:
                    d = abs(cand[0] - src[0]) + abs(cand[1] - src[1])
                    if d < best_d:
                        best_d   = d
                        best_i   = i
                        best_src = src

            dest = remaining.pop(best_i)
            connected.append(dest)

            # Use the source pad's layer as the starting layer for BFS
            src_layer = best_src[2] if len(best_src) > 2 else 0
            path, used_via = _bfs(best_src[0], best_src[1], dest[0], dest[1],
                                  force_single_layer=_is_rf_net,
                                  start_layer=src_layer)
            if not path:
                # Retry with relaxed RF constraint (allow vias)
                if _is_rf_net:
                    path, used_via = _bfs(best_src[0], best_src[1], dest[0], dest[1],
                                          force_single_layer=False,
                                          start_layer=src_layer)
            if not path:
                all_routed = False
                continue

            # Simplify path: merge collinear points on the same layer
            if len(path) > 2:
                simplified: list[tuple[float, float, int]] = [path[0]]
                for k in range(1, len(path) - 1):
                    x_prev, y_prev, l_prev = simplified[-1]
                    x_cur, y_cur, l_cur = path[k]
                    x_nxt, y_nxt, l_nxt = path[k + 1]
                    # Keep point if layer changes or direction changes
                    if l_prev != l_cur or l_cur != l_nxt:
                        simplified.append(path[k])
                        continue
                    # Check collinearity: same direction vector
                    dx1 = x_cur - x_prev
                    dy1 = y_cur - y_prev
                    dx2 = x_nxt - x_cur
                    dy2 = y_nxt - y_cur
                    # Cross product ≈ 0 means collinear
                    if abs(dx1 * dy2 - dy1 * dx2) < 1e-6:
                        continue  # skip — collinear, will be merged
                    simplified.append(path[k])
                simplified.append(path[-1])
                path = simplified

            # Split path into per-layer segments and insert vias at transitions
            for j in range(len(path) - 1):
                x0, y0, l0 = path[j]
                x1, y1, l1 = path[j + 1]
                if l0 != l1:
                    # Layer transition = via
                    vias_list.append({
                        "x": round(x0, 4), "y": round(y0, 4),
                        "size": via_size_mm, "drill": via_drill_mm,
                        "net": net_name,
                    })
                    # Mark via position as occupied on both layers (via size/2 + clearance)
                    via_r = max(1, int(math.ceil((via_size_mm / 2 + cu_clearance) / GRID)))
                    vg = (int(round(x0 / GRID)), int(round(y0 / GRID)))
                    for vdx in range(-via_r, via_r + 1):
                        for vdy in range(-via_r, via_r + 1):
                            occupied.add((vg[0] + vdx, vg[1] + vdy))
                            occupied_b.add((vg[0] + vdx, vg[1] + vdy))
                    continue  # no segment for the transition itself
                if abs(x1 - x0) < 0.001 and abs(y1 - y0) < 0.001:
                    continue  # skip zero-length segments
                layer_name = LAYERS[l0]
                per_layer_segs.setdefault(layer_name, []).append({
                    "start": {"x": round(x0, 4), "y": round(y0, 4)},
                    "end":   {"x": round(x1, 4), "y": round(y1, 4)},
                })
                # Mark on correct layer's occupancy (with clearance expansion)
                _mark_segment(x0, y0, x1, y1, occ=_occupied_by_layer[l0])

        # Re-block this net's pads, body cells, and NC cells now that routing is done
        occupied |= own_cells
        occupied |= own_body_cells
        occupied |= own_nc_cells
        occupied_b |= own_cells
        occupied_b |= own_body_cells
        occupied_b |= own_nc_cells
        _no_via_cells |= own_cells  # re-block vias on pads

        # Build trace objects per layer
        for layer_name, segs in per_layer_segs.items():
            if segs:
                new_traces.append({
                    "net":      net_name,
                    "layer":    layer_name,
                    "width":    width_mm,
                    "segments": segs,
                })
        all_vias.extend(vias_list)
        if per_layer_segs:
            routed += 1
        if not all_routed:
            failed_nets.append(net_name)

    # ── Post-route DRC: detect trace segments crossing foreign-net pads ──────
    # Build a spatial list of all pads with their net and bounding box.
    _all_pad_rects: list[tuple[float, float, float, float, str]] = []  # (cx, cy, hw, hh, net)
    for comp in board.get("components", []):
        cx_c = float(comp.get("x", 0))
        cy_c = float(comp.get("y", 0))
        rot_c = float(comp.get("rotation", 0)) * math.pi / 180
        cos_c, sin_c = math.cos(rot_c), math.sin(rot_c)
        for pad in comp.get("pads", []):
            pnet = (pad.get("net", "") or "").upper()
            if not pnet:
                continue
            lx = float(pad.get("x", 0))
            ly = float(pad.get("y", 0))
            px_w = cx_c + lx * cos_c - ly * sin_c
            py_w = cy_c + lx * sin_c + ly * cos_c
            sx = float(pad.get("size_x", pad.get("sizeX", 0.5)))
            sy = float(pad.get("size_y", pad.get("sizeY", 0.5)))
            if abs(rot_c) > 0.01:
                hw_p = max(abs(sx * cos_c), abs(sy * sin_c)) / 2
                hh_p = max(abs(sx * sin_c), abs(sy * cos_c)) / 2
            else:
                hw_p, hh_p = sx / 2, sy / 2
            _all_pad_rects.append((px_w, py_w, hw_p, hh_p, pnet))

    def _seg_crosses_pad(x0: float, y0: float, x1: float, y1: float, tw: float,
                         pcx: float, pcy: float, phw: float, phh: float) -> bool:
        """Check if a trace segment (as a fat line) crosses a pad rectangle."""
        # Expand pad by half-trace-width + clearance
        ehw = phw + tw / 2 + cu_clearance
        ehh = phh + tw / 2 + cu_clearance
        # Segment AABB
        smin_x, smax_x = (min(x0, x1) - tw / 2, max(x0, x1) + tw / 2)
        smin_y, smax_y = (min(y0, y1) - tw / 2, max(y0, y1) + tw / 2)
        # Quick AABB reject
        if smax_x < pcx - ehw or smin_x > pcx + ehw:
            return False
        if smax_y < pcy - ehh or smin_y > pcy + ehh:
            return False
        # Point-to-segment distance check (closest point on segment to pad center)
        dx, dy = x1 - x0, y1 - y0
        seg_len2 = dx * dx + dy * dy
        if seg_len2 < 1e-12:
            # Zero-length segment — point-to-point
            return abs(x0 - pcx) < ehw and abs(y0 - pcy) < ehh
        t = max(0.0, min(1.0, ((pcx - x0) * dx + (pcy - y0) * dy) / seg_len2))
        closest_x = x0 + t * dx
        closest_y = y0 + t * dy
        return abs(closest_x - pcx) < ehw and abs(closest_y - pcy) < ehh

    violations: list[dict] = []
    _seen_violations: set[tuple[float, float]] = set()
    for trace in new_traces:
        tnet = (trace.get("net", "") or "").upper()
        tw = float(trace.get("width", _default_trace_w))
        for seg in trace.get("segments", []):
            sx0 = float(seg["start"]["x"])
            sy0 = float(seg["start"]["y"])
            sx1 = float(seg["end"]["x"])
            sy1 = float(seg["end"]["y"])
            for pcx_p, pcy_p, phw_p, phh_p, pnet in _all_pad_rects:
                if pnet == tnet:
                    continue  # same net — no violation
                if pnet == "NC" or pnet.startswith("NC_") or pnet.startswith("NC "):
                    continue  # NC pads have no electrical connection — not a real violation
                if _seg_crosses_pad(sx0, sy0, sx1, sy1, tw, pcx_p, pcy_p, phw_p, phh_p):
                    vkey = (round(pcx_p, 2), round(pcy_p, 2))
                    if vkey not in _seen_violations:
                        _seen_violations.add(vkey)
                        violations.append({
                            "type": "NET_CONFLICT",
                            "x": round(pcx_p, 4),
                            "y": round(pcy_p, 4),
                            "trace_net": tnet,
                            "pad_net": pnet,
                            "message": f"Trace '{tnet}' crosses pad of net '{pnet}'",
                        })

    result = dict(board)
    # Normalize all net names to UPPERCASE for consistency
    for comp in result.get("components", []):
        for pad in comp.get("pads", []):
            if pad.get("net"):
                pad["net"] = pad["net"].upper()
    for net_obj in result.get("nets", []):
        if net_obj.get("name"):
            net_obj["name"] = net_obj["name"].upper()
    result["traces"] = new_traces
    result["vias"] = all_vias
    return {**result, "_autoroute": {
        "routed": routed, "total": total, "vias": len(all_vias),
        "violations": violations, "failed_nets": failed_nets,
        "kept_existing": skipped_existing,
    }}


# ── POST /api/pcb/{bid}/autoroute ─────────────────────────────────────────────
@router.post("/pcb/{bid}/autoroute")
async def autoroute_board_id(bid: str):
    fpath = PCB_BOARDS_DIR / f"{bid}.json"
    if not fpath.exists():
        raise HTTPException(404, "Not found")
    board = json.loads(fpath.read_text("utf-8"))
    result = run_autoroute(board)
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
    result = run_autoroute(board)
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


# ── Autoplace ─────────────────────────────────────────────────────────────────

def _load_schematic_hints(
    project_id: str,
    board_width_mm: float,
    board_height_mm: float,
) -> dict[str, tuple[float, float]]:
    """
    Load schematic component positions from the linked project file and
    normalise them into board-mm space, centred on the board centre.

    Returns a dict mapping designator → (x_mm, y_mm), or {} on failure.
    """
    proj_path = PROJECTS_DIR / f"{project_id}.json"
    if not proj_path.exists():
        return {}
    try:
        proj = json.loads(proj_path.read_text("utf-8"))
    except Exception:
        return {}

    # Collect real components only (skip pure power/gnd symbols)
    SKIP_TYPES = {"vcc", "gnd", "pwr", "power"}
    raw: list[tuple[str, float, float]] = []
    for c in proj.get("components", []):
        sym_type = c.get("symType", "").lower()
        if sym_type in SKIP_TYPES:
            continue
        designator = c.get("designator", "")
        sx = c.get("x")
        sy = c.get("y")
        if designator and sx is not None and sy is not None:
            raw.append((designator, float(sx), float(sy)))

    if not raw:
        return {}

    # Find schematic centroid and max extent
    xs = [r[1] for r in raw]
    ys = [r[2] for r in raw]
    cx = sum(xs) / len(xs)
    cy = sum(ys) / len(ys)

    max_extent = max(
        max(abs(x - cx) for x in xs) or 1.0,
        max(abs(y - cy) for y in ys) or 1.0,
    )

    # Scale to fit within 70 % of the usable board half-size
    usable_half_x = (board_width_mm  / 2.0) * 0.70
    usable_half_y = (board_height_mm / 2.0) * 0.70
    scale = min(usable_half_x, usable_half_y) / max_extent

    bx_center = board_width_mm  / 2.0
    by_center = board_height_mm / 2.0

    hints: dict[str, tuple[float, float]] = {}
    for designator, sx, sy in raw:
        hx = bx_center + (sx - cx) * scale
        hy = by_center + (sy - cy) * scale
        hints[designator] = (hx, hy)

    return hints


def _positions_are_spread(components: list[dict]) -> bool:
    """Return True if components have meaningfully different positions
    (i.e. the board has already been laid out, not just default-stacked)."""
    if len(components) < 2:
        return True
    xs = [c.get("x", 0) for c in components]
    ys = [c.get("y", 0) for c in components]
    spread = max(max(xs) - min(xs), max(ys) - min(ys))
    # If all components are within 2 mm of each other they're stacked/unplaced
    if spread <= 2.0:
        return False
    # Also reject if the majority of components share the same position
    # (e.g. 8 of 11 stacked at the same point with only 3 outliers spread)
    from collections import Counter as _Counter
    positions = [(round(c.get("x", 0), 1), round(c.get("y", 0), 1)) for c in components]
    most_common_count = _Counter(positions).most_common(1)[0][1]
    if most_common_count / len(components) > 0.4:
        return False  # majority are stacked — treat as unplaced
    return True


def run_autoplace(board: dict, min_clearance_mm: float = 1.0) -> dict:
    """Force-directed net-proximity autoplacer.

    1. Filters out pure power/GND symbols (no footprint = no physical placement).
    2. **If traces exist**: optimise positions to minimise total trace length
       while keeping tracks and avoiding pad collisions.
    3. Loads schematic positions from the linked project (if projectId present)
       and normalises them to board-mm space as initial-position hints.
    4. Falls back to existing board positions as hints only when they are already
       spread out (i.e. the board has been laid out before).  Stacked/unplaced
       boards get the full net-proximity algorithm from scratch.
    5. Runs the Fruchterman-Reingold force-directed algorithm.
    """
    from agents.autoplace.placement_optimizer import compute_greedy_placement  # noqa: PLC0415

    bw = float(board.get("board", {}).get("width", 100))
    bh = float(board.get("board", {}).get("height", 100))

    # ── Filter: skip pure power/GND symbols that have no physical footprint ──
    _SKIP_REF_PREFIXES = ("GND", "VDD", "VCC", "PWR", "AGND", "DGND", "PGND")
    _SKIP_VALS  = {"GND", "VDD", "VCC", "PWR_FLAG"}
    all_components = [dict(c) for c in board.get("components", [])]
    components = [
        c for c in all_components
        if c.get("footprint", "").strip()                       # must have a footprint
        and not any(c.get("ref", "").upper().startswith(p) for p in _SKIP_REF_PREFIXES)  # skip GND/GND1/GND2 etc.
        and c.get("value", "").upper() not in _SKIP_VALS        # skip pure power values
    ]
    if not components:
        return {**board}

    # ── Build comp_ref → list[net_name] ─────────────────────────────────
    nets = board.get("nets", [])
    comp_nets: dict[str, list[str]] = {c["ref"]: [] for c in components}
    for net in nets:
        net_name = (net.get("name", "") or "").upper()
        if not net_name:
            continue
        for pad_ref in net.get("pads", []):
            ref = pad_ref.split(".")[0]
            if ref in comp_nets and net_name not in comp_nets[ref]:
                comp_nets[ref].append(net_name)

    for comp in components:
        ref = comp.get("ref", "")
        for pad in comp.get("pads", []):
            net_name = (pad.get("net", "") or "").upper()
            if net_name and ref in comp_nets and net_name not in comp_nets[ref]:
                comp_nets[ref].append(net_name)

    # ── Load schematic hints from linked project ─────────────────────────
    schematic_hints: dict[str, tuple[float, float]] = {}
    project_id = board.get("projectId", "")
    if project_id:
        schematic_hints = _load_schematic_hints(project_id, bw, bh)

    # ── Fallback: use existing board positions ONLY when already spread out ──
    # If components are all stacked at the same point (unplaced board),
    # existing positions are useless as hints — run net-proximity from scratch.
    # Only use board positions as hints when the board has real prior placement.
    using_board_positions = False
    if not schematic_hints and _positions_are_spread(components):
        for comp in components:
            ref = comp.get("ref", comp.get("id", ""))
            cx = comp.get("x")
            cy = comp.get("y")
            if ref and cx is not None and cy is not None:
                schematic_hints[ref] = (float(cx), float(cy))
        using_board_positions = bool(schematic_hints)

    # ── Save original rotations from ALL components (preserve on write-back) ─
    orig_rotations: dict[str, int] = {}
    for comp in all_components:
        ref = comp.get("ref", comp.get("id", ""))
        orig_rotations[ref] = comp.get("rotation", 0)

    # ── Load PCB rotations from example_schematic.json (authoritative source) ─
    # The example_schematic.json stores the correct PCB footprint rotation for
    # each component (0/90/180/270°).  These override the board's original
    # rotations, which default to 0° on a freshly-created board.
    pcb_rotations: dict[str, int] = {}
    _example_sch_path = Path(os.getenv("OUTPUT_DIR", str(PROJECT_ROOT_SA / "data" / "outputs"))) / "example_schematic.json"
    if _example_sch_path.exists():
        try:
            _example_sch = json.loads(_example_sch_path.read_text("utf-8"))
            for _ec in _example_sch.get("components", []):
                _ref = _ec.get("reference", "")
                if _ref and "rotation" in _ec:
                    pcb_rotations[_ref] = int(_ec["rotation"])
        except Exception:
            pass  # silently fall back to orig_rotations

    # ── Fill in missing pad-level net fields from the board nets array ────
    # Some boards store net→pad mappings only in the nets array, leaving
    # pad.net empty.  The optimizer needs pad-level net info for pin-aligned
    # placement, so back-fill it here.
    _pad_ref_to_net: dict[str, str] = {}  # "L1.1" → "RF_IN"
    for net in nets:
        net_name = (net.get("name", "") or "").upper()
        if not net_name:
            continue
        for pad_ref in net.get("pads", []):
            _pad_ref_to_net[pad_ref.upper()] = net_name
    for comp in components:
        ref = comp.get("ref", "")
        for pad in comp.get("pads", []):
            if not (pad.get("net", "") or "").strip():
                pad_num = pad.get("number", pad.get("name", ""))
                pad_key = f"{ref}.{pad_num}".upper()
                if pad_key in _pad_ref_to_net:
                    pad["net"] = _pad_ref_to_net[pad_key]

    # ── Build optimizer-format component list ────────────────────────────
    opt_components = [
        {
            "reference": c.get("ref", c.get("id", "?")),
            "value":     c.get("value", ""),
            "footprint": c.get("footprint", ""),
            "rotation":  pcb_rotations.get(
                             c.get("ref", c.get("id", "")),
                             orig_rotations.get(c.get("ref", c.get("id", "")), 0)
                         ),
            "nets":      comp_nets.get(c.get("ref", c.get("id", "")), []),
            "pads":      c.get("pads", []),
        }
        for c in components
    ]

    # Fuzzy fallback: for refs like "U1_g2", "C1_g3" that have no hint,
    # strip the group suffix and reuse the base component's hint with a
    # small offset so duplicated chains start near their originals.
    if schematic_hints:
        import re as _re
        suffix_pat = _re.compile(r'_g\d+$|_inst\d+$|_ch\d+$')
        for comp in opt_components:
            ref = comp["reference"]
            if ref in schematic_hints:
                continue
            base = suffix_pat.sub("", ref)
            if base != ref and base in schematic_hints:
                bx_base, by_base = schematic_hints[base]
                n_dup = sum(1 for k in schematic_hints
                            if suffix_pat.sub("", k) == base)
                schematic_hints[ref] = (bx_base + (n_dup % 3) * 4.0,
                                        by_base + (n_dup // 3) * 4.0)

    # ── Determine hint_weight ─────────────────────────────────────────────
    has_nets = any(net.get("name") for net in nets)
    if using_board_positions:
        # Strong: preserve prior manual placement
        hw = 0.85
    elif not has_nets and schematic_hints:
        # No net connectivity → net-attraction does nothing; rely on hints
        hw = 0.75
    elif schematic_hints:
        # Normal: balance net-attraction with schematic-position hints
        hw = 0.4
    else:
        hw = 0.0

    # ── Run placement ────────────────────────────────────────────────────
    placements = compute_greedy_placement(
        components=opt_components,
        board_width_mm=bw,
        board_height_mm=bh,
        min_clearance_mm=min_clearance_mm,
    )

    # ── Write x/y and rotation back ──────────────────────────────────────
    # Priority: example_schematic.json PCB rotation → optimizer result → original board rotation
    placement_by_ref = {p["reference"]: p for p in placements}
    for comp in components:
        ref = comp.get("ref", comp.get("id", ""))
        if ref in placement_by_ref:
            comp["x"] = placement_by_ref[ref]["x"]
            comp["y"] = placement_by_ref[ref]["y"]
            # Use PCB rotation from example_schematic if known; fall back to
            # the optimizer result (which already carries pcb_rotations), then
            # fall back to the board's original rotation.
            comp["rotation"] = pcb_rotations.get(
                ref,
                placement_by_ref[ref].get("rotation", orig_rotations.get(ref, 0))
            )

    # Merge: keep all original components (including skipped power symbols),
    # updating only the ones that were actually placed.
    placed_map = {c["ref"]: c for c in components}
    merged = []
    for orig_comp in all_components:
        ref = orig_comp.get("ref", "")
        merged.append(placed_map.get(ref, orig_comp))

    result = dict(board)
    result["components"] = merged
    return result


@router.post("/pcb/{bid}/autoplace")
async def autoplace_board_id(bid: str, clearance_mm: float = 1.0):
    fpath = PCB_BOARDS_DIR / f"{bid}.json"
    if not fpath.exists():
        raise HTTPException(404, "Board not found")
    board = json.loads(fpath.read_text("utf-8"))
    result = run_autoplace(board, min_clearance_mm=max(0.0, clearance_mm))
    fpath.write_text(json.dumps(result, indent=2), "utf-8")
    resp: dict[str, Any] = {"components": result.get("components", []), "placed": len(result.get("components", []))}
    return resp


@router.post("/pcb/autoplace")
async def autoplace_direct(request: Request):
    body = await request.json()
    board = body.get("board", body)
    clearance_mm = float(body.get("clearance_mm", 1.0))
    result = run_autoplace(board, min_clearance_mm=max(0.0, clearance_mm))
    resp: dict[str, Any] = {"components": result.get("components", []), "placed": len(result.get("components", []))}
    return resp


# ── Schematic → PCB importer ───────────────────────────────────────────────────

# Cache of available footprint file stems, built lazily from FOOTPRINTS_DIR.
# Maps lowercase stem → original-case stem (e.g. "sot-23" → "SOT-23").
_FOOTPRINT_STEMS: dict[str, str] = {}

def _build_footprint_stems() -> None:
    """Populate _FOOTPRINT_STEMS from the footprints directory (once)."""
    global _FOOTPRINT_STEMS
    if not _FOOTPRINT_STEMS:
        _FOOTPRINT_STEMS = {p.stem.lower(): p.stem for p in FOOTPRINTS_DIR.glob("*.json")}


def _match_package_to_footprint(package_types: list[str]) -> str | None:
    """Try to resolve a library profile's package_types list to an available footprint stem.

    Normalisation cascade (tried in order for each entry):
      1. Exact case-insensitive match          "SOT-23"              → SOT-23
      2. Strip parenthetical suffix            "D2PAK (SMD)"         → D2PAK
      3. First whitespace token                "0402 SMD"            → 0402
      4. Strip trailing alpha suffix           "TO-220AB"            → TO-220
      5. Semantic re-writes                    "28-pin DIP (PDIP28)" → DIP-28
                                               "HTSSOP-16 (...)"     → TSSOP-16
                                               "16-Pad ... QFN"      → QFN-16-...
    The first entry that resolves wins; later entries act as fall-backs.
    """
    _build_footprint_stems()
    for pkg in package_types:
        pkg = pkg.strip()
        bare = re.sub(r'\s*\(.*?\)', '', pkg).strip()   # parenthetical removed

        # 5. Semantic re-writes for common verbose package strings
        semantic: list[str] = []

        # "28-pin DIP ..." → DIP-28
        m = re.match(r'(\d+)-pin\s+DIP', bare, re.IGNORECASE)
        if m:
            semantic.append(f"DIP-{m.group(1)}")

        # "32-pad TQFP ..." → TQFP-32
        m = re.match(r'(\d+)-pad\s+TQFP', bare, re.IGNORECASE)
        if m:
            semantic.append(f"TQFP-{m.group(1)}")

        # "32-pad QFN ..." → QFN-32 (then look for nearest variant in stems)
        m = re.match(r'(\d+)-[Pp]ad\s+(?:\S+\s+)?QFN', bare, re.IGNORECASE)
        if not m:
            m = re.match(r'(\d+)-[Pp]ad.*?QFN', bare, re.IGNORECASE)
        if m:
            n = m.group(1)
            semantic.append(f"QFN-{n}")
            # also try common size variants e.g. QFN-16-3x3
            for stem in _FOOTPRINT_STEMS:
                if stem.startswith(f"qfn-{n}-"):
                    semantic.append(_FOOTPRINT_STEMS[stem])

        # "HTSSOP-16" → TSSOP-16  (H-prefix thermal pad variant)
        m = re.match(r'H(TSSOP-\d+)', bare, re.IGNORECASE)
        if m:
            semantic.append(m.group(1))

        # "12-lead MCLP ..." → MCLP-12
        m = re.match(r'(\d+)-lead\s+MCLP', bare, re.IGNORECASE)
        if m:
            semantic.append(f"MCLP-{m.group(1)}")

        # "SO-8" / "SOIC-8" synonyms
        m = re.match(r'SO-(\d+)$', bare, re.IGNORECASE)
        if m:
            semantic.append(f"SOIC-{m.group(1)}")

        candidates = [
            pkg,            # 1. exact
            bare,           # 2. no parens
            pkg.split()[0] if pkg.split() else '',   # 3. first token
            re.sub(r'[A-Za-z]+$', '', bare).strip(), # 4. strip trailing alpha
        ] + semantic        # 5. semantic re-writes

        for c in candidates:
            if c and c.lower() in _FOOTPRINT_STEMS:
                return _FOOTPRINT_STEMS[c.lower()]
    return None


def _pick_footprint(slug: str, sym_type: str, pin_count: int,
                    package_types: list[str] | None = None,
                    profile_footprint: str | None = None) -> dict | None:
    """Return footprint JSON dict for a schematic component, or None for power symbols.

    Priority:
      0. profile.footprint explicit field        — set by footprint agent (most accurate)
      1. package_types from the library profile  — parsed from real datasheet
      2. SLUG_FP hardcoded overrides             — reliable common-component defaults
      3. SYMTYPE_FP symType-level fallback        — generic category defaults
      4. DIP-N / LQFP-64 by pin count            — last resort for unknown ICs
    """
    slug_up = slug.upper()

    # Skip power symbols — they have no physical footprint
    if sym_type in ("vcc", "gnd", "pwr", "power") or slug_up in ("GND", "VCC", "PWR", "POWER"):
        return None

    # 0. Explicit footprint field saved by the footprint agent (highest priority)
    fp_name: str | None = None
    if profile_footprint:
        _build_footprint_stems()
        fp_name = _FOOTPRINT_STEMS.get(profile_footprint.lower())

    # 1. Generic-symbol overrides — checked BEFORE package_types.
    #    These slugs represent multi-package schematic symbols (RESISTOR, LED…).
    #    We pin a sensible default early so that a random package_type entry like
    #    "0402 SMD" doesn't accidentally override the intended footprint.
    GENERIC_SLUG_FP: dict[str, str] = {
        "RESISTOR":      "0402",
        "CAPACITOR":     "0402",
        "CAPACITOR_POL": "CAP-POL-5mm",
        "INDUCTOR":      "0603",
        "DIODE":         "DO-41",
        "LED":           "LED-5mm",
        "ZENER":         "SOD-123",
        "SCHOTTKY":      "SOD-123",
        "FUSE":          "0603",
        "FERRITE":       "0603",
        "CRYSTAL":       "0805",
        "TESTPOINT":     "0402",
    }
    if fp_name is None:
        fp_name = GENERIC_SLUG_FP.get(slug_up)

    # 2. Use package_types from the library profile (authoritative source for real ICs)
    if fp_name is None:
        fp_name = _match_package_to_footprint(package_types or [])

    # 3. IC-specific slug fallbacks — for known library components whose package_types
    #    strings may not resolve, or as a safety net when profile.footprint is absent.
    IC_SLUG_FP: dict[str, str] = {
        "AMS1117-3.3":    "SOT-223",
        "AP2112":         "SOT25",
        "ATMEGA328P":     "DIP-28",
        "BC547":          "TO-92",
        "BC557":          "TO-92",
        "DRV8833":        "TSSOP-16",
        "ESP32_WROOM_32": "DIP-40",   # No module footprint — DIP-40 is closest available
        "IRF540N":        "TO-220",
        "L298N":          "DIP-20",
        "LM358":          "DIP-8",
        "LM7805":         "TO-220",
        "NE555":          "DIP-8",
        "STM32F103C8":    "LQFP-48",
    }
    if fp_name is None:
        fp_name = IC_SLUG_FP.get(slug_up)

    # 3. symType-level fallback
    SYMTYPE_FP: dict[str, str] = {
        "resistor":   "0402",
        "capacitor":  "0402",
        "inductor":   "0603",
        "diode":      "DO-41",
        "led":        "LED-5mm",
        "transistor": "SOT-23",
        "mosfet":     "SOT-23",
        "jfet":       "SOT-23",
        "zener":      "SOD-123",
        "schottky":   "SOD-123",
        "tvs":        "SOD-123",
        "ferrite":    "0603",
        "crystal":    "0805",
        "oscillator": "DIP-8",
        "switch":     "0402",
        "connector":  "DIP-8",
        "relay":      "DIP-8",
        "fuse":       "0603",
        "testpoint":  "0402",
    }
    if fp_name is None:
        fp_name = SYMTYPE_FP.get((sym_type or "").lower())

    # 4. IC pin-count → package fallback
    if fp_name is None:
        if pin_count <= 8:
            fp_name = "DIP-8"
        elif pin_count <= 16:
            fp_name = "DIP-16"
        elif pin_count <= 20:
            fp_name = "DIP-20"
        elif pin_count <= 28:
            fp_name = "DIP-28"
        elif pin_count <= 40:
            fp_name = "DIP-40"
        else:
            fp_name = "LQFP-64"

    fp_path = FOOTPRINTS_DIR / (fp_name + ".json")
    if fp_path.exists():
        try:
            return json.loads(fp_path.read_text("utf-8"))
        except Exception:
            pass
    return None


@router.post("/pcb/import-schematic")
async def api_pcb_import_schematic(request: Request):
    """Convert a schematic project JSON into an initial PCB board layout.

    Accepts: { "project": <project JSON>, "netlist": <optional namedNets dict>,
               "boardW": float, "boardH": float }
    Returns: PCB board JSON consumable by PCBEditor.load()
    """
    body = await request.json()
    project: dict = body.get("project", {})
    netlist: dict = body.get("netlist") or {}  # { net_name: ["R1.1", "C1.2", ...], ... }

    schematic_comps: list[dict] = project.get("components", [])
    bw = float(body.get("boardW", 100.0))
    bh = float(body.get("boardH", 80.0))

    # ── Board title: "{project name} - V{n}" ──────────────────────────────
    project_name = project.get("name") or "Design"
    project_id   = project.get("id", "")
    existing_count = 0
    if project_id:
        try:
            existing_count = sum(
                1 for f in PCB_BOARDS_DIR.glob("*.json")
                if json.loads(f.read_text("utf-8")).get("projectId") == project_id
            )
        except Exception:
            pass
    board_title = f"{project_name} - V{existing_count + 1}"

    # ── Scale schematic positions → board mm ──────────────────────────────
    xs = [float(c["x"]) for c in schematic_comps if isinstance(c.get("x"), (int, float))]
    ys = [float(c["y"]) for c in schematic_comps if isinstance(c.get("y"), (int, float))]

    if xs and ys:
        schem_cx = sum(xs) / len(xs)
        schem_cy = sum(ys) / len(ys)
        schem_w  = max(max(xs) - min(xs), 1.0)
        schem_h  = max(max(ys) - min(ys), 1.0)
        margin   = 0.15
        scale_x  = (bw * (1 - 2 * margin)) / schem_w
        scale_y  = (bh * (1 - 2 * margin)) / schem_h
    else:
        schem_cx = schem_cy = 0.0
        scale_x  = scale_y  = 1.0

    board_cx = bw / 2.0
    board_cy = bh / 2.0

    # ── Build pad→net lookup from netlist ──────────────────────────────────
    pad_net: dict[str, str] = {}  # "R1.1" → "GND"
    for net_name, pads_list in netlist.items():
        if isinstance(pads_list, list):
            for p in pads_list:
                pad_net[str(p)] = net_name.upper()  # force CAPS

    # ── Pre-load layout_example data for example groups ────────────────────
    # When a schematic component was placed via an "example circuit" drop
    # (_exampleGroupId is set), we use the library's saved layout_example to
    # position it and its support components exactly as the designer intended.
    #
    # Match chain:
    #   schematic._exampleX/Y  →  example_circuit position  →  ref
    #   ref  →  layout_example component position (mm)
    #
    # le_groups[eg_id] = {
    #   "eg_slug":       str,           e.g. "PMA3_83LNW"
    #   "le_ref_to_pos": {ref: (x,y,rot)},
    #   "le_anchor_ref": str,           the main IC ref in the layout_example
    #   "le_anchor_pos": (x, y),        the main IC's position in layout_example
    #   "ec_pos_to_ref": {(rx,ry): ref},  rounded example_circuit pos → ref
    #   "traces":        [...],
    #   "vias":          [...],
    # }
    le_groups: dict[str, dict] = {}

    # Collect unique (eg_id, eg_slug) pairs
    eg_slugs: dict[str, str] = {}  # eg_id → eg_slug (uppercased)
    for sc in schematic_comps:
        eg_id  = sc.get("_exampleGroupId")
        eg_slug = sc.get("_exampleSlug", "")
        if eg_id and eg_slug:
            eg_slugs[eg_id] = eg_slug.upper()

    for eg_id, eg_slug in eg_slugs.items():
        profile_path = LIBRARY_DIR / eg_slug / "profile.json"
        if not profile_path.exists():
            continue
        try:
            eg_profile = json.loads(profile_path.read_text("utf-8"))
        except Exception:
            continue

        le       = eg_profile.get("layout_example") or {}
        le_comps = le.get("components") or []
        ec_comps = (eg_profile.get("example_circuit") or {}).get("components") or []
        if not le_comps:
            continue

        # Build example_circuit position → ref map (rounded to nearest int for
        # fuzzy matching against _exampleX/_exampleY which may be floats)
        ec_pos_to_ref: dict[tuple, str] = {}
        for ec in ec_comps:
            ex  = ec.get("x")
            ey  = ec.get("y")
            ref = str(ec.get("ref", ec.get("designator", "")))
            if ex is not None and ey is not None and ref:
                ec_pos_to_ref[(round(float(ex)), round(float(ey)))] = ref

        # Build layout_example ref → (x, y, rotation) map
        le_ref_to_pos: dict[str, tuple] = {}
        le_anchor_ref: str | None = None
        le_anchor_pos: tuple | None = None
        _PASSIVE_FPS = {"0201", "0402", "0603", "0805", "1206", "2010", "2512",
                        "do-41", "sod-123", "sod123", "led-5mm"}
        for lc in le_comps:
            le_ref  = str(lc.get("ref", lc.get("id", "")))
            le_x    = float(lc.get("x", 0))
            le_y    = float(lc.get("y", 0))
            le_rot  = int(lc.get("rotation", 0))
            le_ref_to_pos[le_ref] = (le_x, le_y, le_rot)
            # Anchor = the first non-passive (IC) component
            if le_anchor_ref is None:
                fp_lower = str(lc.get("footprint", "")).lower()
                if fp_lower not in _PASSIVE_FPS:
                    le_anchor_ref = le_ref
                    le_anchor_pos = (le_x, le_y)
        # Fallback: first component is anchor
        if le_anchor_ref is None and le_comps:
            lc = le_comps[0]
            le_anchor_ref = str(lc.get("ref", lc.get("id", "")))
            le_anchor_pos = (float(lc.get("x", 0)), float(lc.get("y", 0)))

        if le_anchor_ref is None:
            continue

        le_groups[eg_id] = {
            "eg_slug":       eg_slug,
            "le_ref_to_pos": le_ref_to_pos,
            "le_anchor_ref": le_anchor_ref,
            "le_anchor_pos": le_anchor_pos,
            "ec_pos_to_ref": ec_pos_to_ref,
            "traces":        le.get("traces") or [],
            "vias":          le.get("vias")   or [],
        }

    # Pre-compute the anchor component's PCB position for each example group
    # (the IC's schematic x/y is scaled to board mm normally, everything else
    # in the group is then offset relative to it using layout_example coords)
    anchor_pcb_pos: dict[str, tuple] = {}  # eg_id → (bx, by)
    for sc in schematic_comps:
        eg_id = sc.get("_exampleGroupId")
        if not eg_id or eg_id not in le_groups:
            continue
        sc_slug = str(sc.get("slug", sc.get("symType", ""))).upper()
        if sc_slug != le_groups[eg_id]["eg_slug"]:
            continue  # not the anchor
        sx = float(sc.get("x", 0))
        sy = float(sc.get("y", 0))
        ax = board_cx + (sx - schem_cx) * scale_x
        ay = board_cy + (sy - schem_cy) * scale_y
        ax = round(max(5.0, min(bw - 5.0, ax)), 2)
        ay = round(max(5.0, min(bh - 5.0, ay)), 2)
        anchor_pcb_pos[eg_id] = (ax, ay)

    # ── Build net mappings for each example group ──────────────────────────
    # We need to translate layout_example net names (which may be internal
    # "Nx" identifiers or LE-canonical names like "RF_IN") to the actual
    # schematic net names produced by buildNetlist() (e.g. "RFIN", "GND").
    #
    # Chain:
    #   le_pad_key → internal LE net   (from LE component pads)
    #   le_pad_key → LE canonical net  (from le.nets[] list)
    #   internal LE net → LE canonical (cross-ref)
    #   LE pad ref → SC pad ref        (via le_ref_to_sc_ref)
    #   SC pad ref → SC net name       (via pad_net lookup)
    #
    # Results stored back into le_groups[eg_id]:
    #   "le_ref_to_sc_ref":   {le_ref: sc_ref}
    #   "le_int_net_to_sc":   {internal_net: sc_net_name}
    #   "le_canonical_to_sc": {le_canonical: sc_net_name}
    for eg_id, eg_info in le_groups.items():
        # 1. Build le_ref → sc_ref from schematic components
        le_ref_to_sc_ref: dict[str, str] = {}
        for sc in schematic_comps:
            if sc.get("_exampleGroupId") != eg_id:
                continue
            ex_x = sc.get("_exampleX")
            ex_y = sc.get("_exampleY")
            sc_ref = str(sc.get("ref", sc.get("designator", "")))
            if ex_x is not None and ex_y is not None and sc_ref:
                le_ref = eg_info["ec_pos_to_ref"].get(
                    (round(float(ex_x)), round(float(ex_y))))
                if le_ref:
                    le_ref_to_sc_ref[le_ref] = sc_ref
        eg_info["le_ref_to_sc_ref"] = le_ref_to_sc_ref

        # 2. Build le_pad_key → internal LE net  (from component pad fields)
        le_pad_to_int_net: dict[str, str] = {}
        # Retrieve the raw component list from the library profile directly
        try:
            _le_profile = json.loads(
                (LIBRARY_DIR / eg_info["eg_slug"] / "profile.json").read_text("utf-8"))
            le_comps_raw = (_le_profile.get("layout_example") or {}).get("components") or []
            le_nets_raw  = (_le_profile.get("layout_example") or {}).get("nets") or []
        except Exception:
            le_comps_raw = []
            le_nets_raw  = []

        for lc in le_comps_raw:
            lc_ref = str(lc.get("ref", lc.get("id", "")))
            for p in lc.get("pads", []):
                key = f'{lc_ref}.{p.get("number", "")}'
                le_pad_to_int_net[key] = str(p.get("net", ""))

        # 3. Build le_canonical → internal nets  and  le_canonical → sc_net
        le_canonical_to_sc_net: dict[str, str] = {}
        le_int_net_to_canonical: dict[str, str] = {}
        for le_net in le_nets_raw:
            canonical = str(le_net.get("name", ""))
            for le_pad_ref in le_net.get("pads", []):
                # internal net name for this pad
                int_net = le_pad_to_int_net.get(le_pad_ref, "")
                if int_net and canonical:
                    le_int_net_to_canonical.setdefault(int_net, canonical)

                # sc net name: translate LE pad ref → SC pad ref → pad_net
                parts = le_pad_ref.split(".", 1)
                if len(parts) == 2:
                    sc_ref = le_ref_to_sc_ref.get(parts[0])
                    if sc_ref:
                        sc_net = pad_net.get(f"{sc_ref}.{parts[1]}", "")
                        if sc_net and canonical not in le_canonical_to_sc_net:
                            le_canonical_to_sc_net[canonical] = sc_net

        # 4. Build internal → sc_net  (the final lookup used on traces)
        le_int_net_to_sc: dict[str, str] = {}
        for int_net, canonical in le_int_net_to_canonical.items():
            sc_net = le_canonical_to_sc_net.get(canonical, canonical)
            le_int_net_to_sc[int_net] = sc_net
        # Also map canonical names themselves (pads that already use readable names)
        for canonical, sc_net in le_canonical_to_sc_net.items():
            le_int_net_to_sc[canonical] = sc_net

        eg_info["le_int_net_to_sc"]   = le_int_net_to_sc
        eg_info["le_canonical_to_sc"] = le_canonical_to_sc_net
        eg_info["le_nets_raw"]        = le_nets_raw

    # ── Build PCB components ───────────────────────────────────────────────
    pcb_comps: list[dict] = []
    for sc in schematic_comps:
        slug     = str(sc.get("slug", sc.get("symType", ""))).upper()
        sym_type = str(sc.get("symType", "")).lower()
        ref      = str(sc.get("ref", sc.get("designator", sc.get("id", ""))))
        value    = str(sc.get("value", ""))

        # Skip power symbols — no physical component on PCB
        if sym_type in ("vcc", "gnd", "pwr", "power") or slug in ("GND", "VCC", "PWR", "POWER"):
            continue
        if not ref:
            continue

        # Load library profile for pin names
        profile: dict = {}
        profile_path = LIBRARY_DIR / slug / "profile.json"
        if profile_path.exists():
            try:
                profile = json.loads(profile_path.read_text("utf-8"))
            except Exception:
                pass

        pins: list[dict] = profile.get("pins", [])
        pin_count = len(pins)
        package_types: list[str] = profile.get("package_types", [])
        profile_footprint: str | None = profile.get("footprint") or None
        pin_by_num: dict[str, str] = {
            str(p.get("number", "")): str(p.get("name", "")) for p in pins
        }

        fp_data = _pick_footprint(slug, sym_type, pin_count, package_types, profile_footprint)

        # ── Build pads ────────────────────────────────────────────────────
        pads: list[dict] = []
        if fp_data:
            for fp_pad in fp_data.get("pads", []):
                pad_num  = str(fp_pad.get("number", ""))
                pad_name = pin_by_num.get(pad_num, pad_num)
                pad_key  = f"{ref}.{pad_num}"
                pad_entry: dict = {
                    "number": pad_num,
                    "name":   pad_name,
                    "x":      float(fp_pad.get("x", 0)),
                    "y":      float(fp_pad.get("y", 0)),
                    "type":   fp_pad.get("type",  "smd"),
                    "shape":  fp_pad.get("shape", "rect"),
                    "size_x": float(fp_pad.get("size_x", 0.6)),
                    "size_y": float(fp_pad.get("size_y", 0.6)),
                    "net":    pad_net.get(pad_key, ""),
                }
                if "drill" in fp_pad:
                    pad_entry["drill"] = fp_pad["drill"]
                pads.append(pad_entry)
        elif pins:
            # Generate inline pads from pin list (fallback for unknown packages)
            half = (pin_count - 1) / 2.0
            for i, pin in enumerate(pins):
                pad_num = str(pin.get("number", i + 1))
                pad_key = f"{ref}.{pad_num}"
                pads.append({
                    "number": pad_num,
                    "name":   str(pin.get("name", pad_num)),
                    "x":      round((i - half) * 2.54, 3),
                    "y":      0.0,
                    "type":   "thru_hole",
                    "shape":  "circle",
                    "size_x": 1.6,
                    "size_y": 1.6,
                    "drill":  0.8,
                    "net":    pad_net.get(pad_key, ""),
                })
        else:
            # Last-resort: 2-pad SMD
            for i, pad_num in enumerate(["1", "2"]):
                pad_key = f"{ref}.{pad_num}"
                pads.append({
                    "number": pad_num,
                    "name":   pad_num,
                    "x":      -0.5 + i * 1.0,
                    "y":      0.0,
                    "type":   "smd",
                    "shape":  "rect",
                    "size_x": 0.6,
                    "size_y": 0.6,
                    "net":    pad_net.get(pad_key, ""),
                })

        # ── Determine PCB position ─────────────────────────────────────────
        # Priority 1: layout_example relative position (for example-group components)
        # Priority 2: standard schematic → board mm scaling
        rotation = int(sc.get("rotation", 0))
        eg_id    = sc.get("_exampleGroupId")
        eg_info  = le_groups.get(eg_id) if eg_id else None

        if eg_info and eg_id in anchor_pcb_pos:
            is_anchor = (slug == eg_info["eg_slug"])
            if is_anchor:
                # Anchor: use its schematic-derived position (already computed)
                bx, by = anchor_pcb_pos[eg_id]
                # Use layout_example rotation for the anchor too
                le_anchor_ref = eg_info["le_anchor_ref"]
                if le_anchor_ref in eg_info["le_ref_to_pos"]:
                    rotation = eg_info["le_ref_to_pos"][le_anchor_ref][2]
            else:
                # Support component: translate from layout_example relative to anchor
                ex_x = sc.get("_exampleX")
                ex_y = sc.get("_exampleY")
                le_ref = None
                if ex_x is not None and ex_y is not None:
                    le_ref = eg_info["ec_pos_to_ref"].get(
                        (round(float(ex_x)), round(float(ex_y)))
                    )
                if le_ref and le_ref in eg_info["le_ref_to_pos"]:
                    le_x, le_y, le_rot = eg_info["le_ref_to_pos"][le_ref]
                    le_ax, le_ay       = eg_info["le_anchor_pos"]
                    ax, ay             = anchor_pcb_pos[eg_id]
                    bx = round(ax + (le_x - le_ax), 2)
                    by = round(ay + (le_y - le_ay), 2)
                    bx = round(max(2.0, min(bw - 2.0, bx)), 2)
                    by = round(max(2.0, min(bh - 2.0, by)), 2)
                    rotation = le_rot
                else:
                    # No layout_example match — fall back to schematic position
                    sx = float(sc.get("x", 0))
                    sy = float(sc.get("y", 0))
                    bx = board_cx + (sx - schem_cx) * scale_x
                    by = board_cy + (sy - schem_cy) * scale_y
                    bx = round(max(5.0, min(bw - 5.0, bx)), 2)
                    by = round(max(5.0, min(bh - 5.0, by)), 2)
        else:
            # Standard: scale schematic position to board mm
            sx = float(sc.get("x", 0))
            sy = float(sc.get("y", 0))
            bx = board_cx + (sx - schem_cx) * scale_x
            by = board_cy + (sy - schem_cy) * scale_y
            bx = round(max(5.0, min(bw - 5.0, bx)), 2)
            by = round(max(5.0, min(bh - 5.0, by)), 2)

        comp_entry: dict = {
            "id":        ref,
            "ref":       ref,
            "value":     value,
            "footprint": fp_data.get("name", "") if fp_data else slug,
            "x":         bx,
            "y":         by,
            "rotation":  rotation,
            "layer":     "F",
            "pads":      pads,
        }
        if eg_id:
            comp_entry["groupId"] = eg_id
        pcb_comps.append(comp_entry)

    # ── Translate layout_example traces/vias onto the board ────────────────
    # For each example group that had a saved layout, offset its traces/vias
    # by the same (anchor_pcb - anchor_le) vector used for components.
    pcb_traces: list[dict] = []
    pcb_vias:   list[dict] = []
    for eg_id, eg_info in le_groups.items():
        if eg_id not in anchor_pcb_pos:
            continue
        ax, ay    = anchor_pcb_pos[eg_id]
        le_ax, le_ay = eg_info["le_anchor_pos"]
        dx = ax - le_ax
        dy = ay - le_ay

        net_map = eg_info.get("le_int_net_to_sc", {})
        for trace in eg_info["traces"]:
            t = dict(trace)
            # Remap internal LE net name ("N1" → "RFIN") using the chain:
            #   internal → LE canonical → schematic net name
            raw_net = t.get("net", "")
            t["net"] = net_map.get(raw_net, raw_net)
            # Tag trace with the example groupId so it moves/rotates with the group
            t["groupId"] = eg_id
            # Translate coordinates — three possible storage formats:
            if "x1" in t:                                        # flat x1/y1/x2/y2
                t["x1"] = round(float(t["x1"]) + dx, 3)
                t["y1"] = round(float(t["y1"]) + dy, 3)
                t["x2"] = round(float(t["x2"]) + dx, 3)
                t["y2"] = round(float(t["y2"]) + dy, 3)
            if "points" in t:                                    # points list
                t["points"] = [
                    {"x": round(float(p["x"]) + dx, 3),
                     "y": round(float(p["y"]) + dy, 3)}
                    for p in t["points"]
                ]
            if "segments" in t:                                  # segments list
                t["segments"] = [
                    {
                        "start": {"x": round(float(seg["start"]["x"]) + dx, 3),
                                  "y": round(float(seg["start"]["y"]) + dy, 3)},
                        "end":   {"x": round(float(seg["end"]["x"])   + dx, 3),
                                  "y": round(float(seg["end"]["y"])   + dy, 3)},
                    }
                    for seg in t["segments"]
                ]
            pcb_traces.append(t)

        for via in eg_info["vias"]:
            v = dict(via)
            v["x"]   = round(float(v.get("x", 0)) + dx, 3)
            v["y"]   = round(float(v.get("y", 0)) + dy, 3)
            raw_net  = v.get("net", "")
            v["net"] = net_map.get(raw_net, raw_net)
            # Tag via with the example groupId so it moves/rotates with the group
            v["groupId"] = eg_id
            pcb_vias.append(v)

    # ── Build nets list from netlist dict + translated LE nets ─────────────
    # Start from the schematic netlist, then merge in the LE canonical nets
    # (translated to schematic refs) so pad connectivity from the layout_example
    # is reflected even for pads not covered by the schematic wire tracing.
    pcb_nets_by_name: dict[str, set] = {}
    for net_name, pads_list in netlist.items():
        if isinstance(pads_list, list) and pads_list:
            pcb_nets_by_name.setdefault(net_name, set()).update(str(p) for p in pads_list)

    for eg_id, eg_info in le_groups.items():
        le_ref_to_sc_ref = eg_info.get("le_ref_to_sc_ref", {})
        canonical_to_sc  = eg_info.get("le_canonical_to_sc", {})
        for le_net in eg_info.get("le_nets_raw", []):
            canonical = str(le_net.get("name", ""))
            sc_net_name = canonical_to_sc.get(canonical, canonical)
            if not sc_net_name:
                continue
            for le_pad_ref in le_net.get("pads", []):
                parts = le_pad_ref.split(".", 1)
                if len(parts) == 2:
                    sc_ref = le_ref_to_sc_ref.get(parts[0])
                    if sc_ref:
                        pcb_nets_by_name.setdefault(sc_net_name, set()).add(
                            f"{sc_ref}.{parts[1]}")

    pcb_nets: list[dict] = [
        {"name": name.upper(), "pads": sorted(pads)}
        for name, pads in pcb_nets_by_name.items()
        if pads
    ]

    # ── Build groups list from example-group membership ────────────────────
    pcb_groups_map: dict[str, dict] = {}
    for comp in pcb_comps:
        gid = comp.get("groupId")
        if gid:
            if gid not in pcb_groups_map:
                eg_info = le_groups.get(gid, {})
                pcb_groups_map[gid] = {
                    "id":      gid,
                    "name":    eg_info.get("eg_slug", gid),
                    "members": [],
                }
            pcb_groups_map[gid]["members"].append(comp["id"])
    pcb_groups = list(pcb_groups_map.values())

    return {
        "title":      board_title,
        "board":      {"width": bw, "height": bh, "units": "mm"},
        "components": pcb_comps,
        "nets":       pcb_nets,
        "traces":     pcb_traces,
        "vias":       pcb_vias,
        "areas":      [],
        "groups":     pcb_groups,
    }


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
        prompt = f"""Build a PCB layout example for **{part}**.

## Component profile
{profile_json}

## All pins ({len(pins)})
  NUM   NAME                 TYPE         DESCRIPTION
{pin_table}

## Existing footprint
{existing_footprint}

## Existing example circuit
{existing_example}

## Existing layout example (for reference / improvement)
{existing_layout}

## Task
Produce a compact, routable PCB layout that matches the example circuit above.
Place components sensibly (decoupling caps close to power pins, etc.) and
pre-route as many traces as possible.

The layout JSON must include:
- `components[]`: each with `id`, `ref`, `x`, `y`, `rotation`, `layer` (F|B), `pads[]`
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
- `part_number`: exact part number string
- `description`: one-line description
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
