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

_LANG_NAME = {"en": "English", "ary": "Moroccan Darija"}

# Fallback voice tags / music moods per format when the format module doesn't
# declare its own. The Director picks a specific voice + track within these.
_FORMAT_VOICE_TAGS = {
    "reddit_story": ("reddit", "storytime", "hype"),
    "ai_brainrot": ("documentary", "narrator", "ai_brainrot"),
    "anime_figure": ("hype", "narrator"),
    "object_story": ("storytime", "calm", "narrator"),
}
_FORMAT_MUSIC_MOODS = {
    "reddit_story": ("tense", "funny", "hype", "chill", "dramatic", "phonk", "sad"),
    "ai_brainrot": ("eerie", "epic", "tense", "dramatic", "quirky", "phonk"),
    "anime_figure": ("epic", "hype", "tense", "phonk", "dramatic", "nostalgic"),
    "object_story": ("chill", "funny", "sad", "nostalgic", "dreamy", "quirky", "eerie"),
}

_MUSIC_BRIEF_SYS = (
    "You pick the background music for a short vertical video. Given the format, "
    "the topic and a rough mood, name the exact vibe in 3-7 words — genre + "
    "feel, the kind of thing that's actually used on TikTok/Reels for this. "
    "Examples: 'dark drift phonk, aggressive', 'warped music box, uneasy', "
    "'warm nostalgic lofi, bittersweet', 'epic orchestral build, heroic', "
    "'deadpan pizzicato, comedic'. Answer with ONLY that phrase."
)


def _music_brief(fmt_id: str, topic: str, mood: str) -> str:
    """A specific, search-ready music vibe for this exact video."""
    if llm.available():
        try:
            out = llm.complete_text(
                _MUSIC_BRIEF_SYS,
                f"Format: {fmt_id}\nTopic: {topic}\nMood: {mood}",
                fast=True, max_tokens=32,
            ).strip().strip('"\'`.').splitlines()[0].strip()
            if 3 <= len(out) <= 80:
                return out
        except Exception:
            pass
    return f"{mood} background music"


def decide(
    signals: list[TrendSignal],
    *,
    format_id: str | None = None,
    topic: str | None = None,
    language: str | None = None,
    voice: str | None = None,
    visual_treatment: str | None = None,
    seed: int | None = None,
) -> Brief:
    settings = get_settings()
    rng = random.Random(seed)

    fmt_id = format_id or _choose_format(rng)
    fmt = registry.get(fmt_id)

    if fmt_id == "compilation":
        return _compilation_brief(rng, topic, signals, seed)

    signal = None if topic else _pick_signal(signals, fmt, rng)
    if topic:
        chosen_topic = topic
    elif signal and signal.kind == "trend":
        chosen_topic = _topic_from_trend(signal, fmt_id)     # reshape a raw trend
    elif signal:
        chosen_topic = signal.title
    else:
        chosen_topic = "a story that went too far"

    # An explicit voice pins the language too (unless one was also given).
    if voice and not language:
        language = voices.language_of(voice)
    language = (language or _choose_language(rng)).lower()

    target_seconds = rng.choice([30, 35, 40, 45, 50])
    target_seconds = max(settings.video.min_seconds,
                         min(settings.video.max_seconds, target_seconds))

    angle, hook = _angle_and_hook(fmt_id, chosen_topic, signal, language)

    style = _style_knobs(fmt, rng, language, voice)
    if visual_treatment in ("footage", "generated"):
        style["visual_treatment"] = visual_treatment
    if settings.music.enabled and settings.music.per_video_query:
        style["music_query"] = _music_brief(fmt_id, chosen_topic, style.get("music_mood", ""))

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


# --- compilation ----------------------------------------------------------

