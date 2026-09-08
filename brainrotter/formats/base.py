"""The Format plugin contract.

A format is a self-contained recipe: how to write the script, and how to turn
that script into a RenderPlan. The Director picks between formats; a future
"format synthesizer" will emit new ones.
"""

from __future__ import annotations

from typing import Protocol

from ..engine import voices
from ..models import Brief, RenderPlan, Script

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
  "beats": [                          // 4-12 beats, each a spoken line
    {"narration": str,                // ONE sentence, spoken aloud. No emojis, no stage directions.
     "on_screen": str|null,           // optional short overlay (a name, a number, a label)
     "visual": str,                   // 3-6 words describing what should be on screen
     "sfx": str|null}                 // optional: "whoosh" | "boom" | "ding" | "record_scratch" | null
  ],
  "hashtags": [str, ...],             // 3-6, no leading '#'
  "cta": str|null                     // optional last-line call to action
}
The FIRST beat's narration must be the hook — it has ~1.5 seconds to stop the scroll.
Keep total spoken length within the target duration (roughly 2.5 words/second).
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
