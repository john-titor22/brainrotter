"""Talking Object: an everyday thing narrates its own life, first person.

A car, a banana, a vending machine, a single sock — given a voice, a personality,
petty grievances and small joys, telling a story about something mundane as if
it were the most important day of its life. Deadpan-poignant, a little funny, a
little sad. Runs over satisfying / abstract background footage with big word-pop
captions; later the object itself can be an AI image or a lip-synced photo.
"""

from __future__ import annotations

from ..models import Brief, CaptionStyle, RenderPlan, Script, ScriptBeat
from .base import common_json_rules, finalize_plan, resolve_voice

ID = "object_story"
NAME = "Talking Object"
DESCRIPTION = "An everyday object — a car, a banana, a vending machine — narrates its own life story."
SIGNAL_KINDS = ["topic", "meme", "object"]
BG_CATEGORIES = ["satisfying", "cooking", "subway", "cluster_rush", "surf", "geometry_dash", "trackmania"]

VOICE_TAGS = ("storytime", "calm", "narrator", "reddit")
MUSIC_MOODS = ("chill", "funny", "tense", "eerie")


def writer_system_prompt() -> str:
    return (
        "You write a short first-person monologue spoken BY an everyday object — "
        "the object is the narrator and the main character. Give it a distinct "
        "personality (proud, anxious, bitter, hopeful, done-with-everyone), a "
        "few very specific petty grievances and small joys, and a small dramatic "
        "arc about something completely mundane treated as life-or-death: being "
        "left in a hot car, being the last one in the bowl, a rivalry with the "
        "appliance next to it, being picked or not picked, being replaced.\n"
        "Tone: deadpan and a little poignant, with dry humour. The comedy is the "
        "gap between how small the stakes are and how seriously the object takes "
        "them. Land on a bittersweet or funny final line.\n"
        "Rules: keep it PG — no gore, nothing sexual, no slurs. The object never "
        "becomes a monster or a creature; it stays a normal object with feelings.\n"
        + common_json_rules()
    )


def writer_user_prompt(brief: Brief) -> str:
    lines = [
        f"The object: {brief.topic}",
        f"Angle: {brief.angle}" if brief.angle else "",
        f"Open on this hook (the object's first spoken line): {brief.hook}" if brief.hook else "",
        f"Target length: about {brief.target_seconds} seconds.",
        "Write the monologue now, as JSON. The title is what the object would "
        "call its own story. Speak entirely in first person as the object.",
    ]
    return "\n".join(x for x in lines if x)


def parse_script(raw: dict) -> Script:
    beats = [
        ScriptBeat(
            narration=str(b.get("narration", "")).strip(),
            on_screen=(b.get("on_screen") or None),
            visual=str(b.get("visual", "the object, close up, shallow depth of field")).strip(),
            sfx=(b.get("sfx") or None),
        )
        for b in raw.get("beats", [])
        if str(b.get("narration", "")).strip()
    ]
    return Script(
        title=str(raw.get("title", "")).strip() or "A Small Story",
        beats=beats,
        hashtags=[h.lstrip("#") for h in raw.get("hashtags", [])][:6]
        or ["brainrot", "storytime", "pov", "fyp"],
        cta=(raw.get("cta") or None),
    )


def build_plan(brief: Brief, script: Script, background_clips: list[str]) -> RenderPlan:
    style = brief.style or {}
    voice = resolve_voice(style, brief, *VOICE_TAGS)
    plan = RenderPlan(
        subject=script.title or brief.topic,
        script_text=script.narration_text,
        voice_name=voice.name,
        voice_rate=float(style.get("voice_rate", voice.default_rate)),
        background_clips=background_clips,
        background_source="local" if background_clips else "pexels",
        clip_duration=int(style.get("clip_duration", 4)),
        caption=CaptionStyle(
            position=style.get("caption_position", "center"),
            font_size=int(style.get("font_size", 86)),
            word_by_word=True,
            animation="pop_spring",
        ),
        music=style.get("music", "random"),
        music_volume=float(style.get("music_volume", 0.16)),
    )
    return finalize_plan(plan, brief, style)
