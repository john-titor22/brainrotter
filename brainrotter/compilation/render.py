"""Assemble a themed compilation: 2-5s slices of sourced clips, hard-cut, with
the clips' own audio and a quiet music bed. ffmpeg only — one filtergraph.
"""

from __future__ import annotations

import json
import logging
import random
import re
import shutil
import subprocess
import time
from pathlib import Path

from ..config import get_settings
from ..models import Script
from . import moments, review, sources
from . import themes as _themes
from .themes import THEMES

log = logging.getLogger("brainrotter.compilation")

# fewer than this distinct clips isn't a compilation — bail
MIN_DISTINCT_CLIPS = 3


def _ff() -> str:
    return shutil.which(get_settings().ffmpeg_bin) or "ffmpeg"


def _probe(path: Path) -> float:
    return sources._probe_duration(path)


# --- near-duplicate detection -----------------------------------------------

def _dhash(img) -> int:
    """64-bit difference hash of a PIL image."""
    g = img.convert("L").resize((9, 8))
    px = list(g.getdata())
    bits = 0
    for row in range(8):
        for col in range(8):
            i = row * 9 + col
            bits = (bits << 1) | (1 if px[i] > px[i + 1] else 0)
    return bits


def _slice_hashes(slice_path: Path, ff: str, work: Path) -> list[int]:
    """dhash of 3 frames spread across the slice."""
    try:
        from PIL import Image
    except Exception:
        return []
    dur = _probe(slice_path)
    out: list[int] = []
    for k, frac in enumerate((0.25, 0.55, 0.85)):
        f = work / f"h_{slice_path.stem}_{k}.jpg"
        try:
            subprocess.run(
                [ff, "-hide_banner", "-nostats", "-ss", f"{max(0.1, dur * frac):.2f}",
                 "-i", str(slice_path), "-frames:v", "1", "-vf", "scale=64:64", "-y", str(f)],
                capture_output=True, timeout=20,
            )
            if f.is_file():
                with Image.open(f) as im:
                    out.append(_dhash(im))
        except Exception:
            continue
    return out


def _too_similar(a: list[int], b: list[int], bits: int = 8) -> bool:
    for x in a:
        for y in b:
            if bin(x ^ y).count("1") <= bits:
                return True
    return False


def _recent_hashes_path(theme_key: str) -> Path:
    return sources.theme_dir(theme_key) / "_seen_hashes.json"


def _load_recent_hashes(theme_key: str) -> list[list[int]]:
    try:
        return json.loads(_recent_hashes_path(theme_key).read_text("utf-8"))[-60:]
    except Exception:
        return []


_MAX_SEEN = 200


def _save_recent_hashes(theme_key: str, new: list[list[int]]) -> None:
    cur = _load_recent_hashes(theme_key)
    cur.extend(new)
    try:
        _recent_hashes_path(theme_key).write_text(
            json.dumps(cur[-_MAX_SEEN:]), encoding="utf-8")
    except Exception:
        pass


