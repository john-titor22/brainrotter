"""The ``compilation`` format's engine: theme registry, clip sourcing, render.

A compilation video is a supercut of short REAL clips on a theme (funny, oddly
satisfying, animal fails, wholesome...). No narration, no captions, no generated
visuals — just 2-5s slices of sourced clips, hard-cut together, with the clips'
own audio and a quiet music bed under it.

Sourcing: Reddit API (free, reliable) when ``REDDIT_CLIENT_ID`` /
``REDDIT_CLIENT_SECRET`` are set, else Reddit's ``.rss`` feeds (rate-limited),
plus a YouTube search fallback. Downloading is always yt-dlp.
"""

from __future__ import annotations

from .themes import THEMES, Theme, build_adhoc, get, match_theme, resolve, theme_keys

__all__ = ["THEMES", "Theme", "build_adhoc", "get", "match_theme", "resolve",
           "theme_keys"]
