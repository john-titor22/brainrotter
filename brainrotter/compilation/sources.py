"""Find and download short clips for a compilation theme.

Discovery, in order of preference:
  1. Reddit API (PRAW, read-only "userless" OAuth) when REDDIT_CLIENT_ID /
     REDDIT_CLIENT_SECRET are set — free, 100 queries/min, reliable, and gives
     us ``is_video`` + duration + score to filter on.
  2. Reddit ``.rss`` feeds — keyless but rate-limited (429s under load) and
     thin on metadata, so we just hand every permalink to yt-dlp and let its
     match-filter drop the non-videos.
  3. YouTube search strings (``ytsearchN:...``) — extra variety / last resort.

Download is always yt-dlp (Reddit v.redd.it, RedGifs, Streamable, YouTube...),
partial (first ~35s), audio kept, then re-encoded to a uniform vertical clip.

Cache: ``assets/cache/compilation/<theme>/<id>.mp4``. A sidecar ``_used.json``
records which clips have already appeared in a finished video so repeats prefer
fresh material.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from ..config import get_settings
from . import themes as _themes
from .themes import THEMES

log = logging.getLogger("brainrotter.compilation")

VIDEO_EXTS = {".mp4", ".mkv", ".webm", ".mov", ".m4v"}
# Non-Reddit hosts yt-dlp handles well that carry short clips with audio.
_GOOD_DOMAINS = {"v.redd.it", "redgifs.com", "streamable.com", "youtube.com", "youtu.be"}
# Titles that scream "this is a video-game clip, not real-life footage" — the
# main way gameplay leaks into a funny/fails comp.
_GAMEPLAY_RX = re.compile(
    r"\b(gameplay|game ?play|pubg|fortnite|warzone|minecraft|roblox|valorant|"
    r"apex legends|cod|call of duty|gta ?v?|csgo|cs2|league of legends|lol clip|"
    r"speedrun|montage|kill ?cam|clutch|1v[0-9]|ranked|lobby|fps|no scope|"
    r"twitch\.tv|streamer|xbox|playstation|ps5|nintendo)\b",
    re.I,
)


@dataclass
class Candidate:
    url: str
    title: str
    source: str                # "reddit:funny" | "youtube"
    score: float = 0.0
    duration: float | None = None


# --------------------------------------------------------------------------
# cache helpers
# --------------------------------------------------------------------------

def theme_dir(theme_key: str) -> Path:
    return get_settings().compilation_cache_path / theme_key


def cached_clips(theme_key: str) -> list[Path]:
    d = theme_dir(theme_key)
    if not d.is_dir():
        return []
    return sorted(p for p in d.iterdir()
                  if p.suffix.lower() in VIDEO_EXTS and not p.name.startswith("_")
                  and p.stat().st_size > 40_000)


def _used_path(theme_key: str) -> Path:
    return theme_dir(theme_key) / "_used.json"


def _load_used_raw(theme_key: str) -> dict:
    """{"builds": int, "clips": {stem: {"n": count, "last": build_ordinal}}}.
    Migrates the old flat ``{stem: count}`` shape on read."""
    p = _used_path(theme_key)
    try:
        d = json.loads(p.read_text("utf-8")) if p.is_file() else {}
    except Exception:
        d = {}
    if "clips" not in d:
        d = {"builds": 0,
             "clips": {k: {"n": int(v), "last": 0} for k, v in d.items()
                       if isinstance(v, (int, float))}}
    return d


def load_used(theme_key: str) -> dict[str, int]:
    """map ``clip stem -> times used in a finished video`` (back-compat view)."""
    return {k: v.get("n", 0) for k, v in _load_used_raw(theme_key)["clips"].items()}


def recently_used(theme_key: str, within: int) -> set[str]:
    """Stems that appeared in one of the last ``within`` finished videos."""
    d = _load_used_raw(theme_key)
    cutoff = d["builds"] - within
    return {k for k, v in d["clips"].items() if v.get("last", 0) > cutoff}


def _recent_themes_path() -> Path:
    return get_settings().compilation_cache_path / "_recent_themes.json"


def recent_themes(n: int = 3) -> list[str]:
    try:
        return json.loads(_recent_themes_path().read_text("utf-8"))[-n:]
    except Exception:
        return []


def note_theme(theme_key: str) -> None:
    try:
        cur = json.loads(_recent_themes_path().read_text("utf-8"))
    except Exception:
        cur = []
    cur.append(theme_key)
    try:
        _recent_themes_path().write_text(json.dumps(cur[-20:]), encoding="utf-8")
    except Exception:
        pass


def mark_used(theme_key: str, stems: list[str]) -> None:
    d = _load_used_raw(theme_key)
    d["builds"] = d.get("builds", 0) + 1
    for s in stems:
        c = d["clips"].setdefault(s, {"n": 0, "last": 0})
        c["n"] += 1
        c["last"] = d["builds"]
    try:
        _used_path(theme_key).write_text(json.dumps(d), encoding="utf-8")
    except Exception:
        pass


# --------------------------------------------------------------------------
# discovery
# --------------------------------------------------------------------------

def reddit_api_available() -> bool:
    s = get_settings()
    return bool(s.reddit_client_id and s.reddit_client_secret)


def _reddit_api_candidates(theme_key: str, limit: int) -> list[Candidate]:
    s = get_settings()
    cfg = s.compilation
    try:
        import praw
    except Exception:
        return []
    try:
        reddit = praw.Reddit(
            client_id=s.reddit_client_id,
            client_secret=s.reddit_client_secret,
            user_agent=s.reddit_user_agent or "brainrotter/0.1 (compilation)",
            check_for_async=False,
        )
        reddit.read_only = True
    except Exception as exc:  # noqa: BLE001
        log.warning("reddit api init failed: %s", exc)
        return []

    theme = _themes.get(theme_key)
    out: list[Candidate] = []
    seen: set[str] = set()

    def _take(post, sub: str) -> None:
        if post.over_18 or post.stickied or getattr(post, "is_self", False):
            return
        title = (post.title or "").strip()
        if _GAMEPLAY_RX.search(title):            # gameplay clip — wrong genre
            return
        dur = None
        media = getattr(post, "secure_media", None) or getattr(post, "media", None) or {}
        rv = (media or {}).get("reddit_video") or {}
        if rv.get("duration"):
            dur = float(rv["duration"])
        domain = (getattr(post, "domain", "") or "").lower()
        is_vid = bool(getattr(post, "is_video", False)) or any(
            d in domain for d in _GOOD_DOMAINS)
        if not is_vid:
            return
        if dur is not None and not (cfg.min_source_seconds <= dur <= cfg.max_source_seconds):
            return
        url = "https://www.reddit.com" + post.permalink
        if url in seen:
            return
        seen.add(url)
        pop = min(0.9, (getattr(post, "score", 0) or 0) / 20000)
        sc = pop * (0.4 if sub.lower() in _themes._GENERIC_LC else 1.0)
        out.append(Candidate(url=url, title=title, source=f"reddit:{sub}",
                             score=sc, duration=dur))

    # rotate through several "top" windows + hot, so build #2 of a theme sees
    # different posts than build #1
    windows = list(cfg.time_filters) or ["week"]
    per = max(8, limit // max(1, len(theme.subreddits) * (len(windows) + 1)) + 6)
    for sub in theme.subreddits:
        try:
            sr = reddit.subreddit(sub)
            for tf in windows:
                for post in sr.top(time_filter=tf, limit=per):
                    _take(post, sub)
            for post in sr.hot(limit=per):
                _take(post, sub)
        except Exception as exc:  # noqa: BLE001
            log.warning("reddit api /r/%s failed: %s", sub, exc)
            continue
    out.sort(key=lambda c: c.score, reverse=True)
    return out[:limit]


_ATOM = "{http://www.w3.org/2005/Atom}"
_rss_cache: dict[str, tuple[float, list[Candidate]]] = {}
_rss_cooldown_until = 0.0        # after a 429 wall, skip keyless Reddit for a while
_BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")


def _reddit_json_candidates(theme_key: str, limit: int) -> list[Candidate]:
    """Reddit's public ``.json`` listing — richer than RSS (is_video, duration,
    score) and works from a home IP with a browser UA, though datacenter IPs and
    some connections get 403'd. Tried before RSS."""
    global _rss_cooldown_until
    import httpx

    s = get_settings()
    theme = _themes.get(theme_key)
    cache_key = f"json:{theme_key}"
    hit = _rss_cache.get(cache_key)
    if hit and time.time() - hit[0] < 1800:
        return hit[1][:limit]
    if time.time() < _rss_cooldown_until:
        return []

    cfg = s.compilation
    tf = (list(cfg.time_filters) or ["week"])[0]
    headers = {"User-Agent": s.reddit_user_agent or _BROWSER_UA,
               "Accept": "application/json"}
    out: list[Candidate] = []
    blocked = 0
    with httpx.Client(headers=headers, timeout=12, follow_redirects=True) as c:
        for sub in list(theme.subreddits[:6]):
            if blocked >= 2:
                _rss_cooldown_until = time.time() + 1200
                break
            try:
                r = c.get(f"https://www.reddit.com/r/{sub}/top.json",
                          params={"t": tf, "limit": 40, "raw_json": 1})
            except Exception:
                time.sleep(1.0)
                continue
            if r.status_code in (403, 429, 401):
                blocked += 1
                time.sleep(3.0)
                continue
            blocked = 0
            try:
                children = r.json()["data"]["children"]
            except Exception:
                continue
            for ch in children:
                p = ch.get("data") or {}
                if p.get("over_18") or p.get("stickied") or p.get("is_self"):
                    continue
                title = (p.get("title") or "").strip()
                if _GAMEPLAY_RX.search(title):
                    continue
                dur = ((p.get("media") or {}).get("reddit_video") or {}).get("duration")
                domain = (p.get("domain") or "").lower()
                is_vid = bool(p.get("is_video")) or any(d in domain for d in _GOOD_DOMAINS)
                if not is_vid:
                    continue
                if dur and not (cfg.min_source_seconds <= dur <= cfg.max_source_seconds):
                    continue
                url = "https://www.reddit.com" + (p.get("permalink") or "")
                gen = sub.lower() in _themes._GENERIC_LC
                score = min(0.9, (p.get("score", 0) or 0) / 20000) * (0.4 if gen else 1.0)
                out.append(Candidate(url=url, title=title, source=f"reddit:{sub}",
                                     score=max(score, 0.05 if gen else 0.2),
                                     duration=float(dur) if dur else None))
            time.sleep(2.0)
    if out:
        _rss_cache[cache_key] = (time.time(), out)
        log.info("compilation discover %s: %d via Reddit .json", theme_key, len(out))
    return out[:limit]