def _backfill_seen_hashes(theme_key: str, ff: str, work: Path) -> None:
    """Seed the cross-video dedup store from every source clip used in a past
    build — so clips from videos you already published are recognised even if
    they predate the dedup feature. Runs once (a marker file)."""
    marker = sources.theme_dir(theme_key) / "_seen_backfilled"
    if marker.exists():
        return
    try:
        used = sources.load_used(theme_key)               # {stem: count}
        by_stem = {p.stem: p for p in sources.cached_clips(theme_key)}
        hashes: list[list[int]] = []
        for stem in list(used)[: _MAX_SEEN]:
            p = by_stem.get(stem)
            if not p:
                continue
            h = _slice_hashes(p, ff, work)
            if h:
                hashes.append(h[0:1] or h)
        if hashes:
            _save_recent_hashes(theme_key, hashes)
            log.info("compilation: backfilled %d seen-hashes for '%s'", len(hashes), theme_key)
        marker.write_text("ok", encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        log.warning("compilation backfill failed for %s: %s", theme_key, exc)


def _dedupe_slices(cut: list[tuple[Path, Path]], ff: str, work: Path,
                   theme_key: str, *, already: list[list[int]] | None = None
                   ) -> tuple[list[tuple[Path, Path]], list[list[int]]]:
    """Drop slices that repeat a moment already in this cut OR seen in a recent
    video. Returns (kept, hashes-of-kept)."""
    seen = _load_recent_hashes(theme_key) if theme_key else []
    kept: list[tuple[Path, Path]] = []
    kept_hashes: list[list[int]] = list(already or [])
    for sl, src in cut:
        h = _slice_hashes(sl, ff, work)
        if not h:
            kept.append((sl, src))
            continue
        if any(_too_similar(h, k) for k in kept_hashes):
            log.info("compilation: dropped a near-duplicate slice (%s)", src.stem[:16])
            continue
        if any(_too_similar(h, k, bits=6) for k in seen):
            log.info("compilation: dropped a slice seen in a recent video (%s)", src.stem[:16])
            continue
        kept.append((sl, src))
        kept_hashes.append(h)
    return kept, kept_hashes


def _window(theme_key: str, clip: Path, nth_use: int, rng: random.Random,
            cfg) -> tuple[float, float]:
    """Where to cut ``clip`` — dead space trimmed off, the clip otherwise left
    whole. A 2nd window (the later part) only if the clip has one."""
    wins = moments.windows_for(theme_key, clip)
    if wins:
        s, d = wins[nth_use % len(wins)]
        return float(s), float(d)
    dur = _probe(clip)
    d = min(cfg.clip_max_seconds, max(cfg.clip_min_seconds, dur - 0.2))
    return 0.0, round(d, 2)


def _select(theme_key: str, rng: random.Random, *,
            avoid: set[str] | None = None) -> list[tuple[Path, float, float]]:
    """Pick clips to fill roughly ``target_seconds``. A clip that appeared in the
    last few videos is skipped entirely; if that leaves too few, recency is
    relaxed one video at a time, and only as a last resort is a clip reused.
    A shallow pool makes a SHORTER video — never the same clips again."""
    cfg = get_settings().compilation
    all_clips = sources.cached_clips(theme_key)
    if not all_clips:
        return []
    used = sources.load_used(theme_key)
    avoid = set(avoid or ())

    need = max(MIN_DISTINCT_CLIPS,
               int(cfg.target_seconds / cfg.clip_max_seconds) + 3)
    pool = [p for p in all_clips if p.stem not in avoid]

    # 1st choice: clips NEVER used in any finished video. Only if there aren't
    # enough of those do we fall back to used ones (least-used, oldest-used).
    never = [p for p in pool if used.get(p.stem, 0) == 0]
    if len(never) >= need:
        pool = never
    else:
        for within in range(cfg.recency_exclude, 0, -1):
            recent = sources.recently_used(theme_key, within)
            cand = [p for p in pool if p.stem not in recent]
            if len(cand) >= MIN_DISTINCT_CLIPS:
                pool = cand
                break

    rng.shuffle(pool)
    pool.sort(key=lambda p: used.get(p.stem, 0))       # least-used first
    cap = max(MIN_DISTINCT_CLIPS + 2,
              int(cfg.target_seconds / cfg.clip_min_seconds) + 3)
    pool = pool[:cap]

    # each source yields 1 window (a single clip) up to N (a compilation we
    # re-clip). Round-robin so every source is drawn from once before any twice.
    picks: list[tuple[Path, float, float]] = []
    per_clip: dict[str, int] = {}
    win_cache: dict[str, list] = {}

    def _wins(clip):
        if clip.stem not in win_cache:
            win_cache[clip.stem] = moments.windows_for(theme_key, clip)
        return win_cache[clip.stem]

    total = 0.0
    for round_no in range(4):
        if total >= cfg.target_seconds:
            break
        progressed = False
        for clip in pool:
            if total >= cfg.target_seconds:
                break
            if per_clip.get(clip.stem, 0) != round_no:
                continue
            wins = _wins(clip)
            if round_no >= len(wins):
                continue
            start, d = wins[round_no][0], wins[round_no][1]
            if d < cfg.clip_min_seconds - 0.4:
                continue
            picks.append((clip, float(start), round(float(d), 2)))
            per_clip[clip.stem] = round_no + 1
            total += d
            progressed = True
        if not progressed:
            break
    rng.shuffle(picks)                                 # don't clump one source's slices
    return picks


def _title(theme_key: str, n: int) -> str:
    from ..writer import llm

    theme = _themes.get(theme_key)
    if llm.available():
        try:
            out = llm.complete_text(
                "You write short punchy titles for vertical video compilations. "
                "Return ONLY the title, no quotes, under 60 characters.",
                f"A compilation of {n} short {theme.label}. Give it a title that "
                "would get clicks on TikTok/Shorts.",
                fast=True, max_tokens=24,
            ).strip().strip('"\'`').splitlines()[0].strip()
            if 6 <= len(out) <= 70:
                return out
        except Exception:
            pass
    return theme.label.title()


# --- assembly ----------------------------------------------------------

def _cut_slices(picks, work: Path, w: int, h: int, fps: int, ff: str,
                *, tag: str = "s") -> list[tuple[Path, Path]]:
    """One normalised file per pick. Video: uniform 9:16/fps. Audio: two-pass
    ``loudnorm`` to a fixed target + a limiter, so every slice — quiet talking
    clip or loud crash clip — lands at the same perceived level in the cut.

    Returns ``[(slice_file, source_clip), ...]`` for the picks that cut cleanly."""
    vf = (f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"
          f"setsar=1,fps={fps},format=yuv420p")
    out: list[tuple[Path, Path]] = []
    for i, (clip, start, dur) in enumerate(picks):
        dst = work / f"{tag}{i:02d}.mp4"
        af = _loudnorm_af(clip, start, dur, ff)
        cmd = [ff, "-y", "-ss", f"{start:.2f}", "-t", f"{dur:.2f}", "-i", str(clip),
               "-vf", vf, "-af", af,
               "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
               "-pix_fmt", "yuv420p", "-r", str(fps),
               "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2",
               "-movflags", "+faststart", str(dst)]
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=180)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            log.warning("slice %d cut failed (%s): %s", i, clip.name,
                        getattr(exc, "stderr", "")[-200:] if hasattr(exc, "stderr") else exc)
            continue
        if dst.is_file() and dst.stat().st_size > 20_000:
            out.append((dst, clip))
    return out


