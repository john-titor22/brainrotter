"""Local dashboard + job queue.

A background worker thread drains queued jobs one at a time (rendering is
CPU-heavy; no point running them in parallel on one box). The Director runs
per job unless the request pinned a format/topic.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import socket
import threading
import time
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

MOVIE_EXTS = {".mp4", ".mkv", ".mov", ".webm", ".m4v", ".avi"}

from .. import db, orchestrator
from ..config import get_settings
from ..formats import registry

log = logging.getLogger("brainrotter.server")

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
    language: str | None = None      # en | ary  (blank = Director decides)
    voice: str | None = None         # edge-tts short name (blank = Director decides)
    visuals: str | None = None       # footage | generated  (blank = Director decides)
    count: int = 1
    publish_to: list[str] | None = None
    series: bool = False             # "related story": produce Part 1..N of one arc
    parts: int | None = None         # target part count (capped at series.max_parts)


def _worker_loop() -> None:
    db.init_db()
    db.reap_stale_jobs()
    _last_reap = time.time()
    while True:
        # claim_next_job returns a job ONLY when nothing is in flight and the
        # queue isn't paused — strict one-at-a-time.
        job = db.claim_next_job()
        if not job:
            # A job whose worker was hard-killed mid-render (OOM, segfault, power
            # loss) stays 'rendering' forever and blocks the queue until a
            # restart. Sweep for those periodically, not just at startup.
            if time.time() - _last_reap > 300:
                n = db.reap_stale_jobs()
                if n:
                    log.warning("reaped %d orphaned job(s)", n)
                _last_reap = time.time()
            time.sleep(2)
            continue
        job_id = job["id"]
        try:
            if job.get("series_id"):
                from .. import series as _series

                brief = _series.build_part_brief(job["series_id"], job["part"])
            else:
                ov = db.job_overrides(job)
                brief = orchestrator.plan_brief(
                    format_id=job.get("format_id") or None,
                    topic=job.get("topic") or None,
                    language=ov.get("language") or None,
                    voice=ov.get("voice") or None,
                    visual_treatment=ov.get("visuals") or None,
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


def _quiet_connection_resets() -> None:
    """Windows' Proactor loop logs a scary traceback every time a browser drops
    a connection mid-response (video range requests, cancelled polls). It's
    harmless — swallow just that one."""
    import asyncio

    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        return
    default = loop.get_exception_handler()

    def handler(l, context):
        exc = context.get("exception")
        if isinstance(exc, (ConnectionResetError, ConnectionAbortedError)):
            return
        if getattr(exc, "winerror", None) in (10054, 10053):
            return
        (default or l.default_exception_handler)(context)

    loop.set_exception_handler(handler)


@app.on_event("startup")
def _startup() -> None:
    db.init_db()
    _quiet_connection_resets()
    _ensure_worker()


@app.post("/api/jobs")
def create_jobs(req: JobRequest) -> dict:
    from ..engine import voices

    if req.format_id and req.format_id not in registry.all_ids():
        raise HTTPException(400, f"unknown format '{req.format_id}'")
    if req.voice and req.voice not in voices._BY_NAME:
        raise HTTPException(400, f"unknown voice '{req.voice}'")
    if req.visuals and req.visuals not in ("footage", "generated"):
        raise HTTPException(400, f"unknown visuals '{req.visuals}'")
    lang = req.language
    if req.voice and not lang:
        lang = voices.language_of(req.voice)

    if req.series:
        from .. import series as _series

        info = _series.create(topic=req.topic, target_parts=req.parts,
                              format_id=req.format_id, language=lang, voice=req.voice)
        ids = _series.enqueue(info["series_id"])
        _ensure_worker()
        return {"series": info, "queued": ids, **db.queue_summary()}

    overrides = {"language": lang, "voice": req.voice, "visuals": req.visuals}
    ids = [
        db.create_job(format_id=req.format_id, topic=req.topic, overrides=overrides)
        for _ in range(max(1, min(req.count, 50)))
    ]
    _ensure_worker()
    return {"queued": ids, **db.queue_summary()}


@app.get("/api/voices")
def voices_catalog() -> dict:
    from ..engine import voices

    names = {"en": "English", "ary": "Darija"}
    by_lang = voices.catalog_by_language()
    return {
        "languages": [
            {"code": c, "label": names.get(c, c)}
            for c in ("en", "ary") if c in by_lang
        ],
        "voices": {
            lang: [
                {"name": v.name, "label": v.label, "gender": v.gender}
                for v in vs
            ]
            for lang, vs in by_lang.items()
        },
    }


@app.get("/api/capabilities")
def capabilities() -> dict:
    from .. import avatar, visuals

    return {
        "generated_visuals": visuals.available(),
        "generated_visuals_status": visuals.status(),
        "talking_head": avatar.is_installed(),
    }


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
        if j.get("state") == "done":
            v = db.get_video_by_job(j["id"])
            if v:
                try:
                    urls = json.loads(v.get("platform_urls") or "{}")
                except Exception:
                    urls = {}
                j["publish"] = {
                    "published_at": v.get("published_at"),
                    "urls": urls,
                    "error": v.get("publish_error"),
                    "local_deleted": bool(v.get("local_deleted")),
                }
        out.append(j)
    from .. import publish as _pub

    p = get_settings().publish
    return {"jobs": out, "publish": {
        "available": _pub.available(),
        "configured": _pub.any_ready(),
        "enabled": p.enabled, "auto": _pub.auto_publish_on(),
        "platforms": [x["name"] for x in _pub.provider_status()
                      if x["authed"] or (x["name"] == "upload_post" and x["configured"])],
        "providers": _pub.provider_status(),
        "status": _pub.status(),
    }}


class PublishReq(BaseModel):
    job_id: str
    platforms: list[str] | None = None


@app.post("/api/publish")
def publish_video(req: PublishReq) -> dict:
    from .. import publish as _pub

    if not _pub.any_ready():
        raise HTTPException(400, "no publish provider ready — `brainrotter publish-auth "
                                 "youtube` (etc.) or set UPLOAD_POST_API_KEY")
    res = _pub.publish_job(req.job_id, req.platforms)
    if not res.get("ok"):
        raise HTTPException(502, res.get("error") or "publish failed")
    return res


class AutoPubReq(BaseModel):
    auto_publish: bool


@app.post("/api/publish/settings")
def publish_settings(req: AutoPubReq) -> dict:
    from .. import publish as _pub

    _pub.set_auto_publish(req.auto_publish)
    return {"auto_publish": _pub.auto_publish_on()}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    j = db.get_job(job_id)
    if not j:
        raise HTTPException(404, "no such job")
    return j


@app.get("/api/series")
def series_list(limit: int = 15) -> dict:
    out = []
    for s in db.list_series(limit):
        try:
            plan = json.loads(s.get("plan_json") or "{}")
        except Exception:
            plan = {}
        out.append({
            "id": s["id"], "title": plan.get("title") or s.get("topic"),
            "topic": s.get("topic"), "format_id": s.get("format_id"),
            "language": s.get("language"), "state": s.get("state"),
            "n_parts": s.get("n_parts"), "parts_done": s.get("parts_done", 0),
            "parts_failed": s.get("parts_failed", 0),
            "parts": [
                {"n": p.get("n"), "goal": p.get("goal"), "cliffhanger": p.get("cliffhanger")}
                for p in (plan.get("parts") or [])
            ],
        })
    return {"series": out}


# --- movie_recap: its own dashboard tab -----------------------------------

def _safe_movie_name(name: str) -> str:
    name = Path(name or "").name
    stem, ext = Path(name).stem, Path(name).suffix.lower()
    if ext not in MOVIE_EXTS:
        raise HTTPException(400, f"not a movie file: {name}")
    stem = re.sub(r"[^A-Za-z0-9 ._()\[\]-]+", "_", stem).strip() or "movie"
    return stem + ext


@app.get("/api/movies")
def movies_list() -> dict:
    from ..engine import movie as _m
    from ..formats.movie_recap import _title_from_file

    root = get_settings().movies_path
    files = [p for p in sorted(root.rglob("*")) if p.suffix.lower() in MOVIE_EXTS]

    series_by_path: dict[str, dict] = {}
    for s in db.list_series(80):
        if s.get("format_id") != "movie_recap":
            continue
        try:
            mp = json.loads(s.get("plan_json") or "{}").get("movie_path", "")
        except Exception:
            mp = ""
        if mp:
            series_by_path[str(Path(mp).resolve()).lower()] = s

    out = []
    for p in files:
        s = series_by_path.get(str(p.resolve()).lower())
        out.append({
            "file": p.name,
            "title": _title_from_file(p),
            "seconds": round(_m.probe_duration(p), 1),
            "size_mb": round(p.stat().st_size / 1e6, 1),
            "series": None if not s else {
                "id": s["id"], "state": s["state"], "n_parts": s["n_parts"],
                "parts_done": s.get("parts_done", 0), "parts_failed": s.get("parts_failed", 0),
            },
        })
    return {"movies": out, "dir": str(root)}


@app.post("/api/movies/upload")
async def movies_upload(file: UploadFile = File(...)) -> dict:
    dst = get_settings().movies_path / _safe_movie_name(file.filename)
    tmp = dst.with_suffix(dst.suffix + ".part")
    size = 0
    with tmp.open("wb") as f:
        while chunk := await file.read(1 << 20):
            f.write(chunk)
            size += len(chunk)
    if size < 100_000:
        tmp.unlink(missing_ok=True)
        raise HTTPException(400, "file too small to be a movie")
    tmp.replace(dst)
    return {"file": dst.name, "size_mb": round(size / 1e6, 1)}


class MovieLinkRequest(BaseModel):
    path: str


@app.post("/api/movies/link")
def movies_link(req: MovieLinkRequest) -> dict:
    """Use a movie already on this machine — hardlink it in (instant, no copy),
    falling back to a copy across drives."""
    src = Path(req.path.strip().strip('"'))
    if not src.is_file():
        raise HTTPException(400, f"no file at {src}")
    dst = get_settings().movies_path / _safe_movie_name(src.name)
    if dst.exists():
        return {"file": dst.name, "note": "already added"}
    try:
        dst.hardlink_to(src)
    except (OSError, NotImplementedError):
        shutil.copy2(src, dst)
    return {"file": dst.name, "size_mb": round(dst.stat().st_size / 1e6, 1)}


@app.delete("/api/movies/{name}")
def movies_delete(name: str) -> dict:
    p = get_settings().movies_path / _safe_movie_name(name)
    if p.is_file():
        p.unlink()
    return {"removed": p.name}


class MovieRecapRequest(BaseModel):
    file: str
    parts: int | None = None


@app.post("/api/movies/recap")
def movies_recap(req: MovieRecapRequest) -> dict:
    from .. import series as _series
    from ..formats.movie_recap import _title_from_file

    p = get_settings().movies_path / _safe_movie_name(req.file)
    if not p.is_file():
        raise HTTPException(404, f"no movie {req.file}")
    try:
        info = _series.create(topic=_title_from_file(p), target_parts=req.parts,
                              format_id="movie_recap", language="en")
        ids = _series.enqueue(info["series_id"])
    except Exception as exc:
        raise HTTPException(400, str(exc))
    _ensure_worker()
    return {"series": info, "queued": ids, **db.queue_summary()}


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
        v = db.get_video_by_job(job_id)
        if v and v.get("local_deleted"):
            raise HTTPException(410, "published — local file freed (see platform links)")
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
