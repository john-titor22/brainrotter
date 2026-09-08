"""Brief -> Script.

Uses Claude when ANTHROPIC_API_KEY is set; otherwise a deterministic offline
stub so the rest of the pipeline stays testable.
"""

from __future__ import annotations

import textwrap

from ..formats import registry
from ..models import Brief, Script, ScriptBeat
from . import llm

# Appended to the format's system prompt when the Director picks a non-English
# language. Only the spoken text (narration / title / on_screen) is localised —
# the JSON keys and any English structural rules stay as-is.
_LANGUAGE_DIRECTIVE = {
    "fr": (
        "LANGUE : écris TOUT le texte parlé (narration, titre, textes à l'écran) "
        "en FRANÇAIS naturel et oral — le registre parlé d'un créateur, pas un "
        "français soutenu ni une traduction rigide. Garde les clés JSON en "
        "anglais. Les hashtags peuvent rester en anglais."
    ),
    "ar": (
        "اللغة: اكتب كل النص المنطوق (السرد، العنوان، النصوص على الشاشة) "
        "بالعربية الفصحى الحديثة، بأسلوب شبابي حيوي مناسب لمقاطع السوشيال ميديا "
        "القصيرة، وليس أسلوباً أكاديمياً جامداً. أبقِ مفاتيح JSON بالإنجليزية."
    ),
    "ary": (
        "اللغة: كتب گاع النص المنطوق (السرد، العنوان، النصوص فالشاشة) "
        "بالـدارجة المغربية بحال ما كيهضرو بيها الناس فالواقع — ماشي بالعربية "
        "الفصحى، ماشي ترجمة حرفية. خليها طبيعية، شبابية، وفيها روح. "
        "استعمل الحروف العربية. خلي مفاتيح JSON بالإنجليزية."
    ),
}


def _localise(system: str, brief: Brief) -> str:
    directive = _LANGUAGE_DIRECTIVE.get(brief.language)
    return f"{system}\n\n{directive}" if directive else system


def write(brief: Brief) -> Script:
    fmt = registry.get(brief.format_id)
    if llm.available():
        raw = llm.complete_json(
            _localise(fmt.writer_system_prompt(), brief),
            fmt.writer_user_prompt(brief),
            max_tokens=4000,
            language=brief.language,
        )
        script = fmt.parse_script(raw)
    else:
        script = _stub_script(brief)
    script.est_seconds = round(len(script.narration_text.split()) / 2.5, 1)
    return script


def _stub_script(brief: Brief) -> Script:
    """Offline placeholder — valid, renderable, obviously templated."""
    topic = brief.topic
    hook = brief.hook or f"You will not believe what happened with {topic}."
    body = textwrap.dedent(f"""
        It started like any normal day, but {topic} was about to change everything.
        At first nobody noticed the small warning signs.
        Then things escalated way faster than anyone expected.
        People kept telling me to let it go, but I could not.
        What happened next is the reason I am telling you this story.
        In the end, it worked out, but I still think about it.
    """).strip().split("\n")
    beats = [ScriptBeat(narration=hook, visual="gameplay", sfx="whoosh")]
    beats += [ScriptBeat(narration=line.strip(), visual="gameplay") for line in body]
    beats.append(ScriptBeat(narration="Follow for part two.", visual="gameplay"))
    return Script(
        title=f"[STUB] {topic}"[:70],
        beats=beats,
        hashtags=["fyp", "storytime", "brainrot"],
        cta="Follow for part two.",
    )
