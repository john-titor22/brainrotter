"""The Director — decides what to make and why.

v1: format allocation from config weights blended with measured performance
(`format_stats.ewma_score`) plus an exploration term; topic chosen from the
best matching trend signal; angle/hook via one cheap LLM call when a key is
available, heuristic otherwise.

This is the seam where the "Brain" grows: replace `_score_formats` and
`_pick_signal` with a learned model as metrics accumulate.
"""

from __future__ import annotations

import random

from .. import db, music
from ..config import get_settings
from ..engine import voices
from ..formats import registry
from ..models import Brief, TrendSignal
from ..writer import llm

_LANG_NAME = {"en": "English", "fr": "French", "ar": "Arabic (MSA)", "ary": "Moroccan Darija"}

# Fallback voice tags / music moods per format when the format module doesn't
# declare its own. The Director picks a specific voice + track within these.
_FORMAT_VOICE_TAGS = {
    "reddit_story": ("reddit", "storytime", "hype"),
    "ai_brainrot": ("documentary", "narrator", "ai_brainrot"),
    "anime_figure": ("hype", "narrator"),
    "object_story": ("storytime", "calm", "narrator"),
}
_FORMAT_MUSIC_MOODS = {
    "reddit_story": ("tense", "funny", "hype", "chill"),
    "ai_brainrot": ("eerie", "epic", "tense"),
    "anime_figure": ("epic", "hype", "tense"),
    "object_story": ("chill", "funny", "tense", "eerie"),
}


def decide(
    signals: list[TrendSignal],
    *,
    format_id: str | None = None,
    topic: str | None = None,
    language: str | None = None,
    voice: str | None = None,
    seed: int | None = None,
) -> Brief:
    settings = get_settings()
    rng = random.Random(seed)

    fmt_id = format_id or _choose_format(rng)
    fmt = registry.get(fmt_id)

    signal = None if topic else _pick_signal(signals, fmt, rng)
    chosen_topic = topic or (signal.title if signal else "a story that went too far")

    # An explicit voice pins the language too (unless one was also given).
    if voice and not language:
        language = voices.language_of(voice)
    language = (language or _choose_language(rng)).lower()

    target_seconds = rng.choice([30, 35, 40, 45, 50])
    target_seconds = max(settings.video.min_seconds,
                         min(settings.video.max_seconds, target_seconds))

    angle, hook = _angle_and_hook(fmt_id, chosen_topic, signal, language)

    style = _style_knobs(fmt, rng, language, voice)

    rationale = _rationale(fmt_id, signal, target_seconds, language, style)

    return Brief(
        format_id=fmt_id,
        topic=chosen_topic,
        language=language,
        angle=angle,
        hook=hook,
        target_seconds=target_seconds,
        tone="chaotic, fast, punchy" if fmt_id == "reddit_story"
        else "deadpan, mock-serious, escalating",
        style=style,
        rationale=rationale,
        source_signal=signal,
    )


# --- language --------------------------------------------------------------

def _choose_language(rng: random.Random) -> str:
    weights = dict(get_settings().language.weights) or {"en": 1.0}
    weights = {k: v for k, v in weights.items() if v > 0} or {"en": 1.0}
    # nudge away from the language of the last video so a multi-language config
    # actually alternates instead of streaking
    last = (db.recent_languages(1) or [None])[0]
    if last in weights and len(weights) > 1:
        weights[last] *= 0.4
    langs = list(weights)
    return rng.choices(langs, weights=[weights[l] for l in langs], k=1)[0]


# --- format allocation -------------------------------------------------------

def _choose_format(rng: random.Random) -> str:
    settings = get_settings()
    weights = _score_formats()
    ids = list(weights)
    # epsilon-greedy exploration
    if rng.random() < settings.director.exploration:
        return rng.choice(ids)
    total = sum(weights.values()) or 1.0
    r = rng.random() * total
    acc = 0.0
    for fid, w in weights.items():
        acc += w
        if r <= acc:
            return fid
    return ids[-1]


