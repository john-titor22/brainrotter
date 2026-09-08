"""Curated edge-tts voice catalog, tagged for the Director.

edge-tts exposes hundreds of voices; these are the ones that actually work for
brainrot narration. `rate` is the multiplier passed to the engine (1.0 = normal).
"""

from __future__ import annotations

from pydantic import BaseModel


class Voice(BaseModel):
    name: str            # edge-tts short name
    label: str
    gender: str
    tags: list[str]      # Director hints: "narrator", "hype", "calm", "storytime"
    default_rate: float = 1.15


CATALOG: list[Voice] = [
    Voice(name="en-US-AndrewNeural", label="Andrew — warm, conversational",
          gender="male", tags=["storytime", "narrator", "reddit"], default_rate=1.15),
    Voice(name="en-US-BrianNeural", label="Brian — casual, youthful",
          gender="male", tags=["storytime", "reddit", "hype"], default_rate=1.18),
    Voice(name="en-US-GuyNeural", label="Guy — classic newsreader",
          gender="male", tags=["narrator", "documentary", "ai_brainrot"], default_rate=1.1),
    Voice(name="en-US-ChristopherNeural", label="Christopher — deep, authoritative",
          gender="male", tags=["narrator", "documentary"], default_rate=1.08),
    Voice(name="en-US-EmmaNeural", label="Emma — bright, expressive",
          gender="female", tags=["storytime", "reddit", "hype"], default_rate=1.18),
    Voice(name="en-US-AvaNeural", label="Ava — friendly, natural",
          gender="female", tags=["storytime", "reddit"], default_rate=1.15),
    Voice(name="en-US-AnaNeural", label="Ana — young, high-energy",
          gender="female", tags=["hype", "kids", "chaotic"], default_rate=1.22),
]

_BY_NAME = {v.name: v for v in CATALOG}
DEFAULT = "en-US-AndrewNeural"


def get(name: str) -> Voice:
    return _BY_NAME.get(name, _BY_NAME[DEFAULT])


def for_tags(*tags: str) -> Voice:
    """First voice matching any tag; falls back to the default."""
    wanted = set(tags)
    for v in CATALOG:
        if wanted & set(v.tags):
            return v
    return _BY_NAME[DEFAULT]
