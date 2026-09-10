"""Self-sourcing background footage (yt-dlp) + local cache."""

from . import registry
from .harvester import (
    CC_LICENSE,
    autogrow,
    cached,
    ensure,
    ensure_figure,
    ensure_narrative,
    have_enough,
    slugify,
    sync,
    sync_all,
)
from .registry import categories

__all__ = [
    "cached", "ensure", "ensure_figure", "ensure_narrative", "have_enough",
    "slugify", "sync", "sync_all", "autogrow", "categories", "registry",
    "CC_LICENSE",
]
