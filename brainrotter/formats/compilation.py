"""Compilation: a supercut of short real clips on a theme.

The Director picks a theme (``funny``, ``satisfying``, ``animals``,
``wholesome``, ``fails``, ``amazing``); Brainrotter sources short clips for it
(Reddit API + yt-dlp), cuts a 2-5s slice from each, and hard-cuts ~10 of them
into one ~35s vertical video. The clips keep their own audio; a quiet music bed
sits under it. No writer, no narration, no captions, no generated visuals.

``render_video`` owns the whole thing (like ``movie_recap``).

Sourcing quality depends on Reddit API credentials — set ``REDDIT_CLIENT_ID`` /
``REDDIT_CLIENT_SECRET`` in ``.env`` (free: https://www.reddit.com/prefs/apps →
"create app" → script type). Without them it falls back to Reddit's rate-limited
RSS feeds + YouTube search.

Copyright: sourced clips are other people's uploads — same grey zone as the
footage/music self-sourcing. Your call; these stay unpublished by default.
"""

from __future__ import annotations

from ..models import Brief, Script, ScriptBeat

ID = "compilation"
NAME = "Compilation"
DESCRIPTION = "A supercut of short sourced clips on a theme (funny, satisfying, animal fails...): clip audio + quiet music, no narration."
SIGNAL_KINDS = ["trend", "topic"]
REQUIRES_EXPLICIT = False        # Director auto-picks it
NEEDS_WRITER = False             # render_video owns the whole video
DEFAULT_VISUAL_TREATMENT = "footage"   # n/a


def render_video(brief: Brief, ctx) -> dict:
    from ..compilation import render as R
    from ..compilation import themes as themes

    style = brief.style or {}
    theme_key = style.get("compilation_theme")
    if not theme_key:
        theme_key = themes.resolve(brief.topic).key
    return R.build(theme_key, brief=brief, ctx=ctx)


# --- Format protocol stubs (never reached — render_video owns everything) ---

def writer_system_prompt() -> str:
    return "n/a — compilation has no writer"


def writer_user_prompt(brief: Brief) -> str:
    return brief.topic


def parse_script(raw: dict) -> Script:
    return Script(title=str(raw.get("title", "Compilation")),
                  beats=[ScriptBeat(narration=str(b.get("narration", "")).strip())
                         for b in raw.get("beats", []) if b.get("narration")])


def build_plan(brief: Brief, script: Script, background_clips: list[str]):
    raise RuntimeError("compilation.render_video owns the render; build_plan is unused")
