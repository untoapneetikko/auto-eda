"""
backend/worker.py — Redis queue consumer.

Reads jobs from the 'pipeline:queue' list and runs the EDA pipeline for each.

Run as a separate process (inside Docker or alongside the API server):
    python backend/worker.py

Auth: Claude Code must be authenticated before starting the worker.
  Option A (interactive):  claude auth login
  Option B (CI/server):    set ANTHROPIC_API_KEY env var
"""
from __future__ import annotations

import json
import os
import signal
import sys
import time
from pathlib import Path

import redis

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from orchestrator import run_pipeline  # noqa: E402

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
QUEUE_KEY = "pipeline:queue"

_running = True


def _handle_signal(signum, frame):  # noqa: ANN001
    global _running
    print(f"[worker] Signal {signum} received — shutting down after current job", flush=True)
    _running = False


def main() -> None:
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    r = redis.Redis.from_url(REDIS_URL, decode_responses=True)
    print(f"[worker] Started. Listening on {REDIS_URL} queue={QUEUE_KEY}", flush=True)

    while _running:
        # Blocking pop with 5-second timeout so we can check _running periodically
        item = r.blpop(QUEUE_KEY, timeout=5)
        if item is None:
            continue

        _, raw = item
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            print(f"[worker] Bad queue payload: {exc}", flush=True)
            continue

        job_id = payload.get("job_id", "unknown")
        pdf_path = payload.get("pdf", "")
        brief = payload.get("brief", "")
        api_key = payload.get("api_key", "")

        print(f"[worker] Processing job {job_id}  pdf={pdf_path}  api_key={'set' if api_key else 'missing'}", flush=True)
        t0 = time.time()

        try:
            run_pipeline(r, job_id, pdf_path, brief, api_key=api_key)
            elapsed = time.time() - t0
            print(f"[worker] Job {job_id} done in {elapsed:.1f}s", flush=True)
        except Exception as exc:
            elapsed = time.time() - t0
            print(f"[worker] Job {job_id} FAILED in {elapsed:.1f}s: {exc}", flush=True)

    print("[worker] Exiting.", flush=True)


if __name__ == "__main__":
    main()
