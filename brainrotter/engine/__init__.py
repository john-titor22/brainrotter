"""The rendering engines.

- ``render_isolated(plan)`` — the footage path, via the vendored MoneyPrinterTurbo
  (MIT) in a child process. MPT owns TTS + caption burn + ffmpeg compositing.
- ``storyboard.render(...)`` / ``storyboard.finish(...)`` — a native ffmpeg-only
  renderer (no MoviePy) for AI-still and movie-recap videos: Ken-Burns + word-pop
  ASS captions + ducked music in one pass.
- ``movie`` — cut a local film into a silent 9:16 montage + transcribe it.
"""

from .mpt_engine import EngineError, render_plan
from .runner import render_isolated

__all__ = ["render_plan", "render_isolated", "EngineError"]
