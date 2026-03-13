"""
backend/main.py — Auto-EDA FastAPI application.

Endpoints:
  POST /upload                 — upload PDF + project brief, queue job
  GET  /status/{job_id}        — job status + step info
  GET  /stream/{job_id}        — SSE real-time progress stream
  GET  /outputs/{job_id}       — all output files for a completed job
  GET  /download/{filename}    — download a specific output file
  GET  /jobs                   — list recent jobs
  POST /jobs/{job_id}/retry    — requeue a failed job

Agent routers (mounted under /agents/*):
  /agents/connectivity/*
  (others added as they expose FastAPI routers)

Static frontend:
  GET / → frontend/static/index.html
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from pathlib import Path

import redis
from fastapi import FastAPI, HTTPException, UploadFile, Form
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

# ── Path setup so agent endpoints can import their own modules ────────────────
_PROJECT_ROOT = Path(__file__).parent.parent
for _agent in [
    "agents/connectivity",
    "agents/schematic",
    "agents/component",
    "agents/footprint",
    "agents/datasheet-parser",
    "agents/autoplace",
    "agents/autoroute",
    "agents/layout",
    "agents/example-schematic",
    "backend/tools",
]:
    _p = str(_PROJECT_ROOT / _agent)
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(title="Auto-EDA", version="1.0.0", description="AI-Powered PCB Design Pipeline")

# ── Redis ─────────────────────────────────────────────────────────────────────
_r = redis.Redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379"), decode_responses=True)

UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", str(_PROJECT_ROOT / "data" / "uploads")))
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", str(_PROJECT_ROOT / "data" / "outputs")))

# ── Mount agent routers ───────────────────────────────────────────────────────
try:
    from agents.connectivity.endpoint import router as _conn_router
    app.include_router(_conn_router)
except Exception:
    pass

# ── Mount schematic designer API ──────────────────────────────────────────────
try:
    _backend_dir = str(Path(__file__).parent)
    if _backend_dir not in sys.path:
        sys.path.insert(0, _backend_dir)
    from schematic_api import router as _schematic_router
    app.include_router(_schematic_router)
except Exception as e:
    print(f"[warn] schematic_api not loaded: {e}")


# ── Upload ────────────────────────────────────────────────────────────────────
@app.post("/upload")
async def upload_datasheet(
    file: UploadFile,
    brief: str = Form(default=""),
):
    """Upload a datasheet PDF and queue the EDA pipeline job."""
    job_id = str(uuid.uuid4())
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

    safe_name = Path(file.filename).name
    dest = UPLOAD_DIR / safe_name
    with open(dest, "wb") as fh:
        fh.write(await file.read())

    payload = json.dumps({"job_id": job_id, "pdf": str(dest), "brief": brief})
    _r.hset(f"job:{job_id}", mapping={
        "status": "queued",
        "pdf": str(dest),
        "brief": brief,
        "step": "",
        "step_label": "",
        "step_index": "-1",
        "total_steps": "9",
        "step_status": "",
        "error": "",
    })
    _r.rpush("pipeline:queue", payload)
    _r.lpush("jobs:recent", job_id)
    _r.ltrim("jobs:recent", 0, 49)  # keep last 50

    return {"status": "queued", "job_id": job_id, "file": safe_name}


# ── Status ────────────────────────────────────────────────────────────────────
@app.get("/status/{job_id}")
def get_status(job_id: str):
    """Return the current status and step info for a job."""
    raw = _r.hgetall(f"job:{job_id}")
    if not raw:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    return raw


# ── SSE stream ────────────────────────────────────────────────────────────────
@app.get("/stream/{job_id}")
async def stream_job(job_id: str):
    """
    Server-Sent Events stream.
    Publishes job status updates in real time via Redis pub/sub.
    """
    async def event_gen():
        pubsub = _r.pubsub()
        pubsub.subscribe(f"job:{job_id}")
        try:
            # Send current state immediately
            current = _r.hgetall(f"job:{job_id}")
            if current:
                yield f"data: {json.dumps(current)}\n\n"

            deadline = asyncio.get_event_loop().time() + 1800  # 30-min timeout
            while asyncio.get_event_loop().time() < deadline:
                msg = pubsub.get_message(ignore_subscribe_messages=True, timeout=0.5)
                if msg and msg["type"] == "message":
                    yield f"data: {msg['data']}\n\n"
                    data = json.loads(msg["data"])
                    if data.get("status") in ("done", "error"):
                        break
                await asyncio.sleep(0.1)
        finally:
            pubsub.close()

    return StreamingResponse(event_gen(), media_type="text/event-stream")


# ── Outputs ───────────────────────────────────────────────────────────────────
_OUTPUT_FILES = [
    ("datasheet.json",             "Parsed Datasheet",       "json"),
    ("component.json",             "Component Symbol JSON",  "json"),
    ("component.kicad_sym",        "KiCad Symbol",           "kicad"),
    ("footprint.json",             "Footprint JSON",         "json"),
    ("footprint.kicad_mod",        "KiCad Footprint",        "kicad"),
    ("example_schematic.json",     "Reference Schematic",    "json"),
    ("schematic.json",             "Project Schematic",      "json"),
    ("connectivity.json",          "Connectivity Report",    "json"),
    ("placement.json",             "Component Placement",    "json"),
    ("routing.json",               "Routing",                "json"),
    ("final_layout.json",          "Final Layout Summary",   "json"),
    ("final_layout.kicad_pcb",     "KiCad PCB File",         "kicad"),
]


@app.get("/outputs/{job_id}")
def get_outputs(job_id: str):
    """Return info about all available output files for a job."""
    files = []
    for filename, label, ftype in _OUTPUT_FILES:
        path = OUTPUT_DIR / filename
        files.append({
            "filename": filename,
            "label": label,
            "type": ftype,
            "available": path.exists(),
            "size_bytes": path.stat().st_size if path.exists() else 0,
            "download_url": f"/download/{filename}",
        })
    return {"job_id": job_id, "output_dir": str(OUTPUT_DIR), "files": files}


# ── Download ──────────────────────────────────────────────────────────────────
@app.get("/download/{filename}")
def download_file(filename: str):
    """Download a specific output file."""
    # Prevent path traversal
    safe = Path(filename).name
    path = OUTPUT_DIR / safe
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"{filename} not found")
    media = "application/octet-stream"
    if safe.endswith(".json"):
        media = "application/json"
    return FileResponse(path, filename=safe, media_type=media)


# ── Recent jobs ───────────────────────────────────────────────────────────────
@app.get("/jobs")
def list_jobs():
    """List the 50 most recent jobs."""
    job_ids = _r.lrange("jobs:recent", 0, 49)
    jobs = []
    for jid in job_ids:
        raw = _r.hgetall(f"job:{jid}")
        if raw:
            jobs.append({"job_id": jid, **raw})
    return {"jobs": jobs}


# ── Retry ─────────────────────────────────────────────────────────────────────
@app.post("/jobs/{job_id}/retry")
def retry_job(job_id: str):
    """Requeue a failed or errored job."""
    raw = _r.hgetall(f"job:{job_id}")
    if not raw:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    if raw.get("status") not in ("error", "done"):
        raise HTTPException(status_code=400, detail="Only failed/done jobs can be retried")

    payload = json.dumps({
        "job_id": job_id,
        "pdf": raw.get("pdf", ""),
        "brief": raw.get("brief", ""),
    })
    _r.hset(f"job:{job_id}", mapping={"status": "queued", "error": "", "step": ""})
    _r.rpush("pipeline:queue", payload)
    return {"status": "requeued", "job_id": job_id}


# ── Static frontend ───────────────────────────────────────────────────────────
# We must NOT use StaticFiles at "/" because it shadows all /api/* routes.
# Instead mount under "/static" for assets and serve HTML pages explicitly.
_static_dir = _PROJECT_ROOT / "frontend" / "static"
_static_dir.mkdir(parents=True, exist_ok=True)

from starlette.staticfiles import StaticFiles as _SF
app.mount("/static", _SF(directory=str(_static_dir)), name="assets")

# Serve HTML pages at their natural paths via explicit routes
@app.get("/", include_in_schema=False)
async def serve_index():
    return FileResponse(str(_static_dir / "index.html"))

@app.get("/pcb.html", include_in_schema=False)
async def serve_pcb():
    return FileResponse(str(_static_dir / "pcb.html"))
