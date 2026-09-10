"""Background footage library.

Two sources, merged by category:
  - ``assets/backgrounds/<category>/``      — clips you add by hand
  - ``assets/cache/footage/<category>/``    — clips Brainrotter downloaded itself

The Director asks for a category; if it's empty and ``footage.auto_sync`` is on,
we fetch some with yt-dlp before returning.
"""

from __future__ import annotations

import random
from pathlib import Path

from .config import get_settings

VIDEO_EXTS = {".mp4", ".mkv", ".webm", ".mov", ".m4v"}
# Synthetic / placeholder buckets — only used when there is nothing real, and
# never mixed into a video alongside real footage (different encoding breaks the
# engine's concat).
PLACEHOLDER_CATEGORIES = {"testpattern"}

# "@name" categories are footage OF a specific public figure (speeches,
# interviews) fetched for the anime_figure format. "~series..." categories are
# Creative-Commons b-roll pulled for one part of a "related story" series. Both
# are special-purpose: never blended into a normal video, only returned when
# asked for by that exact category name.
FIGURE_PREFIX = "@"
SERIES_PREFIX = "~"


def _is_special(category: str) -> bool:
    return category.startswith(FIGURE_PREFIX) or category.startswith(SERIES_PREFIX)


# The vendored engine rejects any material whose short side is under ~470 px
# ("no valid local video materials"). A 270-wide vertical clip that slipped into
# a gameplay category is useless as a full-screen background anyway.
_MIN_CLIP_DIMENSION = 472
_dim_cache: dict[tuple[str, float, int], bool] = {}


def _big_enough(p: Path) -> bool:
    import shutil
    import subprocess

    try:
        key = (str(p), p.stat().st_mtime, p.stat().st_size)
    except OSError:
        return False
    if key in _dim_cache:
        return _dim_cache[key]
    ok = True
    probe = shutil.which("ffprobe") or "ffprobe"
    try:
        out = subprocess.run(
            [probe, "-v", "quiet", "-select_streams", "v:0", "-show_entries",
             "stream=width,height", "-of", "csv=p=0:s=x", str(p)],
            capture_output=True, text=True, timeout=15,
        ).stdout.strip()
        w, h = (int(x) for x in out.split("x")[:2])
        ok = min(w, h) >= _MIN_CLIP_DIMENSION
    except Exception:
        ok = True                    # can't probe — give it the benefit of the doubt
    _dim_cache[key] = ok
    return ok


def _is_clip(p: Path) -> bool:
    # skip download scratch dirs, partials, and too-small-for-the-engine clips
    return (
        p.suffix.lower() in VIDEO_EXTS
        and p.is_file()
        and "_raw" not in p.parts
        and not p.name.endswith(".part")
        and _big_enough(p)
    )


def _scan(root: Path) -> dict[str, list[Path]]:
    out: dict[str, list[Path]] = {}
    if not root.exists():
        return out
    for sub in root.iterdir():
        if sub.is_dir() and not sub.name.startswith("_"):
            clips = [p for p in sub.rglob("*") if _is_clip(p)]
            if clips:
                out.setdefault(sub.name, []).extend(sorted(clips))
    loose = [p for p in root.iterdir() if _is_clip(p)]
    if loose:
        out.setdefault("uncategorized", []).extend(sorted(loose))
    return out


def index() -> dict[str, list[Path]]:
    s = get_settings()
    merged: dict[str, list[Path]] = {}
    for src in (_scan(s.backgrounds_path), _scan(s.footage_path)):
        for cat, clips in src.items():
            merged.setdefault(cat, []).extend(clips)
    return merged


def categories() -> list[str]:
    return sorted(index())


def available_categories() -> list[str]:
    """Real gameplay-background categories that actually have clips on disk."""
    return sorted(_gameplay_categories(index()))


def has_any() -> bool:
    return bool(_gameplay_categories(index()))


def _real_categories(idx: dict[str, list[Path]]) -> dict[str, list[Path]]:
    real = {c: v for c, v in idx.items() if c not in PLACEHOLDER_CATEGORIES}
    return real or idx  # fall back to placeholders only if that's all there is


def _gameplay_categories(idx: dict[str, list[Path]]) -> dict[str, list[Path]]:
    """Real background gameplay only — no placeholders, no @figure / ~series footage."""
    return {
        c: v for c, v in idx.items()
        if c not in PLACEHOLDER_CATEGORIES and not _is_special(c)
    }


def _try_fetch(category: str | None, count: int) -> None:
    s = get_settings()
    if not s.footage.auto_sync:
        return
    try:
        from . import footage

        footage.ensure(category or footage.registry.DEFAULT_CATEGORY,
                       count=max(count, s.footage.per_category))
    except Exception:
        pass


def _order_by_freshness(pool: list[Path], exclude: tuple[str, ...], rng: random.Random) -> list[Path]:
    """Shuffle, then float clips whose basename isn't in ``exclude`` (the last
    few videos' backgrounds) to the front — so the feed stops repeating even
    when only a couple of categories are cached."""
    recent = set(exclude)
    shuffled = list(pool)
    rng.shuffle(shuffled)
    shuffled.sort(key=lambda p: p.name in recent)   # False (fresh) sorts first
    return shuffled


def pick(category: str | None = None, *, count: int = 1,
         seed: int | None = None, allow_fetch: bool = True,
         exclude: tuple[str, ...] = ()) -> list[str]:
    """Up to ``count`` clip paths for a category.

    Never returns placeholder clips when real footage exists, and never mixes
    the two. Auto-fetch only runs on a *cold* library (no real footage at all) —
    otherwise renders would block on slow downloads. Grow the pool with
    ``brainrotter footage sync``. ``exclude`` = basenames of recently-used clips
    to push to the back.
    """
    idx = index()

    # An explicit "@figure" / "~series" request is served straight from that
    # category — the only path that ever touches special-purpose footage.
    if category and _is_special(category):
        pool = list(_real_categories(idx).get(category, []))
        pool = _order_by_freshness(pool, exclude, random.Random(seed))
        return [str(p) for p in pool[: max(1, count)]]

    real = _gameplay_categories(idx)
    if not real and allow_fetch:
        _try_fetch(category, count)
        real = _gameplay_categories(index())
    if not real:
        return []

    rng = random.Random(seed)
    if category and category in real:
        pool = list(real[category])
    else:
        pool = [p for clips in real.values() for p in clips]
    pool = _order_by_freshness(pool, exclude, rng)
    return [str(p) for p in pool[: max(1, count)]]


def pick_mixed(categories: list[str], *, count: int = 3, seed: int | None = None,
               exclude: tuple[str, ...] = ()) -> list[str]:
    """Clips spanning several categories so one video cuts between games.

    Only categories that actually resolve to real footage are used; placeholder
    footage is never blended in. ``exclude`` = recently-used clip basenames.
    """
    lanes: list[list[str]] = []
    for i, cat in enumerate(categories or []):
        got = pick(cat, count=count, seed=(seed or 0) + i, allow_fetch=True,
                   exclude=exclude)
        if got:
            lanes.append(got)

    if not lanes:
        fallback = pick(None, count=count, seed=seed, allow_fetch=True,
                        exclude=exclude)
        return fallback

    out: list[str] = []
    idx = 0
    while len(out) < count and idx < count * len(lanes) + len(lanes):
        lane = lanes[idx % len(lanes)]
        pos = idx // len(lanes)
        if pos < len(lane) and lane[pos] not in out:
            out.append(lane[pos])
        idx += 1
    return out or lanes[0][:count]
