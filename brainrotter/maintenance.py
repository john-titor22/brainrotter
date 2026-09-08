"""Housekeeping: clear old jobs, videos, and engine scratch."""

from __future__ import annotations

import shutil
import time
from pathlib import Path

from . import db
from .config import get_settings
from .engine.mpt_config import ENGINE_ROOT


def clear_history(*, keep_last: int = 3, delete_files: bool = True) -> dict:
    """Remove finished jobs (keeping the newest ``keep_last`` successful ones),
    their output videos, orphaned MP4s, and stale engine scratch."""
    result = db.purge_history(keep_last=keep_last)
    removed_files = 0
    if delete_files:
        for p in result["paths"]:
            try:
                Path(p).unlink(missing_ok=True)
                removed_files += 1
            except OSError:
                pass
        for orphan in orphaned_output_files():
            try:
                orphan.unlink(missing_ok=True)
                removed_files += 1
            except OSError:
                pass
    return {
        "removed_jobs": result["removed_jobs"],
        "removed_files": removed_files,
        "engine_dirs_cleaned": _clean_engine_scratch(),
        "staged_clips_cleaned": _clean_staged_materials(),
    }


def _clean_staged_materials(older_than_minutes: float = 30.0) -> int:
    """The engine hardlinks/copies chosen clips into storage/local_videos/;
    clear the stale ones."""
    staging = ENGINE_ROOT / "storage" / "local_videos"
    if not staging.is_dir():
        return 0
    cutoff = time.time() - older_than_minutes * 60
    n = 0
    for f in staging.iterdir():
        if f.is_file() and f.stat().st_mtime < cutoff:
            try:
                f.unlink()
                n += 1
            except OSError:
                pass
    return n


def _clean_engine_scratch(older_than_minutes: float = 5.0) -> int:
    """Delete the vendored engine's per-task folders (they pile up fast).
    Skips anything touched in the last few minutes in case a render is live."""
    tasks = ENGINE_ROOT / "storage" / "tasks"
    if not tasks.is_dir():
        return 0
    cutoff = time.time() - older_than_minutes * 60
    n = 0
    for d in tasks.iterdir():
        if d.is_dir() and d.stat().st_mtime < cutoff:
            shutil.rmtree(d, ignore_errors=True)
            n += 1
    return n


def orphaned_output_files() -> list[Path]:
    """Videos in workspace/out/ with no matching job row."""
    out = get_settings().out_path
    known = {Path(v).name for v in _all_video_paths()}
    return [p for p in out.glob("*.mp4") if p.name not in known]


def _all_video_paths() -> list[str]:
    with db.connect() as conn:
        return [r["path"] for r in conn.execute("SELECT path FROM videos").fetchall() if r["path"]]
