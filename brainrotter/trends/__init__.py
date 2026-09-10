"""Trend signal aggregation."""

from __future__ import annotations

import random

from ..models import TrendSignal
from . import reddit, topic_bank, web


def gather(*, limit: int = 24, seed: int | None = None) -> list[TrendSignal]:
    """Live sources first (real stories + what's trending on the open web), then
    the curated topic bank as filler."""
    out: list[TrendSignal] = []
    try:
        out.extend(reddit.signals())
    except Exception:
        pass
    try:
        out.extend(web.signals())
    except Exception:
        pass
    # shuffle the web trends so the Director doesn't always take #1
    rng = random.Random(seed)
    rng.shuffle(out)
    remaining = max(4, limit - len(out))
    out.extend(topic_bank.signals(limit=remaining, seed=seed))
    return out[:limit]


__all__ = ["gather", "reddit", "web", "topic_bank", "TrendSignal"]
