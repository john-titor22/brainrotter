"""Local AI image generation for video visuals.

When a format's ``DEFAULT_VISUAL_TREATMENT`` is "generated" (or it's forced),
Brainrotter renders one still per script beat with Stable Diffusion — in its own
isolated virtualenv, driven as a subprocess like the ``avatar`` integration.
``engine.storyboard`` then Ken-Burns-zooms each still and plays it for the length
of its beat's narration.

`brainrotter visual-setup` installs it (~7 GB one-time). Until then
``available()`` is False and every format falls back to footage.
"""

from .generator import available, generate, is_installed, status
from .prompts import hero_prompt, negative_extra, storyboard, subject_look

__all__ = [
    "available", "is_installed", "generate", "status",
    "storyboard", "hero_prompt", "subject_look", "negative_extra",
]
