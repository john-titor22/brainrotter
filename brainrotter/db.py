"""SQLite persistence: job queue + video history + performance metrics.

Deliberately thin — a handful of helper functions over raw SQL. No ORM.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from .config import get_settings

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id            TEXT PRIMARY KEY,
    state         TEXT NOT NULL,
    format_id     TEXT,
    topic         TEXT,
    brief_json    TEXT,
    script_json   TEXT,
    result_json   TEXT,
    overrides_json TEXT,
    error         TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS videos (
    id            TEXT PRIMARY KEY,
    job_id        TEXT NOT NULL,
    format_id     TEXT,
    topic         TEXT,
    path          TEXT,
    duration      REAL,
    created_at    TEXT NOT NULL,
    published_at  TEXT,
    platforms     TEXT
);

CREATE TABLE IF NOT EXISTS metrics (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id      TEXT NOT NULL,
    platform      TEXT NOT NULL,
    captured_at   TEXT NOT NULL,
    views         INTEGER DEFAULT 0,
    likes         INTEGER DEFAULT 0,
    comments      INTEGER DEFAULT 0,
    shares        INTEGER DEFAULT 0,
    avg_watch_pct REAL
);

CREATE TABLE IF NOT EXISTS format_stats (
    format_id     TEXT PRIMARY KEY,
    n_videos      INTEGER DEFAULT 0,
    n_published   INTEGER DEFAULT 0,
    ewma_score    REAL DEFAULT 0.0,
    updated_at    TEXT
);
"""


# A job is "in flight" once a worker starts on it — no new job begins until
# every in-flight job is done/failed/canceled. This serialises production even
# if two workers (or a CLI run + the dashboard) are alive at once.
IN_FLIGHT_STATES = (
    "directing", "writing", "synthesizing", "aligning", "assembling", "rendering",
)
_TERMINAL = ("done", "failed", "canceled")


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    settings = get_settings()
    conn = sqlite3.connect(settings.db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 30000")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with connect() as conn:
        conn.executescript(_SCHEMA)
        # migrations for existing DBs
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(jobs)")}
        if "overrides_json" not in cols:
            conn.execute("ALTER TABLE jobs ADD COLUMN overrides_json TEXT")


# --- meta (small key/value store: paused flag, etc.) ------------------------

def meta_get(key: str, default: str | None = None) -> str | None:
    with connect() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT)")
        row = conn.execute("SELECT v FROM meta WHERE k = ?", (key,)).fetchone()
    return row["v"] if row else default


def meta_set(key: str, value: str) -> None:
    with connect() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT)")
        conn.execute(
            "INSERT INTO meta (k, v) VALUES (?, ?) "
            "ON CONFLICT(k) DO UPDATE SET v = excluded.v",
            (key, value),
        )


def is_paused() -> bool:
    return meta_get("queue_paused", "0") == "1"


def set_paused(paused: bool) -> None:
    meta_set("queue_paused", "1" if paused else "0")


# --- jobs ---------------------------------------------------------------------

def create_job(*, format_id: str | None = None, topic: str | None = None,
               overrides: dict | None = None) -> str:
    job_id = uuid.uuid4().hex[:12]
    now = _utcnow()
    ov = json.dumps({k: v for k, v in (overrides or {}).items() if v}) if overrides else None
    with connect() as conn:
        conn.execute(
            "INSERT INTO jobs (id, state, format_id, topic, overrides_json, created_at, updated_at) "
            "VALUES (?, 'queued', ?, ?, ?, ?, ?)",
            (job_id, format_id, topic, ov, now, now),
        )
    return job_id


def job_overrides(job: dict) -> dict:
    try:
        return json.loads(job.get("overrides_json") or "{}") or {}
    except Exception:
        return {}


def update_job(job_id: str, **fields: Any) -> None:
    if not fields:
        return
    for key in ("brief", "script", "result"):
        if key in fields and not isinstance(fields[key], (str, type(None))):
            obj = fields.pop(key)
            fields[f"{key}_json"] = obj if isinstance(obj, str) else json.dumps(obj, default=str)
    fields["updated_at"] = _utcnow()
    cols = ", ".join(f"{k} = ?" for k in fields)
    with connect() as conn:
        conn.execute(f"UPDATE jobs SET {cols} WHERE id = ?", (*fields.values(), job_id))


def get_job(job_id: str) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return dict(row) if row else None


def list_jobs(limit: int = 50) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def next_queued_job() -> dict | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM jobs WHERE state = 'queued' ORDER BY created_at ASC LIMIT 1"
        ).fetchone()
    return dict(row) if row else None


def claim_next_job() -> dict | None:
    """Atomically start the oldest queued job — but ONLY if nothing else is in
    flight and the queue isn't paused. Returns the claimed job, or None."""
    placeholders = ",".join("?" * len(IN_FLIGHT_STATES))
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        busy = conn.execute(
            f"SELECT 1 FROM jobs WHERE state IN ({placeholders}) LIMIT 1",
            IN_FLIGHT_STATES,
        ).fetchone()
        if busy or is_paused():
            return None
        row = conn.execute(
            "SELECT * FROM jobs WHERE state = 'queued' ORDER BY created_at ASC LIMIT 1"
        ).fetchone()
        if not row:
            return None
        conn.execute(
            "UPDATE jobs SET state = 'directing', updated_at = ? WHERE id = ?",
            (_utcnow(), row["id"]),
        )
        return dict(row)


