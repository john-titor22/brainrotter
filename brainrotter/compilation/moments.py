"""Decide which part(s) of a downloaded source to use.

Two cases, decided from the (already length-capped) source:

  SINGLE CLIP  (<= ``single_clip_max`` s)
    leave it as-is — only trim leading black / an obvious silent-static intro
    (gently) and trailing silence / a frozen tail (harder). If what's left is
    over ``clip_max_seconds`` keep the start.

  COMPILATION  (longer — almost every YouTube "X compilation" result)
    it's already a bunch of clips edited together. Split it back apart: find the
    shot changes, and return several 3-8 s windows that each start just after a
    cut, spread across the video, skipping the first ~10 s (intro / channel
    bumper) and the last ~5 s (outro / subscribe screen), preferring segments
    that actually have audio.

Cached per clip in ``<theme>/_moments.json``.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
from pathlib import Path

from ..config import get_settings

log = logging.getLogger("brainrotter.compilation")


def _ff() -> str:
    return shutil.which(get_settings().ffmpeg_bin) or "ffmpeg"


def _probe(path: Path) -> float:
    from .sources import _probe_duration

    return _probe_duration(path)


# Analyse only the first N seconds of a source — we only ever cut a handful of
# short windows and never touch the tail. Full-decode of a long clip for scene
# detection is what made builds stall for 30+ min.
_MAX_ANALYZE_SECONDS = 70.0


def _analyze_pass(clip: Path, threshold: float = 0.30) -> tuple[list, list, list, list[float]]:
    """ONE ffmpeg decode of the first ``_MAX_ANALYZE_SECONDS`` — returns
    (black, silence, freeze, scene_cuts)."""
    try:
        r = subprocess.run(
            [_ff(), "-hide_banner", "-t", f"{_MAX_ANALYZE_SECONDS:.0f}", "-i", str(clip),
             "-af", "silencedetect=n=-40dB:d=0.35",
             "-vf", f"select='gt(scene,{threshold})',blackdetect=d=0.15:pic_th=0.92,"
                    f"freezedetect=n=-58dB:d=0.5,showinfo",
             "-map", "0:v:0", "-map", "0:a:0?", "-f", "null", "-"],
            capture_output=True, text=True, timeout=150,
        ).stderr
    except Exception:
        return [], [], [], []
    black = [(float(a), float(b))
             for a, b in re.findall(r"black_start:([\d.]+).*?black_end:([\d.]+)", r)]
    sil = []
    for m in re.finditer(r"silence_start:\s*(-?[\d.]+)(?:.*?silence_end:\s*([\d.]+))?", r, re.S):
        a = float(m.group(1))
        b = float(m.group(2)) if m.group(2) else 1e9
        sil.append((max(0.0, a), b))
    freeze = [(float(a), float(b))
              for a, b in re.findall(r"freeze_start:\s*([\d.]+).*?freeze_end:\s*([\d.]+)", r, re.S)]
    # showinfo prints n:.. pts_time:.. for the frames select passed (the cuts)
    cuts = sorted({round(float(m), 2)
                   for m in re.findall(r"pts_time:([0-9.]+)", r)})
    return black, sil, freeze, cuts


def _detect(clip: Path) -> tuple[list, list, list]:
    b, s, f, _ = _analyze_pass(clip)
    return b, s, f


def _scene_cuts(clip: Path, threshold: float = 0.30) -> list[float]:
    return _analyze_pass(clip, threshold)[3]


def _overlaps(spans, t0: float, t1: float) -> float:
    return sum(max(0.0, min(b, t1) - max(a, t0)) for a, b in spans)


# --- single clip: trim dead space --------------------------------------

def _trim_single(clip: Path, dur: float) -> list[list[float]]:
    cfg = get_settings().compilation
    lo_s, hi_s = float(cfg.clip_min_seconds), float(cfg.clip_max_seconds)
    black, sil, freeze, _cuts = _analyze_pass(clip)

    lead_cap = min(2.0, dur * 0.20)
    start = 0.0
    for a, b in black:
        if a <= 0.3:
            start = max(start, min(b, lead_cap))
    for a, b in sil:
        if a <= 0.1 and (b - a) <= 2.0:
            start = max(start, min(b, lead_cap))
    start = min(start, lead_cap)

    tail_cap = max(0.0, min(dur * 0.45, dur - start - lo_s))

    def _trailing(spans):
        e = dur
        for a, b in sorted(spans):
            if a <= start + 0.2:
                continue
            if b >= dur - 0.4:
                e = min(e, a)
        return e

    end = min(_trailing(sil), _trailing(freeze), _trailing(black))
    end = max(end, dur - tail_cap)
    end = min(end, dur - 0.03)
    if end - start < lo_s:
        start, end = 0.0, min(dur - 0.03, max(lo_s, dur))
    if end - start > hi_s:
        end = start + hi_s

    windows = [[round(start, 2), round(end - start, 2)]]
    later = windows[0][0] + windows[0][1] + 0.2
    if dur - later >= lo_s + 0.5:
        seg_end = min(dur - 0.03, later + hi_s)
        dead = _overlaps(sil, later, seg_end) + _overlaps(freeze, later, seg_end)
        if seg_end - later - dead >= lo_s:
            windows.append([round(later, 2), round(seg_end - later, 2)])
    return windows


# --- compilation: split into its moments -------------------------------

def _split_compilation(clip: Path, dur: float) -> list[list[float]]:
    cfg = get_settings().compilation
    lo_s, hi_s = float(cfg.clip_min_seconds), float(cfg.clip_max_seconds)
    seg = max(lo_s + 0.5, min(hi_s, 7.0))       # ~7s target per extracted moment

    # only look at the analysed window (first ~75s), never the whole 150s clip
    span = min(dur, _MAX_ANALYZE_SECONDS)
    head, tail = 9.0, 4.0
    lo, hi = head, max(head + seg, span - tail)
    _b, sil, _f, all_cuts = _analyze_pass(clip)
    cuts = [c for c in all_cuts if lo <= c <= hi - seg + 1.0]

    # at most 3 windows per source, well spread — grabbing more just re-mines
    # one upload and risks two windows landing on the same sub-clip
    starts: list[float] = []
    min_gap = max(seg * 2.0, (hi - lo) / 4)
    if len(cuts) >= 3:
        want_n = max(2, min(3, int((hi - lo) / min_gap)))
        step = (hi - lo) / want_n
        for i in range(want_n):
            target = lo + step * (i + 0.4)
            near = [c for c in cuts if target - step * 0.5 <= c <= target + step * 0.5]
            if not near:
                near = [min(cuts, key=lambda c: abs(c - target))]
            best = min(near, key=lambda c: _overlaps(sil, c, c + seg))
            if all(abs(best - s) > min_gap for s in starts):
                starts.append(round(best, 2))
    if len(starts) < 2:                          # thin scene detection — even spread
        n = max(2, min(3, int((hi - lo) / min_gap)))
        starts = [round(lo + (hi - lo) * (i + 0.5) / n, 2) for i in range(n)]

    windows = []
    for st in sorted(starts):
        d = min(seg, dur - st - 0.1)
        if d >= lo_s - 0.3:
            windows.append([round(st, 2), round(d, 2)])
    return windows or [[round(min(head, dur * 0.2), 2),
                        round(min(seg, dur - 1), 2)]]


# --- entry point ------------------------------------------------------

def _analyze(clip: Path) -> list[list[float]]:
    cfg = get_settings().compilation
    dur = _probe(clip)
    if dur <= cfg.clip_min_seconds + 0.3:
        return [[0.0, round(max(0.8, dur - 0.03), 2)]]
    if dur <= cfg.single_clip_max:
        return _trim_single(clip, dur)
    return _split_compilation(clip, dur)


def is_compilation(theme_key: str, clip: Path) -> bool:
    return _probe(clip) > get_settings().compilation.single_clip_max


# --- cache -----------------------------------------------------------

def _cache_path(theme_key: str) -> Path:
    return get_settings().compilation_cache_path / theme_key / "_moments.json"


def _load(theme_key: str) -> dict:
    p = _cache_path(theme_key)
    try:
        return json.loads(p.read_text("utf-8")) if p.is_file() else {}
    except Exception:
        return {}


def _save(theme_key: str, data: dict) -> None:
    try:
        _cache_path(theme_key).write_text(json.dumps(data), encoding="utf-8")
    except Exception:
        pass


def windows_for(theme_key: str, clip: Path) -> list[list[float]]:
    data = _load(theme_key)
    key = clip.stem
    if key not in data:
        try:
            data[key] = _analyze(clip)
        except Exception as exc:  # noqa: BLE001
            log.warning("cut analysis failed for %s: %s", clip.name, exc)
            data[key] = []
        _save(theme_key, data)
    return data.get(key) or []


def prune(theme_key: str, live_stems: set[str]) -> None:
    data = _load(theme_key)
    trimmed = {k: v for k, v in data.items() if k in live_stems}
    if len(trimmed) != len(data):
        _save(theme_key, trimmed)
