"""Local dashboard + job queue.

A background worker thread drains queued jobs one at a time (rendering is
CPU-heavy; no point running them in parallel on one box). The Director runs
per job unless the request pinned a format/topic.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .. import db, orchestrator
from ..config import get_settings
from ..formats import registry

app = FastAPI(title="Brainrotter")
app.mount(
    "/static",
    StaticFiles(directory=Path(__file__).parent / "static"),
    name="static",
)
_worker_started = False
_worker_lock = threading.Lock()
# Machine-wide mutex: only the process that can bind this port runs the worker,
# so a second `brainrotter serve` never spawns a competing worker.
_WORKER_MUTEX_PORT = 47654
_mutex_sock: socket.socket | None = None


def _acquire_worker_mutex() -> bool:
    global _mutex_sock
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", _WORKER_MUTEX_PORT))
        s.listen(1)
        _mutex_sock = s  # keep it alive for the process lifetime
        return True
    except OSError:
        s.close()
        return False


class JobRequest(BaseModel):
    format_id: str | None = None
    topic: str | None = None
    count: int = 1
    publish_to: list[str] | None = None


def _worker_loop() -> None:
    db.init_db()
    db.reap_stale_jobs()
    while True:
        # claim_next_job returns a job ONLY when nothing is in flight and the
        # queue isn't paused — strict one-at-a-time.
        job = db.claim_next_job()
        if not job:
            time.sleep(2)
            continue
        job_id = job["id"]
        try:
            brief = orchestrator.plan_brief(
                format_id=job.get("format_id") or None,
                topic=job.get("topic") or None,
            )
            orchestrator.produce(brief, job_id=job_id)
        except Exception as exc:  # already recorded on the job by produce()
            db.update_job(job_id, state="failed", error=f"{type(exc).__name__}: {exc}")


def _ensure_worker() -> None:
    global _worker_started
    with _worker_lock:
        if _worker_started:
            return
        if not _acquire_worker_mutex():
            # another brainrotter process already owns the worker
            _worker_started = True
            return
        threading.Thread(target=_worker_loop, name="brainrotter-worker", daemon=True).start()
        _worker_started = True


@app.on_event("startup")
def _startup() -> None:
    db.init_db()
    _ensure_worker()


@app.post("/api/jobs")
def create_jobs(req: JobRequest) -> dict:
    if req.format_id and req.format_id not in registry.all_ids():
        raise HTTPException(400, f"unknown format '{req.format_id}'")
    ids = [
        db.create_job(format_id=req.format_id, topic=req.topic)
        for _ in range(max(1, min(req.count, 50)))
    ]
    _ensure_worker()
    return {"queued": ids, **db.queue_summary()}


@app.get("/api/queue")
def queue_status() -> dict:
    return db.queue_summary()


@app.post("/api/queue/pause")
def queue_pause() -> dict:
    db.set_paused(True)
    return db.queue_summary()


@app.post("/api/queue/resume")
def queue_resume() -> dict:
    db.set_paused(False)
    _ensure_worker()
    return db.queue_summary()


@app.post("/api/queue/clear")
def queue_clear() -> dict:
    n = db.cancel_queued()
    return {"canceled": n, **db.queue_summary()}


class HistoryClearRequest(BaseModel):
    keep_last: int = 0


@app.post("/api/jobs/clear-history")
def clear_history(req: HistoryClearRequest) -> dict:
    from .. import maintenance

    return maintenance.clear_history(keep_last=max(0, req.keep_last))


@app.get("/api/jobs")
def list_jobs(limit: int = 50) -> dict:
    out = []
    for j in db.list_jobs(limit):
        for key in ("brief_json", "script_json", "result_json"):
            if j.get(key):
                try:
                    j[key.replace("_json", "")] = json.loads(j.pop(key))
                except Exception:
                    j.pop(key, None)
        out.append(j)
    return {"jobs": out}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    j = db.get_job(job_id)
    if not j:
        raise HTTPException(404, "no such job")
    return j


@app.get("/api/formats")
def formats() -> dict:
    stats = db.format_stats()
    return {"formats": [{**f, "stats": stats.get(f["id"], {})} for f in registry.describe()]}


@app.get("/api/trends")
def trends(limit: int = 15) -> dict:
    from .. import trends as tr

    return {"signals": [s.model_dump() for s in tr.gather(limit=limit)]}


@app.get("/api/video/{job_id}")
def video(job_id: str) -> FileResponse:
    path = get_settings().out_path / f"{job_id}.mp4"
    if not path.is_file():
        raise HTTPException(404, "no video for that job yet")
    # FileResponse honours HTTP Range (206) — needed for <video> seeking/streaming.
    # Videos are immutable (keyed by job id) so let the browser cache them.
    return FileResponse(
        path, media_type="video/mp4",
        headers={"Accept-Ranges": "bytes", "Cache-Control": "public, max-age=86400"},
    )


_STATIC = Path(__file__).parent / "static"


@app.get("/favicon.ico", include_in_schema=False)
@app.get("/icon.svg", include_in_schema=False)
def favicon():
    logo = get_settings().root / "extension" / "logo.svg"
    if logo.is_file():
        return FileResponse(logo, media_type="image/svg+xml")
    raise HTTPException(404)


@app.get("/manifest.webmanifest", include_in_schema=False)
def manifest():
    f = _STATIC / "manifest.webmanifest"
    return FileResponse(f, media_type="application/manifest+json")


@app.get("/", response_class=HTMLResponse)
def dashboard() -> str:
    return (_STATIC / "index.html").read_text(encoding="utf-8")
