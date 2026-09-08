"""Local talking-head generation for the anime_figure format.

Turns a photo of a public figure + the narration audio into a lip-synced video
of them "saying" the script, using SadTalker in its own isolated virtualenv
(SadTalker's dependency tree conflicts with ours, so it stays walled off and is
driven as a subprocess).

`brainrotter avatar-setup` installs it. Until then `available()` is False and
anime_figure falls back to plain footage.
"""

from .sadtalker import available, is_installed, talking_head
from .portraits import get_portrait

__all__ = ["available", "is_installed", "talking_head", "get_portrait"]
