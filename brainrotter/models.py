"""Domain contracts passed between layers. Keep these stable; everything depends on them."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


def _now() -> datetime:
    return datetime.now(timezone.utc)


class JobState(str, Enum):
    QUEUED = "queued"
    DIRECTING = "directing"
    WRITING = "writing"
    SYNTHESIZING = "synthesizing"
    ALIGNING = "aligning"
    ASSEMBLING = "assembling"
    RENDERING = "rendering"
    DONE = "done"
    FAILED = "failed"


class TrendSignal(BaseModel):
    """A raw opportunity picked up by a trend source."""

    source: str                      # "reddit", "topic_bank", ...
    kind: str                        # "story", "topic", "audio", "meme"
    title: str
    body: str = ""
    url: str | None = None
    score: float = 0.0               # source-native popularity, normalized 0..1 by Director
    format_hints: list[str] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)


class Brief(BaseModel):
    """The Director's decision for one video."""

    format_id: str
    topic: str
    angle: str = ""                  # the specific take / framing
    hook: str = ""                   # first-line hook the writer should honor
    target_seconds: int = 40
    tone: str = "chaotic, fast, punchy"
    style: dict[str, Any] = Field(default_factory=dict)  # caption/voice/bg/cut knobs
    rationale: str = ""              # why the Director chose this
    source_signal: TrendSignal | None = None
    created_at: datetime = Field(default_factory=_now)


class ScriptBeat(BaseModel):
    narration: str                   # spoken line (fed to TTS)
    on_screen: str | None = None     # optional overlay text distinct from captions
    visual: str = ""                 # direction for the asset layer
    sfx: str | None = None           # optional sound cue name


class Script(BaseModel):
    title: str
    beats: list[ScriptBeat]
    hashtags: list[str] = Field(default_factory=list)
    cta: str | None = None
    est_seconds: float | None = None

    @property
    def narration_text(self) -> str:
        return " ".join(b.narration.strip() for b in self.beats if b.narration.strip())


class CaptionStyle(BaseModel):
    position: str = "center"             # top | center | bottom
    font_name: str = "BeVietnamPro-Bold.ttf"   # must exist in engine resource/fonts
    font_size: int = 84
    fore_color: str = "#FFFFFF"
    stroke_color: str = "#000000"
    stroke_width: float = 4.0
    word_by_word: bool = True            # the brainrot word-pop caption
    animation: str = "pop_spring"        # none | pop_spring


class RenderPlan(BaseModel):
    """Everything the engine needs to render one video. Maps to MPT's VideoParams.

    The engine does TTS + forced alignment + compositing; we only supply the
    script text, the voice, the background footage, and the style knobs.
    """

    subject: str                         # short label — used for filenames / social meta
    script_text: str                     # full narration
    voice_name: str = "en-US-AndrewNeural"
    voice_rate: float = 1.15             # 1.0 = normal
    voice_volume: float = 1.0
    background_clips: list[str] = Field(default_factory=list)   # local video paths
    background_source: str = "local"     # local | pexels | pixabay
    clip_duration: int = 5              # max seconds per background cut (cut cadence)
    caption: CaptionStyle = CaptionStyle()
    music: str = "random"               # "random" | "" (none) | filename in songs dir
    music_volume: float = 0.16
    aspect: str = "9:16"


class VideoResult(BaseModel):
    job_id: str
    path: str
    duration: float
    brief: Brief
    script: Script
    plan: RenderPlan | None = None
    created_at: datetime = Field(default_factory=_now)
    timings: dict[str, float] = Field(default_factory=dict)   # stage -> seconds
    cost: dict[str, float] = Field(default_factory=dict)      # e.g. {"llm_usd": 0.03}