def _compilation_brief(rng: random.Random, topic: str | None,
                       signals: list[TrendSignal], seed: int | None) -> Brief:
    """The compilation format needs almost nothing the normal pipeline builds —
    no writer, no voice, no captions, no visual treatment. Just a theme and a
    quiet music track."""
    from ..compilation import sources as _csrc
    from ..compilation import themes as _cth

    settings = get_settings()

    if topic and topic.strip():
        # the user asked for a specific topic — honour it exactly (built-in when
        # it plainly matches one, otherwise an ad-hoc theme sourced for it)
        theme = _cth.resolve(topic.strip())
        theme_key = theme.key
    else:
        keys = list(_cth.THEMES)
        recent = _csrc.recent_themes(3)
        blob = " ".join((s.title or "") for s in signals).lower()
        weights = []
        for k in keys:
            hits = sum(1 for kw in _cth.THEMES[k].keywords if kw in blob)
            w = (1.0 + hits) * (0.15 ** recent.count(k))
            weights.append(max(0.02, w))
        theme_key = rng.choices(keys, weights=weights, k=1)[0]
        theme = _cth.THEMES[theme_key]

    # quiet music bed: the theme's mood, a specific track if the pool has one
    mood = theme.music_mood
    track = None
    if settings.music.enabled:
        try:
            track = music.pick(mood, rng, exclude=tuple(db.recent_music(8)))
            if track is None and settings.music.auto_sync:
                music.ensure(mood, count=2)
                track = music.pick(mood, rng)
        except Exception:
            track = None

    target = settings.compilation.target_seconds
    style = {
        "compilation_theme": theme_key,
        "music_mood": mood,
        "music_file": track,
        "music_volume": settings.compilation.music_volume,
    }
    rationale = (
        f"format=compilation; theme={theme_key}; "
        f"clips from r/{', r/'.join(theme.subreddits[:3])}…; "
        f"music={mood}" + ("" if track else " (pool empty — will fetch)")
        + f"; target {target}s"
    )
    return Brief(
        format_id="compilation",
        topic=theme.label,
        language="en",
        angle=f"a supercut of short {theme.label}",
        hook="",
        target_seconds=target,
        tone="fast cuts, clip audio, no narration",
        style=style,
        rationale=rationale,
        source_signal=None,
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
        # Formats that need an explicit topic + asset (movie_recap needs a movie
        # file) are never picked by the autonomous Director.
        if getattr(registry.get(fid), "REQUIRES_EXPLICIT", False):
            continue
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
    # a raw web trend can be reshaped to fit any format, so it always matches
    matches = [
        s for s in signals
        if s.kind == "trend" or fmt.ID in s.format_hints or s.kind in fmt.SIGNAL_KINDS
    ]
    if not matches:
        matches = signals
    if not matches:
        return None

    def rank(s: TrendSignal) -> float:
        native = 0.25 if (fmt.ID in s.format_hints or s.kind in fmt.SIGNAL_KINDS) else 0.0
        return s.score + native + rng.random() * 0.35

    matches.sort(key=rank, reverse=True)
    top = matches[: max(3, len(matches) // 3)]
    return rng.choice(top)


_TREND_TOPIC_SYS = {
    "reddit_story": (
        "You take something in the news / trending and invent a specific "
        "first-person Reddit-drama premise it could plausibly cause (AITA, petty "
        "revenge, malicious compliance, confession). One sentence, concrete, a "
        "real human conflict.\n"
        "Trending 'airline strike' -> 'AITA for leaving my coworker stranded at "
        "the airport after she mocked me for booking a refundable ticket?'\n"
        "Trending 'housing prices' -> 'My landlord raised my rent 40 percent, so "
        "I reported every code violation I'd been ignoring for three years.'"
    ),
    "ai_brainrot": (
        "You turn something trending into an absurd 'Italian brainrot' "
        "creature/artifact — a straight-faced, ominous, fake-specific monster. "
        "One phrase.\n"
        "Trending 'AI chatbots' -> 'a fax machine that gained sentience and now "
        "only speaks in customer-service apologies'\n"
        "Trending 'crypto crash' -> 'a vending machine deity that eats coins and "
        "prophesies market doom'"
    ),
    "anime_figure": (
        "If the trending item is a real, widely-known PERSON, reply with only "
        "their exact name. Otherwise invent ONE mascot character that embodies "
        "it (a name + 2-4 words).\n"
        "Trending 'Elon Musk' -> 'Elon Musk'\n"
        "Trending 'the stock market' -> 'The Bull, a suited minotaur of pure "
        "greed'"
    ),
    "object_story": (
        "You turn something trending into ONE everyday object that would narrate "
        "its own quiet, deadpan-poignant life story shaped by it. One phrase.\n"
        "Trending 'remote work' -> 'the office chair nobody has sat in for two "
        "years'\n"
        "Trending 'the lottery' -> 'the losing scratch ticket crumpled in a "
        "gas-station bin'"
    ),
    "movie_recap": "",
}


def _topic_from_trend(signal: TrendSignal, fmt_id: str) -> str:
    """A raw trend phrase -> a premise that actually fits the format."""
    if not llm.available():
        return signal.title
    sys = _TREND_TOPIC_SYS.get(fmt_id) or _TREND_TOPIC_SYS["reddit_story"]
    try:
        out = llm.complete_text(
            sys + "\nReply with ONLY the premise, one line, no quotes, no preamble.",
            f"Trending now: {signal.title}\n{(signal.body or '')[:280]}\n\nPremise:",
            fast=True, max_tokens=70, temperature=0.8,
        ).strip().strip('"\'`').splitlines()[0].strip()
        low = out.lower()
        if 4 < len(out) < 160 and low != signal.title.lower() and " " in out:
            return out
    except Exception:
        pass
    # reshape failed — a bare trend phrase is a bad topic for a story format
    if fmt_id in ("reddit_story", "object_story"):
        return f"a situation that spiralled because of {signal.title}"
    return signal.title


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

    treatment, visual_note = _visual_treatment(fmt)

    return {
        "voice": voice.name,
        "voice_rate": round(rng.uniform(*(
            (1.0, 1.12) if language in ("ar", "ary") else (1.08, 1.25)
        )), 2),
        "visual_treatment": treatment,
        "_visual_note": visual_note,
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


def _visual_treatment(fmt) -> tuple[str, str]:
    """Each format declares its visual identity via ``DEFAULT_VISUAL_TREATMENT``
    ('footage' = gameplay, 'generated' = local AI stills of the subject). This is
    deterministic per format — not a dice roll. 'generated' silently degrades to
    'footage' until `brainrotter visual-setup` has run.

    Returns (treatment, note) — note explains a downgrade, for the rationale."""
    want = getattr(fmt, "DEFAULT_VISUAL_TREATMENT", "footage")
    if want != "generated":
        return "footage", ""
    try:
        from .. import visuals

        if visuals.available():
            return "generated", ""
    except Exception:
        pass
    return "footage", "generated visuals wanted — run `brainrotter visual-setup` (using footage)"


def _pick_backgrounds(fmt, rng: random.Random) -> list[str]:
    """Choose 1-2 background games for this video, biased away from the ones the
    last few videos used, so the feed keeps changing.

    Prefer categories that actually have footage cached — picking one that's
    empty just makes ``assets.pick_mixed`` fall back to "any clip", which is why
    the same background kept repeating."""
    pool = list(getattr(fmt, "BG_CATEGORIES", None) or ["subway"])
    try:
        from .. import assets

        have = set(assets.available_categories())
        live = [c for c in pool if c in have]
        if live:
            pool = live                      # only pick games we actually have footage for
    except Exception:
        pass
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
        if style.get("visual_treatment") == "generated":
            bits.append("visuals=AI-generated stills of the subject")
        elif style.get("_visual_note"):
            bits.append(style["_visual_note"])
    bits.append(f"target {seconds}s")
    return "; ".join(bits)
