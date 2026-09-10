"""Serialized "related story" mode.

The Director plans ONE story as an arc of parts (up to ``series.max_parts``),
then Brainrotter produces Part 1..N as sequential videos. Continuity is carried
between parts via a running "story so far" recap; the same narrator and music
run through the whole series; and — unlike every other format — the background
is Creative-Commons b-roll chosen to fit the narrative, not gameplay.

Flow:
    create(...)          -> plans the arc, locks voice/music, writes the series row
    enqueue(series_id)   -> creates the N queued part-jobs (dashboard worker path)
    build_part_brief(id, part) -> the Brief for one part, with SeriesContext
    record_part(...)     -> stores a recap so the next part can continue
    abort(...)           -> a part failed: cancel the rest, mark the series aborted

The CLI (`brainrotter run --series`) creates jobs one at a time as it produces
them; the dashboard enqueues all parts up front and its worker drains them.
"""

from __future__ import annotations

import json
import random

from . import db, trends
from .config import get_settings
from .director import director as _director
from .engine import voices
from .formats import registry
from .models import Brief, Script, SeriesContext
from .writer import llm

_LANG_NAME = {"en": "English", "ary": "Moroccan Darija"}


# --- arc planning ---------------------------------------------------------

_PLAN_SYS = (
    "You are a showrunner for serialized short-form video. Given a story premise "
    "and a target number of parts, design a COMPLETE multi-part arc that builds "
    "and then RESOLVES.\n"
    "Rules:\n"
    "- Each part is one ~40-second vertical video.\n"
    "- Parts 1..N-1 each end on a cliffhanger that forces the next watch.\n"
    "- Part N RESOLVES every open thread and delivers a real ending — no "
    "cliffhanger, no 'to be continued'.\n"
    "- Same characters, names and setting throughout. Escalate stakes each part.\n"
    "- Use between {min} and {target} parts. Prefer {target}; use fewer only if "
    "the story would obviously run out. The story MUST be over by the last part.\n"
    "- 'broll' phrases are short, literal stock-footage search terms for scenery "
    "and mood (e.g. 'rain on a city window at night', 'empty office hallway'). "
    "No real names, no text, no people-specific shots."
)

_PLAN_SCHEMA = """
Return ONLY a JSON object with this shape:
  title:       the saga's title, no "Part N"
  premise:     2-3 sentences — characters, setting, central conflict, where it heads
  mood:        one word for the music, one of: __MOODS__
  characters:  array of 1-4 strings, "Name - one line"
  parts:       array of objects, each with:
      goal        what happens in THIS part (2 sentences), in English
      hook        the exact first spoken line of this part (<=14 words)__HOOK_LANG__
      cliffhanger the unanswered question it ends on (empty string for the final part)
      broll       array of 3 literal stock-footage search phrases fitting this part
"""


def _plan_arc(*, topic: str, format_id: str, language: str, target: int,
              seed: int | None) -> dict:
    cfg = get_settings().series
    target = max(cfg.min_parts, min(cfg.max_parts, target or cfg.default_parts))
    fmt = registry.get(format_id)
    fmt_brief = _director._FORMAT_BRIEF.get(format_id, "")
    moods = list(getattr(fmt, "MUSIC_MOODS", None)
                 or _director._FORMAT_MUSIC_MOODS.get(format_id, ("chill", "hype")))

    if llm.available():
        hook_lang = ""
        if language != "en":
            hook_lang = (
                f" Write every \"hook\" in {_LANG_NAME.get(language, language)} "
                "(natural spoken register). Keep \"goal\"/\"cliffhanger\" in English."
            )
        try:
            data = llm.complete_json(
                _PLAN_SYS.format(min=cfg.min_parts, target=target)
                + "\n" + _PLAN_SCHEMA.replace("__HOOK_LANG__", hook_lang)
                                     .replace("__MOODS__", ", ".join(moods)),
                f"Story premise / topic: {topic}\n"
                f"Format: {format_id} — {fmt_brief}\n"
                f"Target parts: {target}\n"
                f"Hook language: {_LANG_NAME.get(language, 'English')}",
                max_tokens=2600, language=language,
            )
            plan = _clean_plan(data, topic, target)
            if plan:
                return plan
        except Exception:
            pass
    return _stub_plan(topic, target)


