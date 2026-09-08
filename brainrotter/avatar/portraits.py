"""Get one good front-facing photo of a public figure for the talking head."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path

from ..config import get_settings
from ..footage import slugify

log = logging.getLogger("brainrotter.avatar")

_UA = "brainrotter/0.1 (talking-head portrait fetch)"


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

    # Wikipedia's page image is a real head photo far more often than a YouTube
    # thumbnail is — try it first, fall back to a video thumbnail.
    got = _from_wikipedia(name, slug) or _download_portrait(name, slug)
    return got


def _http_json(url: str) -> dict | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=15) as r:  # noqa: S310
            return json.loads(r.read())
    except Exception as exc:  # noqa: BLE001
        log.warning("wikipedia lookup failed: %s", exc)
        return None


def _from_wikipedia(name: str, slug: str) -> str | None:
    q = urllib.parse.quote(name)
    url = (
        "https://en.wikipedia.org/w/api.php?action=query&format=json&formatversion=2"
        f"&generator=search&gsrsearch={q}&gsrlimit=1&gsrnamespace=0"
        "&prop=pageimages&piprop=original|thumbnail&pithumbsize=768"
    )
    data = _http_json(url)
    pages = (data or {}).get("query", {}).get("pages", [])
    if not pages:
        return None
    src = (pages[0].get("original") or pages[0].get("thumbnail") or {}).get("source")
    if not src:
        return None
    raw = _dir() / f"{slug}.src"
    try:
        req = urllib.request.Request(src, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=30) as r, raw.open("wb") as f:  # noqa: S310
            shutil.copyfileobj(r, f)
    except Exception as exc:  # noqa: BLE001
        log.warning("portrait image download failed for %s: %s", name, exc)
        return None
    if raw.stat().st_size < 3000:
        raw.unlink(missing_ok=True)
        return None
    out = _sanitize(raw)
    raw.unlink(missing_ok=True)
    return out


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
    """Re-encode to a centred 512x512 jpg — SadTalker's face detector wants the
    head reasonably large and centred, not a tiny face in a 16:9 thumbnail."""
    ff = shutil.which(get_settings().ffmpeg_bin) or "ffmpeg"
    slug = p.name.split(".")[0]
    final = _dir() / f"{slug}.jpg"
    tmp = _dir() / f"{slug}.fix.jpg"
    try:
        subprocess.run(
            [ff, "-y", "-i", str(p), "-vf",
             "scale=512:512:force_original_aspect_ratio=increase,crop=512:512",
             "-q:v", "3", str(tmp)],
            check=True, capture_output=True, timeout=60,
        )
        if tmp.is_file() and tmp.stat().st_size > 3000:
            if p != final:
                p.unlink(missing_ok=True)
            tmp.replace(final)
            return str(final)
    except Exception:
        tmp.unlink(missing_ok=True)
    return str(p)
