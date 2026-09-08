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
        cap = max(20, int(brief.target_seconds * 1.6))
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
    _trim_to_budget(script, brief)
    script.est_seconds = round(len(script.narration_text.split()) / 2.5, 1)
    return script


def _trim_to_budget(script: Script, brief: Brief) -> None:
    """Models (Aya especially, in Arabic) overshoot the target length, which
    balloons the clip count and makes the render flakier. Drop trailing beats
    once we're well past budget — keep at least 3."""
    # words/sec varies by language; Arabic script "words" are denser
    wps = 2.0 if brief.language in ("ar", "ary") else 2.6
    budget_words = int(brief.target_seconds * wps * 1.25)   # 25% slack
    kept: list = []
    total = 0
    for beat in script.beats:
        n = len(beat.narration.split())
        if kept and total + n > budget_words and len(kept) >= 3:
            break
        kept.append(beat)
        total += n
    script.beats = kept


_DARIJA_POLISH_SYS = (
    "نتا مغربي وخدمتك هي تحويل النص من الفصحى للدارجة المغربية الصافية — الدارجة "
    "اللي كيهضرو بيها الناس فالزنقة، ماشي فالتلفزة.\n"
    "بدّل كل كلمة فصحى بلي كاينة فالدارجة: يوجد/هناك→كاين، لا يوجد→ماكاينش، "
    "الذي/التي→اللي، هذا→هاد، جداً→بزاف، الآن→دابا، سوف/سـ→غادي، مثل→بحال، "
    "لأن→حيت/على حقاش، ليس→ماشي، عندما→منين، لماذا→علاش، كيف→كيفاش، أين→فين، "
    "أريد→بغيت، يصبح→يولي، يستطيع→يقدر، بدأ→بدا، وجد→لقا، رأى→شاف، ذهب→مشا، "
    "قال→قال، يقول→كيقول، لكن→ولكن/بصح، فقط→غير، الأمة/البلاد→البلاد، "
    "المواجهة→المواجهة (خليها)، عزيمة→عزيمة (خليها).\n"
    "الأفعال المضارع خصهم يبداو بـ (كـ): يمشي→كيمشي، يحارب→كيحارب.\n"
    "مثال:\n"
    "فصحى: «عندما كان صغيراً، كان الناس يقولون إن هذا مستحيل، لكنه لم يستسلم "
    "وأصبح بطلاً».\n"
    "دارجة: «منين كان صغير، كانو الناس كيقولو بلي هاد الشي مستحيل، بصح ما "
    "استسلماش وولا بطل».\n"
    "خلي المعنى والطول كيفما هما. ما تزيد ولا تنقص شي معلومة."
)

# fus-ha giveaways — if the polish still leaves these, run it again
_MSA_MARKERS = ("يوجد", "هناك", "الذي", "التي", " جداً", " جدا", " سوف ", "عندما",
                "لماذا", " كيف ", " ليس ", "الآن", " إنّ", " أنّ", "يصبح", "أصبح",
                "لم يست", "لكنه", "لكنها")


def _msa_count(text: str) -> int:
    return sum(text.count(m) for m in _MSA_MARKERS)


def _darija_polish(script: Script, rounds: int = 2) -> None:
    """Local models slip back into MSA on hype/epic register — rewrite every
    line into pure street Darija, repeating while fus-ha markers remain.
    Best-effort: on any failure or a length mismatch, the last good text stays."""
    for _ in range(max(1, rounds)):
        lines = [script.title] + [b.narration for b in script.beats]
        if _msa_count(" ".join(lines)) <= 2:
            return
        try:
            data = llm.complete_json(
                _DARIJA_POLISH_SYS,
                "رجّع هاد الجمل دارجة مغربية صافية. جاوب غير بـ JSON: "
                '{"lines": [...]} بنفس العدد وبنفس الترتيب.\n\n'
                + json.dumps(lines, ensure_ascii=False),
                fast=True, max_tokens=2000, language="ary", temperature=0.2,
            )
            out = data.get("lines") or data.get("جمل") or []
            if not (isinstance(out, list) and len(out) == len(lines)):
                return
            cleaned = [str(x).strip() for x in out]
            if not all(cleaned):
                return
            # only accept the rewrite if it actually reduced the fus-ha
            if _msa_count(" ".join(cleaned)) < _msa_count(" ".join(lines)):
                script.title = cleaned[0]
                for beat, txt in zip(script.beats, cleaned[1:]):
                    beat.narration = txt
            else:
                return
        except Exception:
            return


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
