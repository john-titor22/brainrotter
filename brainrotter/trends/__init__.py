"""Trend signal aggregation."""

from __future__ import annotations

from ..models import TrendSignal
from . import reddit, topic_bank


def gather(*, limit: int = 20, seed: int | None = None) -> list[TrendSignal]:
    """All available signals, live sources first, topic bank as filler."""
    out: list[TrendSignal] = []
    out.extend(reddit.signals())
    remaining = max(4, limit - len(out))
    out.extend(topic_bank.signals(limit=remaining, seed=seed))
    return out


__all__ = ["gather", "reddit", "topic_bank", "TrendSignal"]
