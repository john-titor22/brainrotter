"""AI brainrot: absurd invented characters and lore, narrated documentary-style.

Think "Italian brainrot" (Tralalero Tralala, Bombardiro Crocodilo) — a straight-
faced narrator describing a ridiculous creature/universe as if it were a nature
documentary or a lore explainer. Visuals are AI-image driven later; for now it
runs over abstract / satisfying background footage.
"""

from __future__ import annotations

from ..engine import voices
from ..models import Brief, CaptionStyle, RenderPlan, Script, ScriptBeat
from .base import common_json_rules

ID = "ai_brainrot"
NAME = "AI Brainrot Lore"
DESCRIPTION = "Deadpan narrator explains an absurd invented creature / universe."
SIGNAL_KINDS = ["topic", "meme", "audio"]
BG_CATEGORIES = ["satisfying", "subway", "cluster_rush", "geometry_dash", "surf", "trackmania", "cooking"]


def writer_system_prompt() -> str:
    return (
        "You invent absurd 'brainrot' characters and narrate them with a completely "
        "straight face, like a documentary or a fandom lore explainer. The comedy is "
        "in the contrast: unhinged premise, serious delivery, fake-specific detail "
        "(measurements, dates, rivalries, tragic backstory). Coin a memorable name "
        "(often rhyming or onomatopoeic). Escalate to a mythic or ominous ending.\n"
        + common_json_rules()
    )


def writer_user_prompt(brief: Brief) -> str:
    lines = [
        f"Seed concept: {brief.topic}",
        f"Angle: {brief.angle}" if brief.angle else "",
        f"Open on this hook: {brief.hook}" if brief.hook else "",
        f"Target length: about {brief.target_seconds} seconds.",
        "Invent the character and its lore now, as JSON. The title should be the "
        "creature's name.",
    ]
    return "\n".join(x for x in lines if x)


def parse_script(raw: dict) -> Script:
    beats = [
        ScriptBeat(
            narration=str(b.get("narration", "")).strip(),
            on_screen=(b.get("on_screen") or None),
            visual=str(b.get("visual", "surreal creature")).strip(),
            sfx=(b.get("sfx") or None),
        )
        for b in raw.get("beats", [])
        if str(b.get("narration", "")).strip()
    ]
    return Script(
        title=str(raw.get("title", "")).strip() or "Unnamed Entity",
        beats=beats,
        hashtags=[h.lstrip("#") for h in raw.get("hashtags", [])][:6]
        or ["brainrot", "lore", "fyp"],
        cta=(raw.get("cta") or None),
    )


def build_plan(brief: Brief, script: Script, background_clips: list[str]) -> RenderPlan:
    style = brief.style or {}
    voice = voices.get(style.get("voice") or voices.for_tags("documentary", "narrator").name)
    return RenderPlan(
        subject=script.title or brief.topic,
        script_text=script.narration_text,
        voice_name=voice.name,
        voice_rate=float(style.get("voice_rate", 1.08)),
        background_clips=background_clips,
        background_source="local" if background_clips else "pexels",
        clip_duration=int(style.get("clip_duration", 4)),
        caption=CaptionStyle(
            position=style.get("caption_position", "center"),
            font_size=int(style.get("font_size", 88)),
            word_by_word=True,
            animation="pop_spring",
        ),
        music=style.get("music", "random"),
        music_volume=float(style.get("music_volume", 0.18)),
    )
