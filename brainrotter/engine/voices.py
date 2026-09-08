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
LOCALE = {"en": "en-US", "fr": "fr-FR", "ar": "ar-EG", "ary": "ar-MA"}
RTL_LANGS = {"ar", "ary"}


class Voice(BaseModel):
    name: str            # edge-tts short name
    label: str
    gender: str
    language: str        # en | fr | ar | ary
    tags: list[str]      # Director hints: "narrator", "hype", "calm", "storytime", "reddit"…
    default_rate: float = 1.15


CATALOG: list[Voice] = [
    # --- English -----------------------------------------------------------
    Voice(name="en-US-AndrewNeural", label="Andrew - warm, conversational",
          gender="male", language="en",
          tags=["storytime", "narrator", "reddit"], default_rate=1.15),
    Voice(name="en-US-BrianNeural", label="Brian - casual, youthful",
          gender="male", language="en",
          tags=["storytime", "reddit", "hype"], default_rate=1.18),
    Voice(name="en-US-GuyNeural", label="Guy - classic newsreader",
          gender="male", language="en",
          tags=["narrator", "documentary", "ai_brainrot"], default_rate=1.1),
    Voice(name="en-US-ChristopherNeural", label="Christopher - deep, authoritative",
          gender="male", language="en",
          tags=["narrator", "documentary", "hype"], default_rate=1.08),
    Voice(name="en-US-EmmaNeural", label="Emma - bright, expressive",
          gender="female", language="en",
          tags=["storytime", "reddit", "hype"], default_rate=1.18),
    Voice(name="en-US-AvaNeural", label="Ava - friendly, natural",
          gender="female", language="en",
          tags=["storytime", "reddit", "calm"], default_rate=1.15),
    Voice(name="en-US-AnaNeural", label="Ana - young, high-energy",
          gender="female", language="en",
          tags=["hype", "kids", "chaotic"], default_rate=1.22),

    # --- French ----------------------------------------------------------
    Voice(name="fr-FR-HenriNeural", label="Henri - posé, narrateur",
          gender="male", language="fr",
          tags=["narrator", "documentary", "storytime"], default_rate=1.12),
    Voice(name="fr-FR-DeniseNeural", label="Denise - naturelle, vive",
          gender="female", language="fr",
          tags=["storytime", "reddit", "hype"], default_rate=1.15),
    Voice(name="fr-FR-EloiseNeural", label="Eloise - jeune, énergique",
          gender="female", language="fr",
          tags=["hype", "chaotic", "kids"], default_rate=1.2),
    Voice(name="fr-FR-RemyMultilingualNeural", label="Remy - chaud, expressif",
          gender="male", language="fr",
          tags=["hype", "reddit", "storytime", "ai_brainrot"], default_rate=1.15),

    # --- Arabic (MSA - rendered by lively Egyptian voices) --------------
    Voice(name="ar-EG-ShakirNeural", label="Shakir - راوي حيوي",
          gender="male", language="ar",
          tags=["narrator", "hype", "reddit", "ai_brainrot"], default_rate=1.1),
    Voice(name="ar-EG-SalmaNeural", label="Salma - واضحة، مؤثرة",
          gender="female", language="ar",
          tags=["storytime", "reddit", "hype"], default_rate=1.12),
    Voice(name="ar-SA-HamedNeural", label="Hamed - عميق، جدّي",
          gender="male", language="ar",
          tags=["narrator", "documentary", "calm"], default_rate=1.05),
    Voice(name="ar-SA-ZariyahNeural", label="Zariyah - هادئة، رصينة",
          gender="female", language="ar",
          tags=["documentary", "calm", "storytime"], default_rate=1.08),

    # --- Darija (Maghrebi voices reading Moroccan Darija text) --------
    # Microsoft's ar-MA voices lean MSA in prosody; the ar-DZ (Algerian) ones
    # often sound more colloquially Maghrebi on Darija text. Both are offered.
    Voice(name="ar-MA-JamalNeural", label="Jamal - مغربي، راجل",
          gender="male", language="ary",
          tags=["narrator", "hype", "reddit", "ai_brainrot", "storytime"],
          default_rate=1.05),
    Voice(name="ar-MA-MounaNeural", label="Mouna - مغربية، مرا",
          gender="female", language="ary",
          tags=["storytime", "reddit", "hype", "calm"], default_rate=1.06),
    Voice(name="ar-DZ-IsmaelNeural", label="Ismael - مغاربي، راجل (أقرب للدارجة)",
          gender="male", language="ary",
          tags=["narrator", "hype", "reddit", "storytime", "ai_brainrot"],
          default_rate=1.05),
    Voice(name="ar-DZ-AminaNeural", label="Amina - مغاربية، مرا (أقرب للدارجة)",
          gender="female", language="ary",
          tags=["storytime", "reddit", "hype", "calm"], default_rate=1.06),
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