def _clean_plan(data: dict, topic: str, target: int) -> dict | None:
    cfg = get_settings().series
    parts_in = data.get("parts") or []
    if not isinstance(parts_in, list) or len(parts_in) < cfg.min_parts:
        return None
    parts_in = parts_in[: cfg.max_parts]
    parts: list[dict] = []
    for i, p in enumerate(parts_in, 1):
        if not isinstance(p, dict):
            continue
        parts.append({
            "n": i,
            "goal": str(p.get("goal", "")).strip() or f"Part {i} of the story.",
            "hook": str(p.get("hook", "")).strip(),
            "cliffhanger": str(p.get("cliffhanger", "")).strip(),
            "broll": [str(b).strip() for b in (p.get("broll") or []) if str(b).strip()][:4],
        })
    if len(parts) < cfg.min_parts:
        return None
    parts[-1]["cliffhanger"] = ""      # the finale never dangles
    return {
        "title": str(data.get("title", "")).strip() or topic.title(),
        "premise": str(data.get("premise", "")).strip() or topic,
        "mood": str(data.get("mood", "")).strip().lower(),
        "characters": [str(c).strip() for c in (data.get("characters") or []) if str(c).strip()][:4],
        "parts": parts,
    }


def _stub_plan(topic: str, target: int) -> dict:
    """Offline fallback — a generic escalation arc so the pipeline still runs."""
    beats = [
        "introduce the character and the ordinary situation, then the first crack appears",
        "the problem is bigger than it looked; a first attempt to deal with it fails",
        "stakes rise and an ally or enemy is revealed",
        "a plan is made and set in motion",
        "the plan goes wrong in a new way",
        "everything the character believed is turned around",
        "the lowest point — all seems lost",
        "one last desperate move",
        "the confrontation and the true cost of it all",
    ]
    n = max(3, min(target, len(beats)))
    parts = [
        {
            "n": i + 1,
            "goal": beats[i] if i < len(beats) else "push the story toward its end",
            "hook": "",
            "cliffhanger": "" if i == n - 1 else "and that was when it got worse",
            "broll": [topic, "cinematic establishing shot", "moody atmospheric scenery"],
        }
        for i in range(n)
    ]
    return {"title": topic.title(), "premise": topic, "characters": [], "parts": parts}


# --- create / enqueue --------------------------------------------------------

