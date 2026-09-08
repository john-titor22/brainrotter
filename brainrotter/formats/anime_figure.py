"""Anime-figure brainrot: a real public figure narrated as an anime protagonist.

A hype shonen-narrator voice recasts a well-known person — a politician, CEO,
athlete, historical figure — as the star of an over-the-top anime: their
"archetype", their signature technique, their rival, a tragic backstory beat, a
final-form transformation, and a season-2 cliffhanger.

It's parody. The writer prompt keeps it to the person's *public persona* and
absurd invention — no fabricated real scandals stated as fact, nothing sexual,
no slurs. Visuals are AI-anime-image driven later; for now it runs over
high-energy background footage with big word-pop captions.
"""

from __future__ import annotations

from ..engine import voices
from ..models import Brief, CaptionStyle, RenderPlan, Script, ScriptBeat
from .base import common_json_rules

ID = "anime_figure"
NAME = "Anime Figure Brainrot"
DESCRIPTION = "A real public figure narrated as an over-the-top anime protagonist."
SIGNAL_KINDS = ["figure", "topic", "meme"]
# Background = footage OF the figure (speeches / interviews); these gameplay
# categories are only the fallback when that download fails.
USES_FIGURE_FOOTAGE = True
BG_CATEGORIES = ["subway", "parkour", "surf", "geometry_dash", "satisfying", "trackmania"]


def writer_system_prompt() -> str:
    return (
        "You are a hype anime narrator (think a fan-made AMV voiceover crossed with "
        "a shonen episode recap). You take a REAL, well-known public figure and "
        "recast them as the protagonist of an absurd anime, played completely "
        "straight and epic.\n"
        "Cover, in order: their anime ARCHETYPE (the chosen one / the fallen hero / "
        "the final boss / the mentor who dies in episode 3), a signature TECHNIQUE "
        "with a shouted name tied to what they're actually famous for, a RIVAL, one "
        "TRAGIC backstory beat, a FINAL FORM transformation, and a Season 2 "
        "cliffhanger.\n"
        "Rules: this is affectionate parody of the ONE person's PUBLIC image only. "
        "The rival must be a FICTIONAL/abstract force (a shadow council, entropy, "
        "the algorithm, their own doubt) — never another real named person cast as "
        "a villain. Do NOT invent real crimes, scandals, health claims, or "
        "private-life details and state them as fact. No sexual content, no slurs, "
        "nothing hateful. Silly and hype, not mean.\n" + common_json_rules()
    )


def writer_user_prompt(brief: Brief) -> str:
    lines = [
        f"Public figure: {brief.topic}",
        f"Anime angle: {brief.angle}" if brief.angle else "",
        f"Open on this hook: {brief.hook}" if brief.hook else "",
        f"Target length: about {brief.target_seconds} seconds.",
        "Write the anime recap now, as JSON. The title should be the anime's name "
        "(e.g. a dramatic subtitle for the figure). Put shouted technique names in "
        "the on_screen field.",
    ]
    return "\n".join(x for x in lines if x)


def parse_script(raw: dict) -> Script:
    beats = [
        ScriptBeat(
            narration=str(b.get("narration", "")).strip(),
            on_screen=(b.get("on_screen") or None),
            visual=str(b.get("visual", "anime hero, speed lines, glowing aura")).strip(),
            sfx=(b.get("sfx") or None),
        )
        for b in raw.get("beats", [])
        if str(b.get("narration", "")).strip()
    ]
    return Script(
        title=str(raw.get("title", "")).strip() or "The Chosen One",
        beats=beats,
        hashtags=[h.lstrip("#") for h in raw.get("hashtags", [])][:6]
        or ["anime", "brainrot", "edit", "fyp"],
        cta=(raw.get("cta") or None),
    )


def build_plan(brief: Brief, script: Script, background_clips: list[str]) -> RenderPlan:
    style = brief.style or {}
    voice = voices.get(style.get("voice") or voices.for_tags("hype", "narrator").name)
    return RenderPlan(
        subject=script.title or brief.topic,
        script_text=script.narration_text,
        voice_name=voice.name,
        voice_rate=float(style.get("voice_rate", 1.2)),
        background_clips=background_clips,
        background_source="local" if background_clips else "pexels",
        clip_duration=int(style.get("clip_duration", 3)),
        caption=CaptionStyle(
            position=style.get("caption_position", "center"),
            font_size=int(style.get("font_size", 90)),
            word_by_word=True,
            animation="pop_spring",
        ),
        music=style.get("music", "random"),
        music_volume=float(style.get("music_volume", 0.2)),
    )