def _score_formats() -> dict[str, float]:
    settings = get_settings()
    stats = db.format_stats()
    out: dict[str, float] = {}
    for fid in registry.all_ids():
        base = settings.director.default_weights.get(fid, 0.3)
        s = stats.get(fid)
        # Blend prior with measured performance once a format has a track record.
        if s and s.get("n_published", 0) >= 3:
            perf = float(s.get("ewma_score", 0.0))
            base = 0.4 * base + 0.6 * perf
        out[fid] = max(0.02, base)
    return out


# --- signal selection -------------------------------------------------------

def _pick_signal(signals: list[TrendSignal], fmt, rng: random.Random) -> TrendSignal | None:
    matches = [
        s for s in signals
        if fmt.ID in s.format_hints or s.kind in fmt.SIGNAL_KINDS
    ]
    if not matches:
        matches = signals
    if not matches:
        return None
    matches.sort(key=lambda s: s.score + rng.random() * 0.15, reverse=True)
    return matches[0]


# --- angle / hook ----------------------------------------------------------

_FORMAT_BRIEF = {
    "reddit_story": "a first-person Reddit drama readalong; the hook is the "
                    "escalating opening line of the story.",
    "ai_brainrot": "a deadpan documentary about an absurd invented creature; "
                   "the hook opens the reveal.",
    "anime_figure": "a hype anime-narrator recap that recasts a REAL public "
                    "figure as an over-the-top anime protagonist. The hook is a "
                    "shonen-narrator line, e.g. 'They said he was just a "
                    "senator. They were wrong.' Parody of the public persona "
                    "only — no real scandals or private life.",
    "object_story": "a first-person monologue spoken BY an everyday object about "
                    "its own mundane life. The hook is the object's dry, "
                    "world-weary opening line, e.g. 'Nobody's driven me in "
                    "three weeks.' or 'I'm the last one in the bowl and I know "
                    "how this ends.'",
}


def _angle_and_hook(fmt_id: str, topic: str, signal: TrendSignal | None,
                    language: str = "en") -> tuple[str, str]:
    if llm.available():
        try:
            lang_line = ""
            if language != "en":
                lang_line = (
                    f"\nWrite the \"hook\" in {_LANG_NAME.get(language, language)} "
                    "(natural spoken register, not formal). Keep \"angle\" in English."
                )
                if language == "ary":
                    lang_line = (
                        "\nWrite the \"hook\" in Moroccan Darija, Arabic script, "
                        "the way people actually speak in the street — NOT Modern "
                        "Standard Arabic. Use Darija words (كاين، ديال، بزاف، "
                        "دابا، غادي، راه، بحال) not fus-ha (يوجد، الذي، جداً، سوف). "
                        "Keep \"angle\" in English."
                    )
            data = llm.complete_json(
                "You are a short-form video strategist. Given a topic and format, "
                "return {\"angle\": str, \"hook\": str}. The angle is the specific "
                "framing (one sentence). The hook is the exact first spoken line "
                "(<=14 words), in the VOICE of the format, engineered to stop the "
                "scroll." + lang_line,
                f"Format: {fmt_id} — {_FORMAT_BRIEF.get(fmt_id, '')}\nTopic: {topic}\n"
                + (f"Source excerpt: {signal.body[:800]}" if signal and signal.body else ""),
                fast=True, max_tokens=400, language=language,
            )
            return str(data.get("angle", "")).strip(), str(data.get("hook", "")).strip()
        except Exception:
            pass
    # heuristic fallback (English hook — the writer still produces the script in
    # the target language, this is only a seed)
    if fmt_id == "ai_brainrot":
        return "documentary reveal of an absurd creature", f"Deep in the archives, they found {topic}."
    if fmt_id == "anime_figure":
        return "recast as an over-the-top anime protagonist", f"They said {topic} was just a normal person. They were wrong."
    if fmt_id == "object_story":
        return "the object narrates its own mundane life, deadpan", f"So I'm {topic}, and today everything changed."
    return "escalating first-person conflict with a payoff", f"I never thought {topic} would blow up like this."