_TARGET_I, _TARGET_TP, _TARGET_LRA = -14.0, -1.5, 9.0


def _loudnorm_af(clip: Path, start: float, dur: float, ff: str) -> str:
    """Two-pass loudnorm filter string for one slice: measure this exact window,
    then normalise it with those measurements (accurate on short clips where
    single-pass drifts). Falls back to single-pass + limiter on any hiccup."""
    base = (f"loudnorm=I={_TARGET_I}:TP={_TARGET_TP}:LRA={_TARGET_LRA}")
    try:
        p = subprocess.run(
            [ff, "-hide_banner", "-nostats", "-ss", f"{start:.2f}", "-t", f"{dur:.2f}",
             "-i", str(clip), "-af", base + ":print_format=json", "-f", "null", "-"],
            capture_output=True, text=True, timeout=90,
        ).stderr
        m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", p, re.S)
        d = json.loads(m.group(0)) if m else {}
        need = ("input_i", "input_tp", "input_lra", "input_thresh")
        if all(k in d for k in need) and d["input_i"] not in ("-inf", "inf"):
            return (f"{base}:measured_I={d['input_i']}:measured_TP={d['input_tp']}:"
                    f"measured_LRA={d['input_lra']}:measured_thresh={d['input_thresh']}:"
                    f"linear=true:print_format=summary,alimiter=limit=0.97")
    except Exception:
        pass
    return f"{base},dynaudnorm=f=250:g=9,alimiter=limit=0.97"


def _concat(slices: list[Path], dst: Path, ff: str) -> Path:
    listf = dst.parent / "concat.txt"
    listf.write_text("".join(f"file '{p.resolve().as_posix()}'\n" for p in slices),
                     encoding="utf-8")
    cmd = [ff, "-y", "-f", "concat", "-safe", "0", "-i", str(listf),
           "-c", "copy", "-movflags", "+faststart", str(dst)]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=300)
    except subprocess.CalledProcessError:
        # a codec/timebase mismatch — re-encode the concat instead of stream-copy
        cmd = [ff, "-y", "-f", "concat", "-safe", "0", "-i", str(listf),
               "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
               "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2",
               "-movflags", "+faststart", str(dst)]
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=600)
    return dst


