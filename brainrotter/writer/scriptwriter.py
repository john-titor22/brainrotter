"""Brief -> Script.

Uses Claude when ANTHROPIC_API_KEY is set; otherwise a deterministic offline
stub so the rest of the pipeline stays testable.
"""

from __future__ import annotations

import textwrap

from ..formats import registry
from ..models import Brief, Script, ScriptBeat
from . import llm


def write(brief: Brief) -> Script:
    fmt = registry.get(brief.format_id)
    if llm.available():
        raw = llm.complete_json(
            fmt.writer_system_prompt(),
            fmt.writer_user_prompt(brief),
            max_tokens=4000,
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
