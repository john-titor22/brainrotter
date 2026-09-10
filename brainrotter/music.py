"""Self-sourced, mood-tagged background music.

Same idea as ``footage``: the software downloads its own "no copyright" tracks
with yt-dlp into ``assets/cache/music/<mood>/<id>.mp3``, trimmed and loudness-
normalised. The Director picks a mood per video and a specific track inside it,
so the feed stops sounding like one looped song.

Moods: hype, tense, eerie, epic, chill, funny  (config: ``music.moods``).

Drop your own MP3s into ``assets/music/<mood>/`` to skip downloading — they're
merged into the same pool.

ToS note: downloading from YouTube is contrary to its Terms of Service. The
queries target channels that publish royalty-free / "no copyright" music for
creator use; per-track attribution terms vary and are your responsibility. Set
``music.allow_youtube = false`` to disable.
"""

from __future__ import annotations

import logging
import random
import shutil
import subprocess
import time
from pathlib import Path

from .config import get_settings

log = logging.getLogger("brainrotter.music")

AUDIO_EXTS = {".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg", ".opus"}

# Search terms per mood. "NCS" / "no copyright" channels are the target.
MOOD_QUERIES: dict[str, list[str]] = {
    "hype": ["no copyright hype trap type beat instrumental",
             "NCS energetic gaming background music no copyright",
             "royalty free phonk instrumental hype"],
    "tense": ["no copyright tense suspense background music instrumental",
              "royalty free dark tension underscore no copyright",
              "no copyright dramatic buildup music"],
    "eerie": ["no copyright creepy ambient background music",
              "royalty free eerie horror underscore instrumental",
              "no copyright unsettling drone music"],
    "epic": ["no copyright epic cinematic background music instrumental",
             "royalty free epic orchestral trailer music no copyright",
             "no copyright heroic cinematic music"],
    "chill": ["no copyright lofi chill background music",
              "royalty free chill hip hop instrumental no copyright",
              "no copyright calm ambient music"],
    "funny": ["no copyright quirky comedic background music instrumental",
              "royalty free goofy playful music no copyright",
              "no copyright silly ukulele background music"],
    "sad": ["no copyright sad emotional piano background music",
            "royalty free melancholic ambient instrumental no copyright",
            "no copyright slow sad strings underscore"],
    "dramatic": ["no copyright dramatic emotional cinematic music instrumental",
                 "royalty free dramatic piano and strings no copyright",
                 "no copyright emotional build up cinematic"],
    "nostalgic": ["no copyright nostalgic warm lofi background music",
                  "royalty free wistful retro synth instrumental no copyright",
                  "no copyright bittersweet memory music box"],
    "phonk": ["no copyright phonk type beat instrumental",
              "royalty free drift phonk background music no copyright",
              "no copyright aggressive phonk instrumental"],
    "dreamy": ["no copyright dreamy ethereal ambient background music",
               "royalty free dreamy synth pad instrumental no copyright",
               "no copyright soft ambient shimmer music"],
    "quirky": ["no copyright quirky playful pizzicato background music",
               "royalty free whimsical eccentric instrumental no copyright",
               "no copyright oddball comedic underscore"],
}


def moods() -> list[str]:
    return get_settings().music.moods


def _user_dir(mood: str) -> Path:
    return get_settings().music_path / mood


def _cache_dir(mood: str) -> Path:
    return get_settings().music_cache_path / mood


def _tracks_in(d: Path) -> list[Path]:
    if not d.is_dir():
        return []
    return sorted(p for p in d.iterdir() if p.suffix.lower() in AUDIO_EXTS)


def cached(mood: str | None = None) -> dict[str, list[Path]]:
    root = get_settings().music_cache_path
    out: dict[str, list[Path]] = {}
    for d in root.iterdir() if root.exists() else []:
        if d.is_dir() and not d.name.startswith("_") and (mood is None or d.name == mood):
            t = _tracks_in(d)
            if t:
                out[d.name] = t
    return out


def _slug(text: str) -> str:
    import re

    return (re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:60]) or "vibe"


def ensure_query(query: str, *, count: int | None = None) -> list[str]:
    """Fetch a couple of tracks for one specific music brief (the Director's
    per-video vibe), cached under ``cache/music/_q/<slug>/``. Returns paths;
    empty if disabled / nothing found."""
    s = get_settings()
    if not (query and s.music.enabled):
        return []
    count = count or s.music.per_video_tracks
    dst = s.music_cache_path / "_q" / _slug(query)
    have = _tracks_in(dst)
    if len(have) >= count or not s.music.allow_youtube:
        return [str(p) for p in have]
    try:
        _sync_into(dst, [
            f"{query} instrumental no copyright",
            f"{query} background music royalty free",
            f"no copyright {query}",
        ], count=count - len(have))
    except Exception as exc:  # noqa: BLE001
        log.warning("music query fetch failed for %r: %s", query, exc)
    return [str(p) for p in _tracks_in(dst)]


