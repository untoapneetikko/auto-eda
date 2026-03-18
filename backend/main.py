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


import hashlib
import hmac
import secrets

import redis
from fastapi import FastAPI, HTTPException, Request, UploadFile, Form
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

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

# ── Authentication ────────────────────────────────────────────────────────────
# Users: username → bcrypt-style salted hash (using SHA-256 + salt for simplicity)
_AUTH_SALT = os.getenv("AUTH_SALT", "auto-eda-v1-salt-2026")

def _hash_password(password: str) -> str:
    return hashlib.sha256((_AUTH_SALT + password).encode()).hexdigest()

_USERS_REDIS_KEY = "eda:users"

def _load_users() -> dict[str, dict]:
    raw = _r.get(_USERS_REDIS_KEY)
    if raw:
        return json.loads(raw)
    return {}

def _save_users(users: dict[str, dict]):
    _r.set(_USERS_REDIS_KEY, json.dumps(users))

def _get_users() -> dict[str, dict]:
    users = _load_users()
    # Seed default admin if no users exist
    if not users:
        users["john"] = {
            "password_hash": _hash_password("boy"),
            "display_name": "John",
            "role": "admin",
        }
        _save_users(users)
    return users

_SESSION_TTL = 60 * 60 * 24 * 30  # 30 days

def _create_session(username: str) -> str:
    users = _get_users()
    token = secrets.token_urlsafe(48)
    _r.setex(f"session:{token}", _SESSION_TTL, json.dumps({
        "username": username,
        "display_name": users[username]["display_name"],
        "role": users[username]["role"],
    }))
    return token

def _get_session(token: str) -> dict | None:
    if not token:
        return None
    data = _r.get(f"session:{token}")
    if not data:
        return None
    return json.loads(data)

def _destroy_session(token: str):
    _r.delete(f"session:{token}")

# Paths that don't require auth
_PUBLIC_PATHS = {"/login", "/login.html", "/api/auth/login", "/api/auth/me"}
_PUBLIC_PREFIXES = ("/static/",)

class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        # Allow public paths
        if path in _PUBLIC_PATHS or any(path.startswith(p) for p in _PUBLIC_PREFIXES):
            return await call_next(request)
        # Check session cookie
        token = request.cookies.get("eda_session", "")
        session = _get_session(token)
        if not session:
            # API requests get 401, page requests get redirected to login
            if path.startswith("/api/"):
                return JSONResponse({"detail": "Not authenticated"}, status_code=401)
            return RedirectResponse("/login", status_code=302)
        # Attach user info to request state
        request.state.user = session
        return await call_next(request)

app.add_middleware(AuthMiddleware)

# ── Auth endpoints ────────────────────────────────────────────────────────────
@app.post("/api/auth/login")
async def api_login(request: Request):
    body = await request.json()
    username = (body.get("username") or "").strip().lower()
    password = body.get("password") or ""
    user = _get_users().get(username)
    if not user or not hmac.compare_digest(user["password_hash"], _hash_password(password)):
        raise HTTPException(401, "Invalid username or password")
    token = _create_session(username)
    resp = JSONResponse({"ok": True, "user": {
        "username": username,
        "display_name": user["display_name"],
        "role": user["role"],
    }})
    resp.set_cookie(
        "eda_session", token,
        max_age=_SESSION_TTL,
        httponly=True,
        samesite="strict",
        path="/",
    )
    return resp

@app.post("/api/auth/logout")
async def api_logout(request: Request):
    token = request.cookies.get("eda_session", "")
    if token:
        _destroy_session(token)
    resp = JSONResponse({"ok": True})
    resp.delete_cookie("eda_session", path="/")
    return resp

@app.get("/api/auth/me")
async def api_me(request: Request):
    token = request.cookies.get("eda_session", "")
    session = _get_session(token)
    if not session:
        return JSONResponse({"authenticated": False}, status_code=200)
    return {"authenticated": True, "user": session}

@app.post("/api/auth/update-profile")
async def api_update_profile(request: Request):
    token = request.cookies.get("eda_session", "")
    session = _get_session(token)
    if not session:
        raise HTTPException(401, "Not authenticated")
    body = await request.json()
    username = session["username"]
    users = _get_users()
    user = users.get(username)
    if not user:
        raise HTTPException(404, "User not found")
    # Update display name
    if "display_name" in body:
        new_name = body["display_name"].strip()
        if new_name:
            user["display_name"] = new_name
            session["display_name"] = new_name
            _r.setex(f"session:{token}", _SESSION_TTL, json.dumps(session))
    # Update password
    if "new_password" in body:
        old_pw = body.get("current_password", "")
        if not hmac.compare_digest(user["password_hash"], _hash_password(old_pw)):
            raise HTTPException(400, "Current password is incorrect")
        new_pw = body["new_password"]
        if len(new_pw) < 2:
            raise HTTPException(400, "Password too short")
        user["password_hash"] = _hash_password(new_pw)
    _save_users(users)
    return {"ok": True, "user": {"username": username, "display_name": user["display_name"], "role": user["role"]}}