def create(*, topic: str | None = None, target_parts: int | None = None,
           format_id: str | None = None, language: str | None = None,
           voice: str | None = None, seed: int | None = None) -> dict:
    """Plan a series and persist it. Returns a summary dict (no jobs yet)."""
    db.init_db()
    cfg = get_settings().series
    rng = random.Random(seed if seed is not None else random.random())

    # Resolve topic / format / language via the Director (its format allocation,
    # signal picking and language choice) only when the caller left something
    # open — a fully-pinned request skips the Director's LLM hook call entirely.
    fallback_voice = voice
    if voice and not language and voice in voices._BY_NAME:
        language = voices.language_of(voice)
    # A format that plans its own arc (movie_recap) owns its topic — don't let
    # the Director substitute a trend-signal title for the movie name.
    self_planned = format_id and callable(
        getattr(registry.get(format_id), "plan_series", None))
    if not self_planned and not (topic and format_id and language):
        signals = trends.gather(seed=seed)
        seed_brief = _director.decide(signals, format_id=format_id, topic=topic,
                                      language=language, voice=voice, seed=seed)
        topic = topic or seed_brief.topic
        format_id = format_id or seed_brief.format_id
        language = language or seed_brief.language
        fallback_voice = voice or seed_brief.style.get("voice")
    language = (language or "en").lower()

    # A format can plan its own arc from real structure (movie_recap splits an
    # actual film by time). Otherwise the LLM invents a fictional arc.
    fmt_planner = getattr(registry.get(format_id), "plan_series", None)
    if callable(fmt_planner):
        plan = fmt_planner(topic=topic, target_parts=target_parts, language=language)
    else:
        plan = _plan_arc(topic=topic, format_id=format_id, language=language,
                         target=target_parts or cfg.default_parts, seed=seed)
    n_parts = len(plan["parts"])

    # Lock the narrator + music for the whole series.
    fmt = registry.get(format_id)
    vtags = tuple(getattr(fmt, "VOICE_TAGS", None)
                  or _director._FORMAT_VOICE_TAGS.get(format_id, ("narrator",)))
    if voice and voice in voices._BY_NAME:
        series_voice = voice
    elif cfg.lock_voice:
        series_voice = voices.pick(rng, language=language, tags=vtags,
                                   exclude=tuple(db.recent_voices(5))).name
    else:
        series_voice = fallback_voice or voices.for_tags(*vtags, language=language).name

    moods = list(getattr(fmt, "MUSIC_MOODS", None)
                 or _director._FORMAT_MUSIC_MOODS.get(format_id, ("chill", "hype")))
    allowed = set(get_settings().music.moods)
    planned_mood = plan.get("mood")
    music_mood = planned_mood if planned_mood in allowed else rng.choice(moods)

    plan["voice"] = series_voice
    plan["music_mood"] = music_mood
    plan["voice_rate"] = round(rng.uniform(*((1.0, 1.1) if language in ("ar", "ary") else (1.08, 1.2))), 2)

    sid = db.create_series(topic=topic, format_id=format_id, language=language,
                           n_parts=n_parts, plan=plan)
    return {
        "series_id": sid, "title": plan["title"], "n_parts": n_parts,
        "topic": topic, "format_id": format_id, "language": language,
        "voice": series_voice, "music_mood": music_mood,
    }


def enqueue(series_id: str) -> list[str]:
    """Create the queued part-jobs (dashboard worker path). Ordered by ``seq``."""
    s = db.get_series(series_id)
    if not s:
        raise KeyError(f"no such series {series_id}")
    ids = []
    for part in range(1, s["n_parts"] + 1):
        ids.append(db.create_job(
            format_id=s["format_id"], topic=s["topic"],
            overrides={"language": s["language"]},
            series_id=series_id, part=part, seq=part,
        ))
    return ids


# --- per-part brief --------------------------------------------------------

