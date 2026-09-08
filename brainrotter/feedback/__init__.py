"""Feedback loop — turn post-publish metrics into a score the Director can use.

v1 is a stub: it records metrics and maintains an EWMA per format in
`format_stats`. v2 predicts a Script's score before rendering.
"""

from __future__ import annotations

from datetime import datetime, timezone

from .. import db

_ALPHA = 0.3  # EWMA weight on the newest observation


def score_from_metrics(*, views: int, likes: int, comments: int,
                       shares: int, avg_watch_pct: float | None) -> float:
    """Collapse raw platform metrics into a single 0..1-ish desirability score."""
    engagement = (likes + 2 * comments + 3 * shares) / max(1, views)
    retention = (avg_watch_pct or 0.0) / 100.0
    velocity = min(1.0, views / 10000)
    return round(0.5 * retention + 0.35 * min(1.0, engagement * 20) + 0.15 * velocity, 4)


def record(video_id: str, platform: str, *, views: int, likes: int = 0,
           comments: int = 0, shares: int = 0, avg_watch_pct: float | None = None) -> float:
    now = datetime.now(timezone.utc).isoformat()
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO metrics (video_id, platform, captured_at, views, likes, "
            "comments, shares, avg_watch_pct) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (video_id, platform, now, views, likes, comments, shares, avg_watch_pct),
        )
        row = conn.execute(
            "SELECT format_id FROM videos WHERE id = ?", (video_id,)
        ).fetchone()
        fmt = row["format_id"] if row else None

    s = score_from_metrics(views=views, likes=likes, comments=comments,
                           shares=shares, avg_watch_pct=avg_watch_pct)
    if fmt:
        with db.connect() as conn:
            cur = conn.execute(
                "SELECT ewma_score, n_published FROM format_stats WHERE format_id = ?",
                (fmt,),
            ).fetchone()
            prev = cur["ewma_score"] if cur else 0.0
            n = (cur["n_published"] if cur else 0) + 1
            ewma = _ALPHA * s + (1 - _ALPHA) * prev if cur and prev else s
            conn.execute(
                "INSERT INTO format_stats (format_id, n_published, ewma_score, updated_at) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(format_id) DO UPDATE SET "
                "n_published = ?, ewma_score = ?, updated_at = ?",
                (fmt, n, ewma, now, n, ewma, now),
            )
    return s


__all__ = ["record", "score_from_metrics"]