def _mux(body: Path, music: str | None, out: Path, work: Path, ff: str, *,
         vol: float) -> Path:
    dur = _probe(body)
    if not music:
        cmd = [ff, "-y", "-i", str(body), "-c", "copy", "-movflags", "+faststart", str(out)]
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=300)
        return out
    fin = 1.2
    fade = max(fin, dur - 1.5)
    # low bed that DUCKS hard under the clip audio (sidechain), then a final
    # loudnorm on the whole mix so the programme level is consistent too.
    fc = (
        f"[0:a]asplit=2[main][key];"
        f"[1:a]volume={vol:.3f},afade=t=in:st=0:d={fin},afade=t=out:st={fade:.2f}:d=1.5[b0];"
        f"[b0][key]sidechaincompress=threshold=0.015:ratio=14:attack=12:release=380[bed];"
        f"[main][bed]amix=inputs=2:duration=first:normalize=0,"
        f"loudnorm=I=-14:TP=-1.0:LRA=11[a]"
    )
    cmd = [ff, "-y", "-i", str(body), "-stream_loop", "-1", "-i", str(music),
           "-filter_complex", fc, "-map", "0:v", "-map", "[a]",
           "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2",
           "-t", f"{dur:.2f}", "-movflags", "+faststart", str(out)]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=600)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"compilation mux failed: {(exc.stderr or '')[-500:]}") from exc
    return out


