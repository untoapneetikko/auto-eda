from fastapi import FastAPI, UploadFile
from fastapi.staticfiles import StaticFiles
import redis
import json
import os
import uuid

app = FastAPI()
r = redis.Redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379"))

UPLOAD_DIR = os.getenv("UPLOAD_DIR", "data/uploads")
OUTPUT_DIR = os.getenv("OUTPUT_DIR", "data/outputs")


@app.post("/upload")
async def upload_datasheet(file: UploadFile):
    job_id = str(uuid.uuid4())
    path = os.path.join(UPLOAD_DIR, file.filename)
    os.makedirs(UPLOAD_DIR, exist_ok=True)

    with open(path, "wb") as f:
        f.write(await file.read())

    r.hset(f"job:{job_id}", mapping={"status": "queued", "pdf": path, "step": "datasheet-parser"})
    r.rpush("pipeline:queue", json.dumps({"job_id": job_id, "pdf": path, "step": "datasheet-parser"}))

    return {"status": "queued", "job_id": job_id, "file": path}


@app.get("/status/{job_id}")
async def get_status(job_id: str):
    status = r.hgetall(f"job:{job_id}")
    return {k.decode(): v.decode() for k, v in status.items()}


@app.get("/outputs/{job_id}")
async def get_outputs(job_id: str):
    outputs = {}
    for filename in os.listdir(OUTPUT_DIR):
        filepath = os.path.join(OUTPUT_DIR, filename)
        if os.path.isfile(filepath) and filename.endswith(".json"):
            with open(filepath) as f:
                outputs[filename] = json.load(f)
    return outputs


app.mount("/", StaticFiles(directory="frontend/static", html=True), name="static")
