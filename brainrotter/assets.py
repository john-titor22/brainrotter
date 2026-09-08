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


def _is_clip(p: Path) -> bool:
    # skip download scratch dirs and partials
    return (
        p.suffix.lower() in VIDEO_EXTS
        and p.is_file()
        and "_raw" not in p.parts
        and not p.name.endswith(".part")
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


def has_any() -> bool:
    return bool(index())


def _real_categories(idx: dict[str, list[Path]]) -> dict[str, list[Path]]:
    real = {c: v for c, v in idx.items() if c not in PLACEHOLDER_CATEGORIES}
    return real or idx  # fall back to placeholders only if that's all there is


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


def pick(category: str | None = None, *, count: int = 1,
         seed: int | None = None, allow_fetch: bool = True) -> list[str]:
    """Up to ``count`` clip paths for a category.

    Never returns placeholder clips when real footage exists, and never mixes
    the two. Auto-fetch only runs on a *cold* library (no real footage at all) —
    otherwise renders would block on slow downloads. Grow the pool with
    ``brainrotter footage sync``.
    """
    idx = index()
    real = _real_categories(idx)

    if not real and allow_fetch:
        _try_fetch(category, count)
        real = _real_categories(index())
    if not real:
        return []

    rng = random.Random(seed)
    if category and category in real:
        pool = list(real[category])
    else:
        pool = [p for clips in real.values() for p in clips]
    rng.shuffle(pool)
    return [str(p) for p in pool[: max(1, count)]]


def pick_mixed(categories: list[str], *, count: int = 3, seed: int | None = None) -> list[str]:
    """Clips spanning several categories so one video cuts between games.

    Only categories that actually resolve to real footage are used; placeholder
    footage is never blended in.
    """
    lanes: list[list[str]] = []
    for i, cat in enumerate(categories or []):
        got = pick(cat, count=count, seed=(seed or 0) + i, allow_fetch=True)
        if got:
            lanes.append(got)

    if not lanes:
        fallback = pick(None, count=count, seed=seed, allow_fetch=True)
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
