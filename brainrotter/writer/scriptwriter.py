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
        system = _localise(fmt.writer_system_prompt(), brief)
        user = fmt.writer_user_prompt(brief)
        if brief.series:
            system, user = _serialize(system, user, brief)
        raw = llm.complete_json(system, user, max_tokens=4000, language=brief.language)
        script = fmt.parse_script(raw)
        if get_settings().writer.coherence_pass:
            _coherence_pass(script, brief)
        if brief.language == "ary" and get_settings().language.darija_polish:
            _darija_polish(script)
    else:
        script = _stub_script(brief)
    if brief.series:
        _apply_series(script, brief)
    _trim_to_budget(script, brief)
    script.est_seconds = round(len(script.narration_text.split()) / 2.5, 1)
    return script


_COHERENCE_SYS = (
    "You are a script editor for short-form video. You get a story as a numbered "
    "list of spoken lines. Do a tight edit pass:\n"
    "- Each line must follow logically from the one before. Fix contradictions "
    "and non-sequiturs.\n"
    "- The last line must pay off the first line (answer its question / land its "
    "twist). If it doesn't, rewrite the last line so it does.\n"
    "- Kill filler lines ('things escalated', 'I couldn't believe it') — replace "
    "with a concrete beat or cut them.\n"
    "- Keep the same voice, the same rough length, and the same number of lines "
    "(±1). Don't blandify it — keep the specific weird details.\n"
    'Return JSON: {"lines": [str, ...]} — the edited lines in order. If the '
    "story is already tight, return the lines unchanged."
)


def _coherence_pass(script: Script, brief: Brief) -> None:
    """One fast pass to make the story hang together and pace better. Best
    effort — any failure or a bad shape leaves the original untouched."""
    lines = [b.narration.strip() for b in script.beats if b.narration.strip()]
    if len(lines) < 3:
        return
    try:
        data = llm.complete_json(
            _COHERENCE_SYS,
            "Story:\n" + "\n".join(f"{i + 1}. {l}" for i, l in enumerate(lines)),
            fast=True, max_tokens=1400, language=brief.language,
            temperature=0.4,
        )
        out = data.get("lines") or []
        out = [str(x).strip() for x in out if str(x).strip()]
        if not (isinstance(out, list) and abs(len(out) - len(lines)) <= 1 and len(out) >= 3):
            return
        # rebuild beats: reuse the old beats' visual/sfx where we can
        new_beats = []
        for i, text in enumerate(out):
            src = script.beats[min(i, len(script.beats) - 1)]
            new_beats.append(ScriptBeat(
                narration=text, on_screen=src.on_screen if i < len(script.beats) else None,
                visual=src.visual if i < len(script.beats) else "",
                sfx=src.sfx if i < len(script.beats) else None,
            ))
        script.beats = new_beats
    except Exception:
        return


_SERIES_DIRECTIVE = (
    "SERIES MODE — this script is ONE part of a multi-part story. Continuity is "
    "the priority:\n"
    "- Keep the EXACT same characters, names, place and tone as 'The story so far'.\n"
    "- Do NOT re-tell earlier parts. Open with at most a one-line catch-up, then "
    "move the story forward.\n"
    "- Cover the events in 'This part must cover' and nothing past them.\n"
    "- If this is NOT the final part: end on a hard cliffhanger — an unanswered "
    "question or a reveal — that makes them need the next part.\n"
    "- If this IS the final part: resolve every open thread and land a real "
    "ending. No cliffhanger, no 'to be continued', no 'follow for part'.\n"
    "- The first beat must be the given hook (tighten wording if needed)."
)


def _serialize(system: str, user: str, brief: Brief) -> tuple[str, str]:
    s = brief.series
    ctx = [
        f"=== SERIES: \"{s.title}\" — Part {s.part} of {s.part_count} ===",
        f"Overall premise: {s.premise}",
    ]
    if s.story_so_far:
        ctx.append("The story so far:\n" + s.story_so_far)
    else:
        ctx.append("This is Part 1 — establish the character and the situation fast, "
                   "then hit the first turn.")
    ctx.append("This part must cover: " + s.part_goal)
    ctx.append("THIS IS THE FINAL PART — end the whole story." if s.is_finale
               else "This is NOT the final part — it must end on a cliffhanger.")
    if brief.hook:
        ctx.append(f"Required opening hook: {brief.hook}")
    return system + "\n\n" + _SERIES_DIRECTIVE, "\n".join(ctx) + "\n\n" + user


def _apply_series(script: Script, brief: Brief) -> None:
    s = brief.series
    script.title = f"{s.title} — Part {s.part}"
    if s.is_finale:
        if script.cta and "part" in script.cta.lower():
            script.cta = None
    else:
        script.cta = script.cta or f"Part {s.part + 1} tomorrow."


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