def reap_stale_jobs(max_age_minutes: int = 30) -> int:
    """Mark in-flight jobs that outlived a plausible render as failed (orphans
    from a killed worker)."""
    placeholders = ",".join("?" * len(IN_FLIGHT_STATES))
    cutoff = datetime.now(timezone.utc).timestamp() - max_age_minutes * 60
    with connect() as conn:
        rows = conn.execute(
            f"SELECT id, updated_at FROM jobs WHERE state IN ({placeholders})",
            IN_FLIGHT_STATES,
        ).fetchall()
        stale = [
            r["id"] for r in rows
            if _parse_ts(r["updated_at"]) < cutoff
        ]
        for jid in stale:
            conn.execute(
                "UPDATE jobs SET state = 'failed', error = 'orphaned (worker died)', "
                "updated_at = ? WHERE id = ?", (_utcnow(), jid),
            )
    return len(stale)


def _parse_ts(s: str) -> float:
    try:
        return datetime.fromisoformat(s).timestamp()
    except Exception:
        return 0.0


def cancel_queued() -> int:
    with connect() as conn:
        cur = conn.execute(
            "UPDATE jobs SET state = 'canceled', updated_at = ? WHERE state = 'queued'",
            (_utcnow(),),
        )
        return cur.rowcount


def purge_history(*, keep_last: int = 3) -> dict:
    """Drop finished jobs and their video rows: keep the ``keep_last`` most
    recent *successful* videos, drop every failed/canceled job and any older
    done jobs. Returns the video file paths the caller should delete."""
    with connect() as conn:
        keep_ids = {
            r["id"] for r in conn.execute(
                "SELECT id FROM jobs WHERE state = 'done' "
                "ORDER BY created_at DESC LIMIT ?", (max(0, keep_last),),
            ).fetchall()
        }
        rows = conn.execute(
            "SELECT id FROM jobs WHERE state IN ('done', 'failed', 'canceled')"
        ).fetchall()
        drop = [r["id"] for r in rows if r["id"] not in keep_ids]
        if not drop:
            return {"removed_jobs": 0, "paths": []}
        dph = ",".join("?" * len(drop))
        paths = [
            r["path"] for r in conn.execute(
                f"SELECT path FROM videos WHERE job_id IN ({dph})", drop
            ).fetchall() if r["path"]
        ]
        conn.execute(f"DELETE FROM videos WHERE job_id IN ({dph})", drop)
        conn.execute(f"DELETE FROM jobs WHERE id IN ({dph})", drop)
    return {"removed_jobs": len(drop), "paths": paths}


def queue_summary() -> dict:
    with connect() as conn:
        rows = conn.execute(
            "SELECT state, COUNT(*) n FROM jobs GROUP BY state"
        ).fetchall()
    counts = {r["state"]: r["n"] for r in rows}
    in_flight = sum(counts.get(s, 0) for s in IN_FLIGHT_STATES)
    return {
        "queued": counts.get("queued", 0),
        "in_flight": in_flight,
        "done": counts.get("done", 0),
        "failed": counts.get("failed", 0),
        "canceled": counts.get("canceled", 0),
        "paused": is_paused(),
    }


# --- videos ------------------------------------------------------------------

def record_video(*, job_id: str, format_id: str, topic: str, path: str, duration: float) -> str:
    vid = uuid.uuid4().hex[:12]
    with connect() as conn:
        conn.execute(
            "INSERT INTO videos (id, job_id, format_id, topic, path, duration, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (vid, job_id, format_id, topic, path, duration, _utcnow()),
        )
        conn.execute(
            "INSERT INTO format_stats (format_id, n_videos, updated_at) VALUES (?, 1, ?) "
            "ON CONFLICT(format_id) DO UPDATE SET n_videos = n_videos + 1, updated_at = excluded.updated_at",
            (format_id, _utcnow()),
        )
    return vid


def format_stats() -> dict[str, dict]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM format_stats").fetchall()
    return {r["format_id"]: dict(r) for r in rows}


def _recent_briefs(limit: int) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT brief_json FROM jobs WHERE brief_json IS NOT NULL "
            "ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    out = []
    for r in rows:
        try:
            out.append(json.loads(r["brief_json"]))
        except Exception:
            continue
    return out


def recent_background_categories(limit: int = 6) -> list[str]:
    """Background categories from the most recent jobs, newest first."""
    out: list[str] = []
    for brief in _recent_briefs(limit):
        style = brief.get("style", {}) or {}
        cats = style.get("background_categories") or (
            [style["background_category"]] if style.get("background_category") else []
        )
        out.extend(c for c in cats if c)
    return out


def _recent_style_field(field: str, limit: int) -> list[str]:
    out: list[str] = []
    for brief in _recent_briefs(limit):
        v = (brief.get("style", {}) or {}).get(field)
        if v:
            out.append(str(v))
    return out


def recent_voices(limit: int = 5) -> list[str]:
    return _recent_style_field("voice", limit)


def recent_music(limit: int = 8) -> list[str]:
    """Basenames of recently-used music files, newest first."""
    import os

    return [os.path.basename(p) for p in _recent_style_field("music_file", limit)]


def recent_music_moods(limit: int = 5) -> list[str]:
    return _recent_style_field("music_mood", limit)


def recent_languages(limit: int = 6) -> list[str]:
    out: list[str] = []
    for brief in _recent_briefs(limit):
        v = brief.get("language")
        if v:
            out.append(str(v))
    return out
