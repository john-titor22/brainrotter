"""Reddit-story readalong: first-person drama narrated over gameplay footage."""

from __future__ import annotations

from ..engine import voices
from ..models import Brief, CaptionStyle, RenderPlan, Script, ScriptBeat
from .base import common_json_rules

ID = "reddit_story"
NAME = "Reddit Story Readalong"
DESCRIPTION = "First-person AITA / revenge / confession story, TTS over gameplay."
SIGNAL_KINDS = ["story", "topic"]
BG_CATEGORIES = ["subway", "parkour", "gta", "temple_run", "trackmania", "slope", "roblox_obby"]


def writer_system_prompt() -> str:
    return (
        "You write viral first-person short-form video scripts in the style of "
        "Reddit story narration (r/AmItheAsshole, r/pettyrevenge, r/tifu). "
        "The voice is a real person telling a story to a friend: casual, specific, "
        "a little petty, emotionally escalating. Concrete details over generalities. "
        "No preamble like 'so basically' every line. Build tension, then land a "
        "satisfying or jaw-dropping ending. Keep it plausible.\n" + common_json_rules()
    )


def writer_user_prompt(brief: Brief) -> str:
    lines = [
        f"Topic / prompt: {brief.topic}",
        f"Angle: {brief.angle}" if brief.angle else "",
        f"Required opening hook (use as the first beat, tighten if needed): {brief.hook}"
        if brief.hook else "",
        f"Target length: about {brief.target_seconds} seconds of narration.",
        f"Tone: {brief.tone}.",
        "Write the story now as JSON.",
    ]
    return "\n".join(x for x in lines if x)


def parse_script(raw: dict) -> Script:
    beats = [
        ScriptBeat(
            narration=str(b.get("narration", "")).strip(),
            on_screen=(b.get("on_screen") or None),
            visual=str(b.get("visual", "gameplay")).strip(),
            sfx=(b.get("sfx") or None),
        )
        for b in raw.get("beats", [])
        if str(b.get("narration", "")).strip()
    ]
    return Script(
        title=str(raw.get("title", "")).strip() or "Reddit Story",
        beats=beats,
        hashtags=[h.lstrip("#") for h in raw.get("hashtags", [])][:6]
        or ["reddit", "storytime", "fyp"],
        cta=(raw.get("cta") or None),
    )


def build_plan(brief: Brief, script: Script, background_clips: list[str]) -> RenderPlan:
    style = brief.style or {}
    voice = voices.get(style.get("voice") or voices.for_tags("reddit", "storytime").name)
    return RenderPlan(
        subject=script.title or brief.topic,
        script_text=script.narration_text,
        voice_name=voice.name,
        voice_rate=float(style.get("voice_rate", voice.default_rate)),
        background_clips=background_clips,
        background_source="local" if background_clips else "pexels",
        clip_duration=int(style.get("clip_duration", 5)),
        caption=CaptionStyle(
            position=style.get("caption_position", "center"),
            font_size=int(style.get("font_size", 84)),
            word_by_word=True,
            animation="pop_spring",
        ),
        music=style.get("music", "random"),
        music_volume=float(style.get("music_volume", 0.12)),
    )