# ── Admin: User Management ────────────────────────────────────────────────────
@app.get("/api/auth/users")
async def api_list_users(request: Request):
    session = _get_session(request.cookies.get("eda_session", ""))
    if not session or session.get("role") != "admin":
        raise HTTPException(403, "Admin access required")
    users = _get_users()
    return [{"username": u, "display_name": d["display_name"], "role": d["role"]} for u, d in users.items()]

@app.post("/api/auth/users")
async def api_create_user(request: Request):
    session = _get_session(request.cookies.get("eda_session", ""))
    if not session or session.get("role") != "admin":
        raise HTTPException(403, "Admin access required")
    body = await request.json()
    username = (body.get("username") or "").strip().lower()
    password = body.get("password") or ""
    display_name = (body.get("display_name") or username).strip()
    role = body.get("role", "user")
    if not username or len(username) < 2:
        raise HTTPException(400, "Username must be at least 2 characters")
    if not password or len(password) < 2:
        raise HTTPException(400, "Password must be at least 2 characters")
    if role not in ("admin", "user"):
        raise HTTPException(400, "Role must be 'admin' or 'user'")
    users = _get_users()
    if username in users:
        raise HTTPException(409, f"User '{username}' already exists")
    users[username] = {
        "password_hash": _hash_password(password),
        "display_name": display_name,
        "role": role,
    }
    _save_users(users)
    return {"ok": True, "username": username}

@app.delete("/api/auth/users/{username}")
async def api_delete_user(username: str, request: Request):
    session = _get_session(request.cookies.get("eda_session", ""))
    if not session or session.get("role") != "admin":
        raise HTTPException(403, "Admin access required")
    if username == session["username"]:
        raise HTTPException(400, "Cannot delete yourself")
    users = _get_users()
    if username not in users:
        raise HTTPException(404, "User not found")
    del users[username]
    _save_users(users)
    return {"ok": True}

@app.put("/api/auth/users/{username}/reset-password")
async def api_reset_password(username: str, request: Request):
    session = _get_session(request.cookies.get("eda_session", ""))
    if not session or session.get("role") != "admin":
        raise HTTPException(403, "Admin access required")
    body = await request.json()
    new_pw = body.get("password", "")
    if len(new_pw) < 2:
        raise HTTPException(400, "Password too short")
    users = _get_users()
    if username not in users:
        raise HTTPException(404, "User not found")
    users[username]["password_hash"] = _hash_password(new_pw)
    _save_users(users)
    return {"ok": True}

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
    import traceback
    print(f"[ERROR] schematic_api failed to load — all /api/pcb, /api/netlist, /api/pipeline routes will 404!")
    traceback.print_exc()


# ── Upload ────────────────────────────────────────────────────────────────────
@app.post("/upload")
async def upload_datasheet(
    request: Request,
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

    # API key comes from the browser cookie — not stored in Redis, only in the
    # transient queue payload consumed immediately by the worker.
    api_key = request.cookies.get("anthropic_api_key", "")

    payload = json.dumps({"job_id": job_id, "pdf": str(dest), "brief": brief, "api_key": api_key})
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


# ── Ticket dispatcher startup ─────────────────────────────────────────────────
@app.on_event("startup")
async def _start_ticket_dispatcher():
    try:
        from schematic_api import _ticket_dispatcher  # noqa: PLC0415
        asyncio.create_task(_ticket_dispatcher())
        print("[startup] ticket dispatcher started", flush=True)
    except Exception as e:
        print(f"[startup] ticket dispatcher failed to start: {e}", flush=True)


# ── Static frontend ───────────────────────────────────────────────────────────
# We must NOT use StaticFiles at "/" because it shadows all /api/* routes.
# Instead mount under "/static" for assets and serve HTML pages explicitly.
_static_dir = _PROJECT_ROOT / "frontend" / "static"
_static_dir.mkdir(parents=True, exist_ok=True)

from starlette.staticfiles import StaticFiles as _SF
from starlette.responses import Response as _Resp

class _NoCacheJS(_SF):
    """Static files — always revalidate .js and .html so browsers pick up updates."""
    async def get_response(self, path: str, scope):
        resp = await super().get_response(path, scope)
        if path.endswith(('.js', '.html')):
            resp.headers['Cache-Control'] = 'no-cache, must-revalidate'
        return resp

app.mount("/static", _NoCacheJS(directory=str(_static_dir)), name="assets")

# Serve HTML pages at their natural paths via explicit routes
@app.get("/login", include_in_schema=False)
@app.get("/login.html", include_in_schema=False)
async def serve_login(request: Request):
    # If already logged in, redirect to app
    token = request.cookies.get("eda_session", "")
    if _get_session(token):
        return RedirectResponse("/", status_code=302)
    return FileResponse(str(_static_dir / "login.html"))

@app.get("/", include_in_schema=False)
async def serve_index():
    return FileResponse(str(_static_dir / "index.html"))

@app.get("/pcb.html", include_in_schema=False)
async def serve_pcb():
    return FileResponse(str(_static_dir / "pcb.html"), headers={"Cache-Control": "no-cache"})
