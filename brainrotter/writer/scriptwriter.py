"""Brief -> Script.

Uses Claude when ANTHROPIC_API_KEY is set; otherwise a deterministic offline
stub so the rest of the pipeline stays testable.
"""

from __future__ import annotations

import json
import textwrap

from ..config import get_settings
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
        "بالدارجة المغربية بحال ما كيهضرو بيها الناس فالزنقة — ماشي بالعربية "
        "الفصحى، ماشي ترجمة حرفية.\n"
        "استعمل كلمات الدارجة بحال: كاين، ماكاينش، ديال، ديالي، ديالنا، بزاف، "
        "دابا، غادي، واخا، بحال، شوية، مزيان، خويا، راه، فين، علاش، كيفاش، بغيت، "
        "خصني، عندو، فـ، ولا، حيت، ماشي.\n"
        "بلا ما تستعمل كلمات الفصحى بحال: يوجد، هناك، الذي، جداً، الآن، سوف، "
        "مثل، أين، لماذا، كيف، أريد، لأن، ليس.\n"
        "مثال ديال الدارجة: «راه هاد الواحد ماشي بحال الناس. من صغرو كان كيبان "
        "ليه بلي غادي يولي شي حاجة كبيرة. ولكن الطريق ماكانش ساهل بزاف.»\n"
        "الحروف عربية. خلي مفاتيح JSON بالإنجليزية."
    ),
}


def _localise(system: str, brief: Brief) -> str:
    directive = _LANGUAGE_DIRECTIVE.get(brief.language)
    if not directive:
        return system
    if brief.language in ("ar", "ary"):
        # Aya reliably overshoots length in Arabic; give it a hard word budget
        # (~2 words/sec of speech) so the TTS lands near target_seconds.
        cap = max(24, int(brief.target_seconds * 2))
        directive += (
            f"\nمهم: المجموع ديال الكلمات فكل beats ما يفوتش {cap} كلمة "
            f"(الفيديو خاصو يكون قريب {brief.target_seconds} ثانية)."
        )
    return f"{system}\n\n{directive}"


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
        if brief.language == "ary" and get_settings().language.darija_polish:
            _darija_polish(script)
    else:
        script = _stub_script(brief)
    script.est_seconds = round(len(script.narration_text.split()) / 2.5, 1)
    return script


_DARIJA_POLISH_SYS = (
    "نتا مترجم للدارجة المغربية. كنعطيوك جمل مكتوبة بخليط ديال الفصحى والدارجة، "
    "وخصك ترجعهم دارجة مغربية صافية بحال ما كيهضرو الناس فالزنقة. بدّل كل كلمة "
    "فصحى بالمعادلة ديالها فالدارجة (يوجد→كاين، الذي→اللي، جداً→بزاف، الآن→دابا، "
    "سوف→غادي، مثل→بحال، لأن→حيت، ليس→ماشي، عندما→منين، يمكن→يمكن ليه). خلي "
    "المعنى كيفما هو وخلي الطول قريب. ما تزيدش شي حاجة."
)


def _darija_polish(script: Script) -> None:
    """Second pass — local models slip back into MSA on hype/epic register, so
    rewrite every line into pure street Darija. Best-effort: on any failure or
    a length mismatch, the original text is kept."""
    lines = [script.title] + [b.narration for b in script.beats]
    try:
        data = llm.complete_json(
            _DARIJA_POLISH_SYS,
            "رجّع هاد الجمل دارجة صافية. جاوب بـ JSON: "
            '{"lines": [...]} بنفس العدد وبنفس الترتيب.\n\n'
            + json.dumps(lines, ensure_ascii=False),
            fast=True, max_tokens=2000, language="ary", temperature=0.3,
        )
        out = data.get("lines") or data.get("جمل") or []
        if isinstance(out, list) and len(out) == len(lines):
            cleaned = [str(x).strip() for x in out]
            if all(cleaned):
                script.title = cleaned[0]
                for beat, txt in zip(script.beats, cleaned[1:]):
                    beat.narration = txt
    except Exception:
        pass


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
