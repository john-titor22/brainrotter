"""Get one good front-facing photo of a public figure for the talking head."""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

from ..config import get_settings
from ..footage import slugify

log = logging.getLogger("brainrotter.avatar")


def _dir() -> Path:
    d = get_settings().cache_path / "portraits"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_portrait(name: str) -> str | None:
    """Return a cached portrait path for the figure, downloading one if needed."""
    slug = slugify(name)
    for ext in (".jpg", ".jpeg", ".png", ".webp"):
        p = _dir() / f"{slug}{ext}"
        if p.is_file():
            return str(p)
    # user-supplied override
    manual = get_settings().root / "assets" / "portraits"
    if manual.is_dir():
        for p in manual.iterdir():
            if p.stem.lower() == slug and p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"):
                dst = _dir() / f"{slug}{p.suffix.lower()}"
                shutil.copy2(p, dst)
                return str(dst)
    return _download_portrait(name, slug)


def _download_portrait(name: str, slug: str) -> str | None:
    if not get_settings().footage.allow_youtube:  # same opt-out gate
        return None
    try:
        import yt_dlp
    except ImportError:
        return None

    out = _dir() / f"{slug}.jpg"
    fc = get_settings().footage
    ydl_opts = {
        "quiet": True, "no_warnings": True, "noprogress": True, "ignoreerrors": True,
        "playlist_items": "1",
        "outtmpl": str(_dir() / f"{slug}.%(ext)s"),
        "writethumbnail": True,
        "skip_download": True,
        "postprocessors": [{"key": "FFmpegThumbnailsConvertor", "format": "jpg"}],
    }
    cookie = get_settings().root / "assets" / "cookies.txt"
    if fc.cookies_file:
        cookie = Path(fc.cookies_file)
    if cookie.is_file():
        ydl_opts["cookiefile"] = str(cookie.resolve())
    node = shutil.which("node")
    ydl_opts["js_runtimes"] = {"deno": {}}
    if node:
        ydl_opts["js_runtimes"]["node"] = {"path": node}

    query = f"ytsearch1:{name} portrait face closeup"
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.extract_info(query, download=True)
    except Exception as exc:  # noqa: BLE001
        log.warning("portrait fetch failed for %s: %s", name, exc)

    for ext in (".jpg", ".jpeg", ".png", ".webp"):
        p = _dir() / f"{slug}{ext}"
        if p.is_file() and p.stat().st_size > 2000:
            return _sanitize(p)
    return None


def _sanitize(p: Path) -> str:
    """Re-encode to a plain 512-wide jpg SadTalker is happy with."""
    ff = shutil.which(get_settings().ffmpeg_bin) or "ffmpeg"
    fixed = p.with_name(p.stem + "_p.jpg")
    try:
        subprocess.run(
            [ff, "-y", "-i", str(p), "-vf", "scale=512:-2", "-q:v", "3", str(fixed)],
            check=True, capture_output=True, timeout=60,
        )
        p.unlink(missing_ok=True)
        fixed.rename(p.with_suffix(".jpg"))
        return str(p.with_suffix(".jpg"))
    except Exception:
        return str(p)
