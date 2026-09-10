"""Curated edge-tts voice catalog, tagged for the Director.

edge-tts exposes hundreds of voices; these are the ones that actually work for
brainrot narration, across the languages Brainrotter supports. `default_rate`
is the multiplier passed to the engine (1.0 = normal).

The Director calls `pick(rng, language=..., tags=..., exclude=...)` per video so
the voice changes from one video to the next instead of always being the first
match.
"""

from __future__ import annotations

import random

from pydantic import BaseModel

# language code -> edge-tts locale prefix. "ary" (Moroccan Darija) is written by
# the LLM in Arabic script and spoken by the Moroccan (ar-MA) neural voices.
# Deliberately small: English + Darija, one male + one female each. edge-tts has
# hundreds of voices; these are the two per language that actually carry
# short-form narration.
LOCALE = {"en": "en-US", "ary": "ar-MA"}
RTL_LANGS = {"ary"}


class Voice(BaseModel):
    name: str            # edge-tts short name
    label: str
    gender: str
    language: str        # en | ary  (Moroccan Darija)
    tags: list[str]      # Director hints: "narrator", "hype", "calm", "storytime", "reddit"…
    default_rate: float = 1.15


CATALOG: list[Voice] = [
    # --- English (the two most-used short-form narrator voices) -----------
    Voice(name="en-US-AndrewNeural", label="Andrew - warm, conversational",
          gender="male", language="en",
          tags=["storytime", "narrator", "reddit", "documentary", "ai_brainrot",
                "object_story", "hype"], default_rate=1.15),
    Voice(name="en-US-AvaNeural", label="Ava - friendly, natural",
          gender="female", language="en",
          tags=["storytime", "reddit", "calm", "narrator", "object_story",
                "hype"], default_rate=1.15),

    # --- Darija (Moroccan voices reading LLM-written Darija) --------------
    Voice(name="ar-MA-JamalNeural", label="Jamal - مغربي، راجل",
          gender="male", language="ary",
          tags=["narrator", "hype", "reddit", "ai_brainrot", "storytime",
                "object_story", "documentary"], default_rate=1.05),
    Voice(name="ar-MA-MounaNeural", label="Mouna - مغربية، مرا",
          gender="female", language="ary",
          tags=["storytime", "reddit", "hype", "calm", "narrator",
                "object_story"], default_rate=1.06),
]

_BY_NAME = {v.name: v for v in CATALOG}
DEFAULT = "en-US-AndrewNeural"


def get(name: str) -> Voice:
    return _BY_NAME.get(name, _BY_NAME[DEFAULT])


def language_of(name: str) -> str | None:
    """The Brainrotter language code for a catalog voice name (or None)."""
    v = _BY_NAME.get(name)
    return v.language if v else None


def catalog_by_language() -> dict[str, list[Voice]]:
    out: dict[str, list[Voice]] = {}
    for v in CATALOG:
        out.setdefault(v.language, []).append(v)
    return out


def for_language(language: str) -> list[Voice]:
    langs = [v for v in CATALOG if v.language == language]
    return langs or [v for v in CATALOG if v.language == "en"]


def for_tags(*tags: str, language: str = "en") -> Voice:
    """First voice in `language` matching any tag; falls back to the language's
    first voice, then the global default. Deterministic - use `pick` for variety."""
    wanted = set(tags)
    pool = for_language(language)
    for v in pool:
        if wanted & set(v.tags):
            return v
    return pool[0] if pool else _BY_NAME[DEFAULT]


def pick(
    rng: random.Random,
    *,
    language: str = "en",
    tags: tuple[str, ...] = (),
    exclude: tuple[str, ...] = (),
) -> Voice:
    """Weighted-random voice for this video.

    Prefers voices matching any of `tags`; down-weights anything in `exclude`
    (the last few videos' voices) so the narration keeps changing.
    """
    pool = for_language(language)
    wanted = set(tags)
    matches = [v for v in pool if wanted & set(v.tags)] or pool
    recent = list(exclude)
    weights = [0.15 ** recent.count(v.name) for v in matches]
    if not any(weights):
        weights = [1.0] * len(matches)
    return rng.choices(matches, weights=weights, k=1)[0]
