"""Self-sourcing background footage (yt-dlp) + local cache."""

from . import registry
from .harvester import cached, ensure, ensure_figure, have_enough, slugify, sync, sync_all
from .registry import categories

__all__ = [
    "cached", "ensure", "ensure_figure", "have_enough", "slugify",
    "sync", "sync_all", "categories", "registry",
]
