"""Download and cache background gameplay footage with yt-dlp.

Files land in ``assets/cache/footage/<category>/<video_id>.mp4``, trimmed to the
first N seconds (config: ``footage.seconds_per_clip``) so they stay small — the
render engine loops/trims them to narration length anyway.

ToS note: downloading from YouTube is contrary to YouTube's Terms of Service.
The queries target creators who publish "no copyright / free to use" gameplay
for exactly this purpose, but attribution/usage terms vary per creator and are
your responsibility. Set ``footage.allow_youtube = false`` in config.toml to
disable, and supply your own clips in ``assets/backgrounds/`` instead.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import time
from pathlib import Path

from ..config import get_settings
from . import registry

log = logging.getLogger("brainrotter.footage")

VIDEO_EXTS = {".mp4", ".mkv", ".webm", ".mov", ".m4v"}

# yt-dlp's exact `license` string for a YouTube video marked reusable.
CC_LICENSE = "Creative Commons Attribution license (reuse allowed)"
# Category prefix for narrative b-roll pulled for a series (kept out of the
# gameplay pools — see assets.SERIES_PREFIX).
SERIES_PREFIX = "~"


def cached(category: str | None = None) -> dict[str, list[Path]]:
    root = get_settings().footage_path
    out: dict[str, list[Path]] = {}
    for cat_dir in root.iterdir() if root.exists() else []:
        if not cat_dir.is_dir():
            continue
        if category and cat_dir.name != category:
            continue
        clips = [p for p in cat_dir.iterdir() if p.suffix.lower() in VIDEO_EXTS]
        if clips:
            out[cat_dir.name] = sorted(clips)
    return out


def have_enough(category: str) -> bool:
    settings = get_settings()
    return len(cached(category).get(category, [])) >= settings.footage.per_category


def slugify(name: str) -> str:
    import re

    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "figure"


def ensure(category: str, *, count: int | None = None,
           queries: list[str] | None = None,
           require_license: str | None = None) -> list[str]:
    """Guarantee at least ``count`` clips for a category; download if short."""
    settings = get_settings()
    count = count or settings.footage.per_category
    have = cached(category).get(category, [])
    if len(have) >= count:
        return [str(p) for p in have]
    if not settings.footage.allow_youtube:
        return [str(p) for p in have]
    fetched = sync(category, count=count - len(have), queries=queries,
                   require_license=require_license)
    return [str(p) for p in cached(category).get(category, [])] or fetched


def ensure_figure(name: str, *, count: int | None = None) -> list[str]:
    """Footage OF a public figure (speeches / interviews), for anime_figure."""
    return ensure(f"@{slugify(name)}", count=count,
                  queries=registry.figure_queries(name))


def ensure_narrative(series_id: str, part: int, queries: list[str], *,
                     count: int | None = None) -> list[str]:
    """Creative-Commons b-roll for one part of a series, matching its scene.

    Kept in a per-part ``~<series>-<part>`` category so it never blends into the
    gameplay pools. If nothing Creative-Commons turns up and ``series.require_cc``
    is off, retries unfiltered; the caller falls back to gameplay if still empty.
    """
    s = get_settings()
    cat = f"{SERIES_PREFIX}{series_id}-{part}"
    n = count or s.series.broll_per_part
    lic = CC_LICENSE if s.series.require_cc else None
    return ensure(cat, count=n, queries=queries, require_license=lic)


def sync(category: str, *, count: int | None = None,
         queries: list[str] | None = None,
         require_license: str | None = None) -> list[str]:
    """Download up to ``count`` new source videos for one category.

    ``require_license`` (e.g. ``CC_LICENSE``) restricts downloads to videos
    carrying that yt-dlp ``license`` string — used for series b-roll so the
    footage is genuinely free to reuse.
    """
    settings = get_settings()
    if not settings.footage.allow_youtube:
        raise RuntimeError("footage.allow_youtube is false — enable it or add clips manually")

    try:
        import yt_dlp
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("yt-dlp not installed (pip install yt-dlp)") from exc

    count = count or settings.footage.per_category
    dst = settings.footage_path / category
    raw = dst / "_raw"
    if raw.exists():   # clear leftovers from an interrupted sync
        shutil.rmtree(raw, ignore_errors=True)
    dst.mkdir(parents=True, exist_ok=True)
    raw.mkdir(exist_ok=True)
    existing_ids = {p.stem for p in dst.iterdir() if p.suffix.lower() in VIDEO_EXTS}

    maxres = settings.footage.max_resolution
    secs = settings.footage.seconds_per_clip
    # Grab only the first (secs + 20)s of each source via yt-dlp's section
    # download (ffmpeg fetches just that byte range) — keeps files ~10-25MB.
    ydl_opts = {
        # DASH video+audio: the section download goes through ffmpeg's remote
        # -ss/-to, which YouTube serves fast; progressive formats get throttled.
        "format": f"bv*[height<={maxres}]+ba/b[height<={maxres}]/best",
        "outtmpl": str(raw / "%(id)s.%(ext)s"),
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "ignoreerrors": True,
        "retries": 2,
        "socket_timeout": 20,
        "playlist_items": f"1:{count}",   # cap clips pulled per search
        "download_ranges": yt_dlp.utils.download_range_func(None, [(0, secs + 20)]),
        # False = ffmpeg -ss/-to on the remote stream (true partial download);
        # True would pull the whole file to align keyframes.
        "force_keyframes_at_cuts": False,
        # prefer short-ish uploads; skip livestreams
        "match_filter": yt_dlp.utils.match_filter_func(
            f'duration > 15 & duration < 1800 & !is_live & license = "{require_license}"'
            if require_license else "duration > 45 & !is_live"
        ),
    }
    fc = settings.footage
    cookie_txt = Path(fc.cookies_file) if fc.cookies_file else (settings.root / "assets" / "cookies.txt")
    if cookie_txt.is_file():
        ydl_opts["cookiefile"] = str(cookie_txt.resolve())
    elif fc.cookies_from_browser:
        ydl_opts["cookiesfrombrowser"] = (fc.cookies_from_browser,)
    else:
        ydl_opts["extractor_args"] = {"youtube": {"player_client": ["ios", "mweb", "web"]}}

    # YouTube now requires solving a JS "n" challenge to get real formats.
    # Enable a JS runtime — Deno if present, else Node.
    node = shutil.which("node") or shutil.which("node.exe")
    ydl_opts["js_runtimes"] = {"deno": {}}
    if node:
        ydl_opts["js_runtimes"]["node"] = {"path": node}

    q_list = queries or registry.queries_for(category)
    targets = [f"ytsearch{count + 2}:{q}" for q in q_list]
    if not queries:
        targets += registry.fallback_urls_for(category)

    deadline = time.time() + settings.footage.sync_budget_seconds
    consecutive_fail = 0
    got: list[str] = []
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        for target in targets:
            if len(got) >= count or time.time() > deadline or consecutive_fail >= 3:
                break
            try:
                info = ydl.extract_info(target, download=True)
                consecutive_fail = 0
            except Exception as exc:  # noqa: BLE001
                consecutive_fail += 1
                log.warning("footage fetch failed for %s: %s", target, exc)
                continue
            for e in (info.get("entries", [info]) if info else []) or []:
                vid = e.get("id") if e else None
                if not vid or vid in existing_ids:
                    continue
                src = next((p for p in raw.iterdir() if p.stem == vid
                            and p.suffix.lower() in VIDEO_EXTS), None)
                if not src:
                    continue
                final = _trim(src, dst / f"{vid}.mp4", settings)
                src.unlink(missing_ok=True)
                if final:
                    got.append(str(final))
                    existing_ids.add(vid)
                if len(got) >= count:
                    break
    try:
        raw.rmdir()
    except OSError:
        pass
    log.info("footage sync %s: +%d clip(s)", category, len(got))
    return got


def _probe_duration(path: Path, ff_bin: str) -> float:
    probe = shutil.which("ffprobe") or ff_bin.replace("ffmpeg", "ffprobe")
    try:
        out = subprocess.run(
            [probe, "-v", "quiet", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=30,
        ).stdout.strip()
        return float(out) if out else 0.0
    except Exception:
        return 0.0


def _trim(src: Path, dst: Path, settings) -> Path | None:
    """Re-encode to a small, silent, capped-length background loop."""
    ff = shutil.which(settings.ffmpeg_bin) or "ffmpeg"
    secs = settings.footage.seconds_per_clip
    h = settings.footage.max_resolution
    src_dur = _probe_duration(src, ff)
    if src_dur < 8:
        log.warning("source too short (%.1fs), skipping: %s", src_dur, src.name)
        return None
    skip = 15 if src_dur > secs + 20 else 0        # skip intro only if room
    # Force a uniform landscape frame: scale up to cover 854xH then centre-crop.
    # Guarantees both dimensions clear the engine's 472px minimum and every
    # background clip is the same size (safe to concat). A portrait source that
    # slipped into a gameplay category loses its edges — it was the wrong clip
    # for a full-screen background anyway.
    hh = max(h, 480)
    ww = (hh * 16) // 9
    vf = (f"scale={ww}:{hh}:force_original_aspect_ratio=increase,"
          f"crop={ww}:{hh},setsar=1")
    cmd = [
        ff, "-y", "-ss", str(skip), "-i", str(src), "-t", str(secs),
        "-an", "-vf", vf, "-c:v", "libx264", "-preset", "veryfast",
        "-crf", "27", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(dst),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=600)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        log.warning("trim failed for %s: %s", src.name, exc)
        return None
    if not dst.is_file() or _probe_duration(dst, ff) < 8:
        dst.unlink(missing_ok=True)
        return None
    return dst


def sync_all(*, per_category: int | None = None) -> dict[str, int]:
    return {c: len(sync(c, count=per_category)) for c in registry.categories()}


_growing = False


def autogrow() -> str | None:
    """Best-effort: if the cached gameplay pool is thin, download ONE more
    category. Meant to be called in a daemon thread after a render — never
    raises, and only one runs at a time.

    Returns the category it grew, or None.
    """
    global _growing
    settings = get_settings()
    target = settings.footage.auto_grow_to
    if _growing or target <= 0 or not settings.footage.allow_youtube:
        return None
    try:
        from .. import assets

        have = assets.available_categories()
        if len(have) >= target:
            return None
        missing = [c for c in registry.categories() if c not in have]
        if not missing:
            return None
        import random

        cat = random.choice(missing)
        _growing = True
        try:
            got = sync(cat, count=settings.footage.per_category)
        finally:
            _growing = False
        log.info("autogrow: %s +%d", cat, len(got))
        return cat if got else None
    except Exception as exc:  # noqa: BLE001
        _growing = False
        log.warning("autogrow failed: %s", exc)
        return None
