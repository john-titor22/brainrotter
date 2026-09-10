"""The Format plugin contract.

A format is a self-contained recipe: how to write the script, and how to turn
that script into a RenderPlan. The Director picks between formats; a future
"format synthesizer" will emit new ones.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

from ..engine import voices
from ..models import Brief, RenderPlan, Script


@dataclass
class RenderContext:
    """Handed to a format's optional ``render_video(brief, ctx)`` hook.

    A format that defines ``render_video`` owns its WHOLE video — assets, script
    and render (a movie recap, a compilation cut, a 2D animation, a chat UI). It
    returns ``{"path": str, "duration": float, "seconds": float, "script": dict}``
    (``script`` optional, used for the DB record / series recap).

    Formats WITHOUT ``render_video`` keep using ``writer_*`` + ``build_plan`` and
    the MoneyPrinterTurbo engine, exactly as before.
    """

    job_id: str
    settings: Any
    seed: int
    workdir: Path                     # scratch dir for this job, already created
    render_lock: Callable[[], Any]    # context manager — hold around any heavy ffmpeg/MoviePy render

# Arabic-script caption face, bundled next to the Latin one in engine resource/fonts.
RTL_CAPTION_FONT = "NotoNaskhArabic-Bold.ttf"


class Format(Protocol):
    id: str
    name: str
    description: str
    # Director hints: which trend signals feed this format well.
    signal_kinds: list[str]

    def writer_system_prompt(self) -> str: ...

    def writer_user_prompt(self, brief: Brief) -> str: ...

    def parse_script(self, raw: dict) -> Script: ...

    def build_plan(self, brief: Brief, script: Script, background_clips: list[str]) -> RenderPlan: ...


_COMMON_JSON_RULES = """
Return ONLY a JSON object, no prose, no markdown fences. Schema:
{
  "title": str,                       // punchy, <70 chars, this is the on-platform title
  "beats": [                          // 5-12 beats, each a spoken line
    {"narration": str,                // ONE sentence, spoken aloud. No emojis, no stage directions.
     "on_screen": str|null,           // optional short overlay (a name, a number, a label)
     "visual": str,                   // 4-10 words: the exact shot for this beat
     "sfx": str|null}                 // optional: "whoosh" | "boom" | "ding" | "record_scratch" | null
  ],
  "hashtags": [str, ...],             // 3-6, no leading '#'
  "cta": str|null                     // optional last-line call to action
}

STORY RULES — a short-form video lives or dies on these:
- HOOK: beat 1 drops the viewer mid-situation with a concrete, specific detail
  or an unanswered question. Never "Let me tell you about..." or "So basically".
- LOGIC: every beat must follow from the one before it. No contradictions. The
  ending must pay off exactly what the hook set up — if the hook raises a
  question, the last beat answers it (or lands the twist).
- PACING: do NOT make every beat the same pitch. Roughly: 1-2 beats of setup,
  a turn in the middle where it changes, rising stakes, the biggest moment
  second-from-last, then a short punchy final line. Spend words on the
  interesting part; cut the boring middle.
- SPECIFIC, NOT GENERIC: one real, concrete, slightly odd detail carries the
  whole thing (a name, a number, an object, a place, an exact line someone
  said). Ban filler beats like "things escalated quickly", "I couldn't believe
  it", "and that's when everything changed". Pick the LESS obvious version of
  this story.
- Keep total spoken length within the target duration (~2.5 words/second).
"""


def common_json_rules() -> str:
    return _COMMON_JSON_RULES


def resolve_voice(style: dict, brief: Brief, *fallback_tags: str):
    """The Director normally supplies ``style['voice']``; otherwise fall back to
    a tag match in the brief's language."""
    name = style.get("voice")
    if name:
        v = voices.get(name)
        # guard against a stale English voice on a non-English brief
        if v.language == brief.language or brief.language == "en":
            return v
    return voices.for_tags(*fallback_tags, language=brief.language)


def finalize_plan(plan: RenderPlan, brief: Brief, style: dict) -> RenderPlan:
    """Apply the cross-format bits: language, RTL captions, the Director's track."""
    plan.language = brief.language
    if brief.language in voices.RTL_LANGS:
        plan.caption.rtl = True
        plan.caption.font_name = RTL_CAPTION_FONT
    if style.get("music_file"):
        plan.music_file = style["music_file"]
    return plan
