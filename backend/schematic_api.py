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
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
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
    profile["symbol_type"] = body["symbol_type"]
    _write_profile(slug, profile)
    return {"ok": True}

@router.put("/library/{slug}/pins")
async def api_library_pins(slug: str, request: Request):
    body = await request.json()
    if not isinstance(body.get("pins"), list):
        raise HTTPException(400, "pins array required")
    profile = _read_profile(slug)
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
    return {"ok": True}

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
    data["tickets"][idx] = {**data["tickets"][idx], **body, "id": tid}
    _save_gen_tickets(data)
    return data["tickets"][idx]

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
    snap_path = _history_dir(slug) / (hid + ".json")
    if not snap_path.exists():
        raise HTTPException(404, "Not found")
    snap = json.loads(snap_path.read_text(encoding="utf-8"))
    snap["profile"] = json.loads(_profile_path(slug).read_text(encoding="utf-8"))
    snap["saved_at"] = datetime.now(timezone.utc).isoformat()
    snap_path.write_text(json.dumps(snap, indent=2), encoding="utf-8")
    return {"ok": True}

@router.delete("/library/{slug}/history/{hid}")
def api_history_delete(slug: str, hid: str):
    snap_path = _history_dir(slug) / (hid + ".json")
    if snap_path.exists():
        snap_path.unlink()
    return {"ok": True}

# ── PUT /api/library/:slug — full profile write ───────────────────────────────
@router.put("/library/{slug}")
async def api_library_put(slug: str, request: Request):
    body = await request.json()
    pp = _profile_path(slug)
    if not pp.exists():
        raise HTTPException(404, "Not found")
    _snapshot_profile(slug, body.get("_label", "profile-rebuild"))
    _clear_active_version(slug)
    current = json.loads(pp.read_text(encoding="utf-8"))
    new_profile = {**body, "human_corrections": current.get("human_corrections", [])}
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


# ── Startup: rebuild index ─────────────────────────────────────────────────────
_update_index()