def build_part_brief(series_id: str, part: int) -> Brief:
    s = db.get_series(series_id)
    if not s:
        raise KeyError(f"no such series {series_id}")
    plan = db.series_plan(series_id)
    parts = plan.get("parts") or []
    if not (1 <= part <= len(parts)):
        raise ValueError(f"series {series_id} has no part {part}")
    entry = parts[part - 1]
    n = len(parts)
    is_finale = part >= n
    language = s["language"] or "en"

    state = db.series_state(series_id)
    recaps = state.get("recaps", {}) or {}
    # Only the last few parts' recaps — enough for continuity without ballooning
    # the prompt (which makes the local model slow and timeout-prone) on part 8+.
    story_so_far = "\n".join(
        f"Part {k}: {recaps[str(k)]}"
        for k in range(max(1, part - 4), part) if str(k) in recaps
    )

    settings = get_settings()
    rng = random.Random(f"{series_id}:{part}")

    # Same narrator + music mood every part; resolve the actual track once (on
    # part 1, in the worker) and reuse it for the rest.
    voice_name = plan.get("voice") or voices.DEFAULT
    mood = plan.get("music_mood")
    track = state.get("music_file")
    if mood and not track and settings.music.enabled:
        from . import music
        try:
            music.ensure(mood, count=settings.music.per_mood)
            track = music.pick(mood, rng, exclude=tuple(db.recent_music(8)))
        except Exception:
            track = None
        if track:
            db.merge_series_state(series_id, {"music_file": track})

    # Formats that ARE a subject (object_story / ai_brainrot) keep their
    # generated visuals even as a series — the object/creature narrates every
    # part. Only the narration-only formats fall back to CC b-roll.
    fmt_mod = registry.get(s["format_id"])
    treatment = getattr(fmt_mod, "DEFAULT_VISUAL_TREATMENT", "footage")

    style = {
        "voice": voice_name,
        "voice_rate": float(plan.get("voice_rate", 1.12)),
        "visual_treatment": treatment,
        "clip_duration": rng.choice([4, 5, 6]),
        "caption_position": "center",
        "font_size": rng.choice([80, 84, 88]),
        "music": "random",
        "music_mood": mood,
        "music_file": track,
        "music_volume": round(rng.uniform(0.12, 0.18), 2),
    }

    broll: list[str] = []
    if treatment != "generated":
        broll = entry.get("broll") or [
            s["topic"], "cinematic establishing shot", "moody atmospheric scenery"]

    hook = entry.get("hook") or _fallback_hook(plan, part, is_finale)

    meta = {k: entry[k] for k in ("start", "end", "movie_path", "movie_title")
            if k in entry}
    if plan.get("movie_path") and "movie_path" not in meta:
        meta["movie_path"] = plan["movie_path"]

    sctx = SeriesContext(
        series_id=series_id, title=plan.get("title") or s["topic"],
        part=part, part_count=n, premise=plan.get("premise") or s["topic"],
        part_goal=entry.get("goal") or f"Part {part} of the story.",
        story_so_far=story_so_far, is_finale=is_finale, broll=broll,
        part_meta=meta,
    )

    target = max(settings.video.min_seconds,
                 min(settings.video.max_seconds, settings.series.target_seconds))

    return Brief(
        format_id=s["format_id"], topic=s["topic"], language=language,
        angle=f"Part {part}/{n} of the series \"{sctx.title}\": {sctx.part_goal}",
        hook=hook, target_seconds=target,
        tone="serialized, escalating, continuity-driven",
        style=style,
        rationale=(
            f"series \"{sctx.title}\" part {part}/{n}"
            + ("  — FINALE (resolves)" if is_finale else "  — ends on a cliffhanger")
            + f"; voice={voice_name}; music={mood or 'random'}"
            + f"; CC b-roll: {', '.join(broll[:2])}"
        ),
        series=sctx,
    )


def _fallback_hook(plan: dict, part: int, is_finale: bool) -> str:
    if part == 1:
        return f"This is how it started."
    if is_finale:
        return f"Part {part}. This is how it ends."
    return f"Part {part}. It only got worse from here."


# --- recap / lifecycle ----------------------------------------------------

_RECAP_SYS = (
    "Summarize this part of an ongoing story in 1-2 plain sentences, for a "
    "'previously on' recap. Concrete: who did what, and where it left off. "
    "No commentary, no hype."
)


def record_part(series_id: str, part: int, script: Script, language: str = "en") -> None:
    """Store a short recap so the next part continues cleanly, and close the
    series when the last part is in."""
    s = db.get_series(series_id)
    if not s:
        return
    recap = _recap(script, language)
    db.merge_series_state(series_id, {"recaps": {str(part): recap}})
    if part >= s["n_parts"]:
        db.update_series(series_id, state="done")


def _recap(script: Script, language: str) -> str:
    text = script.narration_text[:2400]
    if llm.available():
        try:
            out = llm.complete_text(_RECAP_SYS, text, fast=True, max_tokens=140,
                                    language=language).strip()
            if out:
                return out[:450]
        except Exception:
            pass
    beats = [b.narration.strip() for b in script.beats if b.narration.strip()]
    return " ".join(beats[:1] + beats[-1:])[:400]


def abort(series_id: str, reason: str) -> None:
    n = db.cancel_series_remainder(series_id)
    db.update_series(series_id, state="aborted")
    db.merge_series_state(series_id, {"aborted_reason": reason, "canceled_parts": n})