def _reddit_rss_candidates(theme_key: str, limit: int) -> list[Candidate]:
    """Keyless Reddit source. Reddit rate-limits unauthenticated RSS hard, so:
    cache 30 min, hit only the first few (topic) subreddits, big delays, and
    after a 429 wall go quiet for 20 min. YouTube is the real primary now — the
    Reddit API is approval-gated in 2026 and often unavailable."""
    global _rss_cooldown_until
    import xml.etree.ElementTree as ET

    import httpx

    s = get_settings()
    theme = _themes.get(theme_key)
    hit = _rss_cache.get(theme_key)
    if hit and time.time() - hit[0] < 1800:      # 30 min
        return hit[1][:limit]
    if time.time() < _rss_cooldown_until:
        return []

    tf = (list(s.compilation.time_filters) or ["week"])[0]
    ua = (s.reddit_user_agent
          or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/122.0 Safari/537.36")
    headers = {"User-Agent": ua}
    per = max(10, limit)
    # only the first few subs (the topic-specific ones) — every extra request
    # risks tripping the limiter
    subs = list(theme.subreddits[:5])
    out: list[Candidate] = []
    consecutive_429 = 0
    with httpx.Client(headers=headers, timeout=10, follow_redirects=True) as c:
        for sub in subs:
            if consecutive_429 >= 2:
                _rss_cooldown_until = time.time() + 1200      # 20 min quiet
                log.info("compilation RSS: 429-walled — quiet for 20 min "
                         "(YouTube carries it; Reddit API is approval-gated now)")
                break
            body = None
            for attempt in range(2):
                try:
                    r = c.get(f"https://old.reddit.com/r/{sub}/top/.rss",
                              params={"t": tf, "limit": per})
                    if r.status_code == 200:
                        body = r.text
                        consecutive_429 = 0
                        break
                    if r.status_code == 429:
                        consecutive_429 += 1
                        if attempt == 0:
                            time.sleep(6.0)
                            continue
                    break
                except Exception:
                    break
            if not body:
                time.sleep(1.5)
                continue
            try:
                root = ET.fromstring(body)
                for e in root.findall(f"{_ATOM}entry")[:per]:
                    title = (e.findtext(f"{_ATOM}title") or "").strip()
                    le = e.find(f"{_ATOM}link")
                    link = le.get("href") if le is not None else None
                    content = e.findtext(f"{_ATOM}content") or ""
                    if not link or not re.search(r"v\.redd\.it|redgifs|streamable|youtu", content):
                        continue
                    if _GAMEPLAY_RX.search(title):
                        continue
                    sc = 0.18 if sub.lower() in _themes._GENERIC_LC else 0.45
                    out.append(Candidate(url=link, title=title,
                                         source=f"reddit:{sub}", score=sc))
            except Exception:
                pass
            time.sleep(3.0)
    if out:
        _rss_cache[theme_key] = (time.time(), out)
    return out[:limit]


_YT_EXCLUDE = " -gameplay -walkthrough"


def _youtube_targets(theme_key: str, n: int) -> list[Candidate]:
    """YouTube search almost always returns long "X compilation" uploads for
    these topics — that's fine, we re-clip individual moments out of the first
    ~2 min (which is what real comp channels do). We still ask for #shorts too,
    in case any real standalone clips surface."""
    theme = _themes.get(theme_key)
    out: list[Candidate] = []
    for q in theme.youtube:
        base = re.sub(r"\b(compilation|montage)\b", "", q, flags=re.I).strip()
        # standalone-clip attempt
        out.append(Candidate(url=f"ytsearch{n}:{base} #shorts{_YT_EXCLUDE}",
                             title=q, source="youtube", score=0.32))
        # compilations to re-clip from — recent ones (fresher clips)
        out.append(Candidate(url=f"ytsearch{n}:{base} compilation {2026}{_YT_EXCLUDE}",
                             title=q, source="youtube", score=0.3))
        out.append(Candidate(url=f"ytsearch{max(4, n - 4)}:{base} caught on camera{_YT_EXCLUDE}",
                             title=q, source="youtube", score=0.28))
    return out


def discover(theme_key: str, limit: int, *, exclude_urls: set[str] | None = None) -> list[Candidate]:
    """Ranked list of clip URLs for a theme. YouTube is the reliable primary
    (the Reddit API is approval-gated in 2026; RSS is rate-limited); Reddit adds
    accuracy when it's reachable. ``exclude_urls`` drops already-tried URLs."""
    exclude_urls = exclude_urls or set()
    cands: list[Candidate] = []
    if reddit_api_available():
        cands = _reddit_api_candidates(theme_key, limit)
        if cands:
            log.info("compilation discover %s: %d via Reddit API", theme_key, len(cands))
    if len(cands) < limit:
        # keyless Reddit: .json first (richer), then .rss
        rj = _reddit_json_candidates(theme_key, limit)
        cands += rj
        if len(cands) < limit:
            rss = _reddit_rss_candidates(theme_key, limit)
            if rss:
                log.info("compilation discover %s: +%d via Reddit RSS", theme_key, len(rss))
            cands += rss
    if get_settings().compilation.allow_youtube:
        # always — YouTube is the dependable source
        cands += _youtube_targets(theme_key, 12 if cands else 18)
    seen: set[str] = set(exclude_urls)
    uniq = []
    for c in cands:
        if c.url in seen:
            continue
        seen.add(c.url)
        uniq.append(c)
    # highest-relevance first: topic subreddits (~0.45+) > youtube (~0.35) >
    # generic "caught on camera" subreddits (~0.18)
    uniq.sort(key=lambda c: c.score, reverse=True)
    return uniq


# --------------------------------------------------------------------------
# download
# --------------------------------------------------------------------------

def _ff() -> str:
    return shutil.which(get_settings().ffmpeg_bin) or "ffmpeg"


def _probe_duration(path: Path) -> float:
    probe = shutil.which("ffprobe") or "ffprobe"
    try:
        out = subprocess.run(
            [probe, "-v", "quiet", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=30,
        ).stdout.strip()
        return float(out) if out else 0.0
    except Exception:
        return 0.0


def _ydl_opts(raw_dir: Path, count: int) -> dict:
    import yt_dlp

    s = get_settings()
    cfg = s.compilation
    grab = int(cfg.download_seconds) + 5
    opts: dict = {
        "format": "bv*[height<=1280]+ba/b[height<=1280]/best",
        "outtmpl": str(raw_dir / "%(extractor)s-%(id)s.%(ext)s"),
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "ignoreerrors": True,
        "retries": 2,
        "socket_timeout": 20,
        "playlist_items": f"1:{count}",
        "download_ranges": yt_dlp.utils.download_range_func(None, [(0, grab)]),
        "force_keyframes_at_cuts": False,
        "match_filter": yt_dlp.utils.match_filter_func(
            f"duration >= {cfg.min_source_seconds} & "
            f"duration <= {cfg.max_source_seconds} & !is_live & "
            r"title !~= '(?i)(gameplay|pubg|fortnite|warzone|minecraft|roblox|"
            r"valorant|speedrun|kill ?cam|no scope|ranked lobby)'"
        ),
    }
    cookie_txt = (Path(s.footage.cookies_file) if s.footage.cookies_file
                  else s.root / "assets" / "cookies.txt")
    if cookie_txt.is_file():
        opts["cookiefile"] = str(cookie_txt.resolve())
    elif s.footage.cookies_from_browser:
        opts["cookiesfrombrowser"] = (s.footage.cookies_from_browser,)
    node = shutil.which("node") or shutil.which("node.exe")
    opts["js_runtimes"] = {"deno": {}}
    if node:
        opts["js_runtimes"]["node"] = {"path": node}
    return opts


def _has_audio(path: Path) -> bool:
    probe = shutil.which("ffprobe") or "ffprobe"
    try:
        out = subprocess.run(
            [probe, "-v", "quiet", "-select_streams", "a", "-show_entries",
             "stream=index", "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=30,
        ).stdout.strip()
        return bool(out)
    except Exception:
        return False


def _normalize(src: Path, dst: Path) -> Path | None:
    """Re-encode one source clip to a uniform vertical clip WITH audio (silent
    track synthesised if the source has none), so slices from different sources
    concat cleanly later."""
    s = get_settings()
    cfg = s.compilation
    w, h, fps = s.video.width, s.video.height, s.video.fps
    dur = _probe_duration(src)
    if dur < cfg.min_source_seconds:
        return None
    has_audio = _has_audio(src)
    if cfg.blur_pad:
        vf = (f"split=2[bg][fg];"
              f"[bg]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"
              f"boxblur=22:2[bg2];"
              f"[fg]scale={w}:-2:force_original_aspect_ratio=decrease[fg2];"
              f"[bg2][fg2]overlay=(W-w)/2:(H-h)/2,setsar=1,fps={fps},format=yuv420p")
    else:
        vf = (f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"
              f"setsar=1,fps={fps},format=yuv420p")
    keep = min(dur, float(cfg.download_seconds))
    cmd = [_ff(), "-y", "-t", f"{keep:.2f}", "-i", str(src)]
    if not has_audio:
        cmd += ["-f", "lavfi", "-t", f"{keep:.2f}",
                "-i", "anullsrc=channel_layout=stereo:sample_rate=44100"]
    cmd += [
        "-map", "0:v:0", "-map", ("1:a:0" if not has_audio else "0:a:0"),
        "-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "24",
        "-pix_fmt", "yuv420p",
    ]
    if has_audio:
        cmd += ["-af", "aresample=async=1,loudnorm=I=-16:TP=-1.5:LRA=11"]
    cmd += [
        "-c:a", "aac", "-b:a", "160k", "-ar", "44100", "-ac", "2",
        "-movflags", "+faststart", str(dst),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=600)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        log.warning("normalize failed for %s: %s", src.name, exc)
        dst.unlink(missing_ok=True)
        return None
    if not dst.is_file() or dst.stat().st_size < 40_000 \
            or _probe_duration(dst) < cfg.min_source_seconds:
        dst.unlink(missing_ok=True)
        return None
    return dst


def harvest(theme_key: str, need: int) -> list[Path]:
    """Download up to ``need`` NEW clips for a theme into its cache dir."""
    s = get_settings()
    cfg = s.compilation
    if not s.footage.allow_youtube and not reddit_api_available():
        log.warning("compilation: no clip source available (no reddit creds, "
                    "footage.allow_youtube false)")
        return []
    try:
        import yt_dlp
    except ImportError:
        raise RuntimeError("yt-dlp not installed (pip install 'yt-dlp[default]')")

    dst = theme_dir(theme_key)
    dst.mkdir(parents=True, exist_ok=True)
    raw = dst / "_raw"
    shutil.rmtree(raw, ignore_errors=True)     # clear leftovers from a killed run
    try:
        raw.mkdir(exist_ok=True)
    except OSError:
        for p in raw.glob("*"):                # rmtree lost a race with a lock
            try:
                p.unlink()
            except OSError:
                pass
    have_stems = {p.stem for p in cached_clips(theme_key)}

    cands = discover(theme_key, cfg.candidates_per_fetch)
    if not cands:
        shutil.rmtree(raw, ignore_errors=True)
        return []

    deadline = time.time() + cfg.sync_budget_seconds
    consecutive_fail = 0
    new_clips: list[Path] = []
    opts = _ydl_opts(raw, count=max(3, need))
    with yt_dlp.YoutubeDL(opts) as ydl:
        for cand in cands:
            if len(new_clips) >= need or time.time() > deadline or consecutive_fail >= 4:
                break
            before = {p.name for p in raw.iterdir()}
            try:
                ydl.extract_info(cand.url, download=True)
                consecutive_fail = 0
            except Exception as exc:  # noqa: BLE001
                consecutive_fail += 1
                log.warning("compilation fetch failed %s: %s", cand.url, exc)
                continue
            # process whatever new video files landed in _raw (id-agnostic)
            fresh = sorted(p for p in raw.iterdir()
                           if p.name not in before
                           and p.suffix.lower() in VIDEO_EXTS
                           and p.stat().st_size > 40_000)
            for src in fresh:
                if len(new_clips) >= need:
                    break
                stem = src.stem.lower().replace(" ", "-")
                if stem in have_stems:
                    src.unlink(missing_ok=True)
                    continue
                final = _normalize(src, dst / f"{stem}.mp4")
                src.unlink(missing_ok=True)
                if final:
                    new_clips.append(final)
                    have_stems.add(stem)
    shutil.rmtree(raw, ignore_errors=True)
    log.info("compilation harvest %s: +%d clip(s)", theme_key, len(new_clips))
    return new_clips


def ensure_pool(theme_key: str, *, minimum: int | None = None,
                hard: bool = False, deadline_seconds: float | None = None) -> list[Path]:
    """Guarantee at least ``minimum`` cached clips for a theme; download if short.

    ``hard`` keeps running sourcing passes (fresh ``discover`` each time — new
    RSS window, more YouTube results) until the pool reaches ``minimum``, a pass
    adds nothing, or ``deadline_seconds`` of wall-clock elapses (so a slow
    connection can't stall a render). The softer default is for background
    top-ups.
    """
    cfg = get_settings().compilation
    minimum = minimum or cfg.pool_per_theme
    have = cached_clips(theme_key)
    if len(have) >= minimum:
        return have

    passes = 6 if hard else 1
    stop = time.time() + (deadline_seconds if deadline_seconds is not None else 1e9)
    for _ in range(passes):
        if time.time() > stop:
            break
        need = minimum - len(cached_clips(theme_key))
        if need <= 0:
            break
        got = harvest(theme_key, min(cfg.fetch_per_build, need))
        if not got:
            break
    return cached_clips(theme_key)