def _style_knobs(fmt, rng: random.Random, language: str = "en",
                 forced_voice: str | None = None) -> dict:
    bg = _pick_backgrounds(fmt, rng)

    # voice — a specific catalog voice for this language, varied across videos
    # (unless the caller pinned one).
    vtags = tuple(getattr(fmt, "VOICE_TAGS", None)
                  or _FORMAT_VOICE_TAGS.get(fmt.ID, ("narrator",)))
    if forced_voice and forced_voice in voices._BY_NAME:
        voice = voices.get(forced_voice)
    else:
        voice = voices.pick(rng, language=language, tags=vtags,
                            exclude=tuple(db.recent_voices(5)))

    # music — a mood, then a specific track inside it, both anti-repeat
    moods = list(getattr(fmt, "MUSIC_MOODS", None)
                 or _FORMAT_MUSIC_MOODS.get(fmt.ID, ("chill", "hype")))
    recent_moods = db.recent_music_moods(4)
    mw = [max(0.05, 0.4 ** recent_moods.count(m)) for m in moods]
    mood = rng.choices(moods, weights=mw, k=1)[0]
    track = None
    if get_settings().music.enabled:
        try:
            track = music.pick(mood, rng, exclude=tuple(db.recent_music(8)))
        except Exception:
            track = None

    return {
        "voice": voice.name,
        "voice_rate": round(rng.uniform(*(
            (1.0, 1.12) if language in ("ar", "ary") else (1.08, 1.25)
        )), 2),
        "clip_duration": rng.choice([3, 4, 5]),
        "caption_position": "center",
        "font_size": rng.choice([80, 84, 88]),
        "music": "random",                      # engine fallback if no track
        "music_mood": mood,
        "music_file": track,                    # specific track, wins over `music`
        "music_volume": round(rng.uniform(0.10, 0.20), 2),
        "background_category": bg[0],            # primary (back-compat)
        "background_categories": bg,             # 1-2 games, mixed within the video
    }


def _pick_backgrounds(fmt, rng: random.Random) -> list[str]:
    """Choose 1-2 background games for this video, biased away from the ones the
    last few videos used, so the feed keeps changing."""
    pool = list(getattr(fmt, "BG_CATEGORIES", None) or ["subway"])
    recent = db.recent_background_categories(6)
    weights = []
    for c in pool:
        penalty = 0.35 ** recent.count(c)          # heavily downweight repeats
        weights.append(max(0.02, penalty))
    first = rng.choices(pool, weights=weights, k=1)[0]
    if len(pool) > 1 and rng.random() < 0.5:
        rest = [c for c in pool if c != first]
        w2 = [max(0.02, 0.35 ** recent.count(c)) for c in rest]
        return [first, rng.choices(rest, weights=w2, k=1)[0]]
    return [first]


def _rationale(fmt_id: str, signal: TrendSignal | None, seconds: int,
               language: str = "en", style: dict | None = None) -> str:
    stats = db.format_stats().get(fmt_id, {})
    bits = [f"format={fmt_id}"]
    if stats.get("n_published"):
        bits.append(f"ewma={stats.get('ewma_score', 0):.2f} over {stats['n_published']} published")
    else:
        bits.append("no performance history yet — using config prior")
    if signal:
        bits.append(f"signal from {signal.source} (score {signal.score:.2f})")
    else:
        bits.append("topic supplied / from bank")
    if language != "en":
        bits.append(f"lang={_LANG_NAME.get(language, language)}")
    if style:
        bits.append(f"voice={style.get('voice', '?')}")
        m = style.get("music_mood")
        if m:
            bits.append(f"music={m}" + ("" if style.get("music_file") else " (random — pool empty)"))
    bits.append(f"target {seconds}s")
    return "; ".join(bits)