def library(mood: str) -> list[Path]:
    """All tracks available for a mood — user-supplied plus downloaded."""
    seen: set[str] = set()
    out: list[Path] = []
    for p in _tracks_in(_user_dir(mood)) + _tracks_in(_cache_dir(mood)):
        if p.name not in seen:
            seen.add(p.name)
            out.append(p)
    return out


def pick(mood: str, rng: random.Random, *, exclude: tuple[str, ...] = ()) -> str | None:
    """A specific track for this mood from what's already on disk, biased away
    from recently-used ones. Returns an absolute path, or None when the mood
    has nothing yet — call `ensure(mood)` first if you want it downloaded."""
    pool = library(mood)
    if not pool:
        return None
    recent = list(exclude)
    weights = [0.1 ** recent.count(p.name) for p in pool]
    if not any(weights):
        weights = [1.0] * len(pool)
    return str(rng.choices(pool, weights=weights, k=1)[0])


def ensure(mood: str, *, count: int | None = None) -> list[str]:
    settings = get_settings()
    count = count or settings.music.per_mood
    have = library(mood)
    if len(have) >= count:
        return [str(p) for p in have]
    if not (settings.music.enabled and settings.music.allow_youtube):
        return [str(p) for p in have]
    sync(mood, count=count - len(have))
    return [str(p) for p in library(mood)]


def sync(mood: str, *, count: int | None = None) -> list[str]:
    """Download up to ``count`` new tracks for one mood."""
    settings = get_settings()
    count = count or settings.music.per_mood
    return _sync_into(
        _cache_dir(mood),
        MOOD_QUERIES.get(mood, [f"no copyright {mood} background music"]),
        count=count,
    )


def _sync_into(dst: Path, queries: list[str], *, count: int) -> list[str]:
    """Download up to ``count`` new tracks matching ``queries`` into ``dst``."""
    settings = get_settings()
    if not settings.music.allow_youtube:
        raise RuntimeError("music.allow_youtube is false — add MP3s to assets/music/<mood>/ instead")
    try:
        import yt_dlp
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("yt-dlp not installed (pip install 'yt-dlp[default]')") from exc

    raw = dst / "_raw"
    if raw.exists():
        shutil.rmtree(raw, ignore_errors=True)
    dst.mkdir(parents=True, exist_ok=True)
    raw.mkdir(exist_ok=True)
    existing = {p.stem for p in _tracks_in(dst)}

    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": str(raw / "%(id)s.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "ignoreerrors": True,
        "retries": 2,
        "socket_timeout": 20,
        "playlist_items": f"1:{count}",
        # instrumentals for a 9:16 short — skip hour-long mixes and livestreams
        "match_filter": yt_dlp.utils.match_filter_func("duration > 40 & duration < 600 & !is_live"),
        "postprocessors": [
            {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"},
        ],
    }
    mc = settings.music
    cookie_txt = (Path(settings.footage.cookies_file) if settings.footage.cookies_file
                  else settings.root / "assets" / "cookies.txt")
    if cookie_txt.is_file():
        ydl_opts["cookiefile"] = str(cookie_txt.resolve())
    elif settings.footage.cookies_from_browser:
        ydl_opts["cookiesfrombrowser"] = (settings.footage.cookies_from_browser,)
    node = shutil.which("node") or shutil.which("node.exe")
    ydl_opts["js_runtimes"] = {"deno": {}}
    if node:
        ydl_opts["js_runtimes"]["node"] = {"path": node}

    targets = [f"ytsearch{count + 2}:{q}" for q in queries]
    deadline = time.time() + mc.sync_budget_seconds
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
                log.warning("music fetch failed for %s: %s", target, exc)
                continue
            for e in (info.get("entries", [info]) if info else []) or []:
                vid = e.get("id") if e else None
                if not vid or vid in existing:
                    continue
                src = next((p for p in raw.iterdir() if p.stem == vid), None)
                if not src:
                    continue
                final = _prep(src, dst / f"{vid}.mp3", settings)
                src.unlink(missing_ok=True)
                if final:
                    got.append(str(final))
                    existing.add(vid)
                if len(got) >= count:
                    break
    shutil.rmtree(raw, ignore_errors=True)
    log.info("music sync %s: +%d track(s)", dst.name, len(got))
    return got


def _prep(src: Path, dst: Path, settings) -> Path | None:
    """Trim to a short loop and normalise loudness so nothing is jarringly loud."""
    ff = shutil.which(settings.ffmpeg_bin) or "ffmpeg"
    secs = settings.music.seconds_per_track
    cmd = [
        ff, "-y", "-i", str(src), "-t", str(secs),
        "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
        "-ar", "44100", "-ac", "2", "-c:a", "libmp3lame", "-q:a", "4", str(dst),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=300)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        log.warning("music prep failed for %s: %s", src.name, exc)
        return None
    return dst if dst.is_file() and dst.stat().st_size > 20_000 else None


def sync_all(*, per_mood: int | None = None) -> dict[str, int]:
    return {m: len(sync(m, count=per_mood)) for m in moods()}