def build(theme_key: str, *, brief, ctx) -> dict:
    t0 = time.time()
    if not theme_key:
        theme_key = "funny"
    is_adhoc = theme_key.startswith("@")
    cfg = ctx.settings.compilation
    rng = random.Random(ctx.seed)

    # 1. deepen the pool (network — outside the render lock). Keep a fresh buffer
    #    ABOVE however many clips past videos have already burned through, so a
    #    long run of videos never repeats. Capped by a wall-clock budget.
    per_video = int(cfg.target_seconds / cfg.clip_min_seconds) + 2
    burned = len(sources.load_used(theme_key))
    want_pool = min(cfg.pool_per_theme, burned + per_video * 3)
    sources.ensure_pool(theme_key, minimum=want_pool, hard=True,
                        deadline_seconds=cfg.sync_budget_seconds)
    ctx.workdir.mkdir(parents=True, exist_ok=True)
    _backfill_seen_hashes(theme_key, _ff(), ctx.workdir)
    picks = _select(theme_key, rng)
    n_sources = len({c.stem for c, _, _ in picks})     # distinct source uploads

    # need enough total slices AND at least a couple of distinct sources
    floor_sources = 2
    floor_slices = 3
    if (n_sources < floor_sources or len(picks) < floor_slices) and not is_adhoc:
        alt = max((k for k in THEMES if k != theme_key),
                  key=lambda k: len(sources.cached_clips(k)), default=None)
        if alt and len(sources.cached_clips(alt)) >= 3:
            log.warning("compilation: '%s' too thin (%d sources, %d slices) — "
                        "using '%s'", theme_key, n_sources, len(picks), alt)
            theme_key = alt
            picks = _select(theme_key, rng)
            n_sources = len({c.stem for c, _, _ in picks})
    if n_sources < floor_sources or len(picks) < floor_slices:
        raise RuntimeError(
            f"compilation: topic '{_themes.get(theme_key).label}' only turned up "
            f"{n_sources} source(s) / {len(picks)} clip(s) — try a broader or "
            f"more common topic." if is_adhoc else
            f"compilation: only {n_sources} source(s) / {len(picks)} clip(s) for "
            f"'{theme_key}'. Run `brainrotter compilation sync {theme_key} -n 15`.")

    work = ctx.workdir
    work.mkdir(parents=True, exist_ok=True)
    out = ctx.settings.out_path / f"{ctx.job_id}.mp4"
    music = (brief.style or {}).get("music_file")
    if music and not Path(music).is_file():
        music = None

    s = ctx.settings
    w, h, fps = s.video.width, s.video.height, s.video.fps
    ff = _ff()

    with ctx.render_lock():
        # 2. cut each slice to its own file, LOUDNESS-NORMALISED per slice so a
        #    quiet clip and a loud clip sit at the same level in the cut.
        cut = _cut_slices(picks, work, w, h, fps, ff)            # [(slice, src_clip)]

        # 3. QA the cut: drop black/frozen slices and (if a vision model is
        #    pulled) anything off-theme — game footage, screenshots, unrelated.
        #    Then re-pick + re-cut replacements from the pool, once.
        bad = review.review([s for s, _ in cut], theme_key, work)
        if bad:
            keep = [cut[i] for i in range(len(cut)) if i not in bad]
            have_stems = {c.stem for _, c in keep}
            short = max(floor_slices, len(cut)) - len(keep)
            if short > 0:
                avoid = {c.stem for _, c in cut} | sources.recently_used(
                    theme_key, cfg.recency_exclude)
                repl = [p for p in _select(theme_key, rng, avoid=avoid)
                        if p[0].stem not in have_stems][: short + 2]
                if repl:
                    more = _cut_slices(repl, work, w, h, fps, ff, tag="r")
                    more_bad = review.structural_flags([s for s, _ in more])
                    keep += [more[i] for i in range(len(more)) if i not in more_bad]
            cut = keep

        # 4. drop near-duplicate slices — different source uploads (or two
        #    windows of one compilation) often carry the SAME viral moment, and
        #    the same moment recurs across YouTube "X compilation" uploads.
        cut, hashes = _dedupe_slices(cut, ff, work, theme_key)
        if len(cut) < max(floor_slices, MIN_DISTINCT_CLIPS):
            avoid = ({c.stem for _, c in cut}
                     | sources.recently_used(theme_key, cfg.recency_exclude))
            more = [m for m in _select(theme_key, rng, avoid=avoid)
                    if m[0].stem not in {c.stem for _, c in cut}][:6]
            if more:
                extra = _cut_slices(more, work, w, h, fps, ff, tag="d")
                new, hashes = _dedupe_slices(extra, ff, work, theme_key, already=hashes)
                cut += new
        if theme_key and hashes:
            _save_recent_hashes(theme_key, hashes[-8:])

        slices = [s for s, _ in cut]
        used_stems = list({c.stem for _, c in cut})
        if len(slices) < MIN_DISTINCT_CLIPS:
            raise RuntimeError(f"compilation: only {len(slices)} slices survived "
                               f"cutting + review for '{theme_key}'")
        random.Random(ctx.seed + 1).shuffle(slices)
        body = _concat(slices, work / "body.mp4", ff)
        _mux(body, music, out, work, ff,
             vol=float(brief.style.get("music_volume") or cfg.music_volume))
    if not out.is_file() or out.stat().st_size < 40_000:
        raise RuntimeError("compilation produced no output")
    n = len(slices)

    sources.mark_used(theme_key, used_stems)
    moments.prune(theme_key, {p.stem for p in sources.cached_clips(theme_key)})
    dur_final = _probe(out)
    title = _title(theme_key, len(used_stems))
    log.info("compilation %s: %d clips, %.0fs, %.0fs wall",
             theme_key, n, dur_final, time.time() - t0)

    sources.note_theme(theme_key)
    # background top-up so the next build has fresh material
    _autogrow(theme_key)

    script = Script(title=title, beats=[],
                    hashtags=[theme_key, "compilation", "fyp", "shorts"])
    return {"path": str(out), "duration": round(dur_final, 2),
            "seconds": round(time.time() - t0, 1), "script": script.model_dump(),
            "clips": n, "theme": theme_key}


_growing: set[str] = set()


def _autogrow(theme_key: str) -> None:
    """After a build, keep topping the pool toward ``pool_per_theme`` in a daemon
    thread so later videos keep finding fresh clips. Best-effort, never raises,
    one theme at a time."""
    import threading

    cfg = get_settings().compilation
    if theme_key in _growing:
        return
    if len(sources.cached_clips(theme_key)) >= cfg.pool_per_theme:
        return

    def _work():
        _growing.add(theme_key)
        try:
            for _ in range(3):
                have = len(sources.cached_clips(theme_key))
                if have >= cfg.pool_per_theme:
                    break
                got = sources.harvest(
                    theme_key, min(cfg.fetch_per_build, cfg.pool_per_theme - have))
                if not got:
                    break
        except Exception as exc:  # noqa: BLE001
            log.warning("compilation autogrow %s failed: %s", theme_key, exc)
        finally:
            _growing.discard(theme_key)

    threading.Thread(target=_work, daemon=True).start()
