"""Turn a Script into a storyboard — one Stable Diffusion prompt per shot.

Each shot's prompt is  <fixed subject look> + <what happens in THIS beat> +
<house style>.  The subject look is written once and reused so the thing stays
recognisably the same from shot to shot; the per-beat part is written by the
local LLM from that beat's narration so the picture actually matches the words.

The subject KIND (object / person / creature) comes from the format
(``SUBJECT_KIND``) and is the guardrail that stops the model drawing a person
for a story narrated *by* a stapler.
"""

from __future__ import annotations

from ..config import get_settings
from ..formats import registry
from ..models import Brief, Script
from ..writer import llm

_KIND_RULES = {
    "object": (
        "The subject is an INANIMATE OBJECT. Every shot is the object itself, in "
        "a setting. NEVER draw a person, a face, hands, or body parts. The object "
        "does not have eyes or a mouth — it is a normal object."
    ),
    "creature": (
        "The subject is an invented creature. Every shot features the creature. "
        "No real people."
    ),
    "person": (
        "The subject is a real public figure — depict them. Other people only if "
        "the narration calls for them."
    ),
}

_KIND_NEG = {
    "object": ", person, people, human, man, woman, face, portrait, hands, eyes, mouth",
    "creature": ", real person, photograph of a man, photograph of a woman",
    "person": "",
}

_LOOK_SYS = {
    "object": (
        "Name the OBJECT and describe its look in ONE short phrase for an image "
        "generator — max 14 words: material, colour, age, wear, distinctive "
        "detail. It is a plain object: no face, no eyes, no limbs, not a person. "
        "Example: 'a bent chrome stapler, chipped paint, one loose spring'."
    ),
    "creature": (
        "Describe the creature's look in ONE short phrase — max 14 words: body, "
        "colours, textures, notable features. No real people."
    ),
    "person": (
        "Describe the person's look in ONE short phrase — max 14 words: age, "
        "hair, build, signature clothing."
    ),
}

_STORYBOARD_SYS = (
    "You are a storyboard artist for a short vertical video. You get a fixed "
    "description of the SUBJECT and the lines of narration, in order. For EACH "
    "line write one image-generation prompt for that exact moment: what the "
    "subject is doing or what is happening to it/around it, where it is, the "
    "light, the mood. Concrete and filmable. 12-28 words. Keep the subject "
    "consistent with the fixed look every time. No lettering/text in the image, "
    "no camera-jargon.\n"
    "{kind_rule}\n"
    'Return JSON: {{"shots": ["prompt for line 1", ...]}} with exactly one '
    "entry per line, same order."
)


def _kind_for(brief: Brief) -> str:
    try:
        return getattr(registry.get(brief.format_id), "SUBJECT_KIND", "object")
    except Exception:
        return "object"


def negative_extra(brief: Brief) -> str:
    return _KIND_NEG.get(_kind_for(brief), "")


def subject_look(topic: str, kind: str = "object") -> str:
    topic = (topic or "").strip() or "an ordinary object"
    if llm.available():
        try:
            out = llm.complete_text(
                _LOOK_SYS.get(kind, _LOOK_SYS["object"]),
                f"Subject: {topic}", fast=True, max_tokens=48,
            )
            out = out.split("\n")[0].strip().strip('"\'`.,').strip()
            if len(out) > 140:
                out = out[:140].rsplit(" ", 1)[0]
            if len(out) > 8:
                return out
        except Exception:
            pass
    return topic


def _clean_visual(v: str, fallback: str) -> str:
    v = (v or "").strip().rstrip(".")
    low = v.lower()
    if not v or low in ("gameplay", "surreal creature", "the object, close up",
                        "the object, close up, shallow depth of field"):
        return fallback
    if len(v) > 120:
        v = v[:120].rsplit(" ", 1)[0]
    return v


def _n_shots(n_beats: int) -> int:
    return max(1, min(n_beats, get_settings().visuals.max_images))


def beat_groups(n_beats: int) -> list[tuple[int, int]]:
    """Map beats onto shots. One shot per beat until ``max_images``; beyond that,
    beats are bundled evenly so a long script still gets a full storyboard."""
    k = _n_shots(n_beats)
    out: list[tuple[int, int]] = []
    for i in range(k):
        start = (i * n_beats) // k
        end = ((i + 1) * n_beats) // k
        out.append((start, max(end, start + 1)))
    return out


_HERO_STYLE = (
    "3D Pixar-style character, big expressive cartoon eyes and a wide mouth, "
    "front view, looking at camera, centred, head and shoulders framing, plain "
    "soft-gradient studio background, soft key light, high detail"
)


def hero_prompt(brief: Brief) -> str:
    """One image of the subject anthropomorphised with a clear face — the still
    that gets lip-synced (SadTalker) into a talking object."""
    look = subject_look(brief.topic, _kind_for(brief))
    return f"{look}, given a friendly face, {_HERO_STYLE}"


def storyboard(brief: Brief, script: Script) -> tuple[list[str], list[float]]:
    """Return (prompts, weights) — one entry per shot. ``weights`` is the word
    count of the beats behind each shot, so the engine can hold each still for
    the time its narration takes."""
    kind = _kind_for(brief)
    look = subject_look(brief.topic, kind)
    beats = script.beats
    groups = beat_groups(len(beats))
    fallback_scene = f"{brief.topic}, establishing shot"
    suffix = get_settings().visuals.style_suffix

    llm_shots: list[str] = []
    if llm.available():
        try:
            lines = [b.narration.strip() for b in beats]
            data = llm.complete_json(
                _STORYBOARD_SYS.format(kind_rule=_KIND_RULES.get(kind, _KIND_RULES["object"])),
                f"Subject: {brief.topic}\nFixed look: {look}\n\nNarration lines:\n"
                + "\n".join(f"{i + 1}. {l}" for i, l in enumerate(lines)),
                max_tokens=1800,
            )
            got = data.get("shots") or data.get("prompts") or []
            if isinstance(got, list):
                llm_shots = [str(s).strip() for s in got]
        except Exception:
            llm_shots = []

    prompts: list[str] = []
    weights: list[float] = []
    head = look.split(",")[0].strip().lower()
    for (start, end) in groups:
        raw = llm_shots[start] if start < len(llm_shots) else ""
        if not raw:
            raw = _clean_visual(beats[start].visual, fallback_scene)
        scene = raw if head and head in raw.lower() else f"{look}. {raw}"
        prompts.append(f"{scene.rstrip('. ')}. {suffix}")
        weights.append(float(sum(len(beats[b].narration.split()) for b in range(start, end)) or 1))
    return prompts, weights
