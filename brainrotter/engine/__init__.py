"""The rendering engine — a thin adapter over the vendored MoneyPrinterTurbo (MIT).

Brainrotter owns the Director, Writer, formats and feedback loop; MPT owns TTS,
caption alignment and ffmpeg compositing. `render_plan()` is the only entry point
the rest of the codebase should call.
"""

from .mpt_engine import EngineError, render_plan
from .runner import render_isolated

__all__ = ["render_plan", "render_isolated", "EngineError"]
