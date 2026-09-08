"""Curated fallback topics, tagged by which format they fit.

Always available (no network). The Director uses these when live signals are
thin, and mixes them in for variety.
"""

from __future__ import annotations

import random

from ..models import TrendSignal

# (topic, format_hints, weight, kind)
_BANK: list[tuple[str, list[str], float, str]] = [
    ("petty revenge on a bad landlord", ["reddit_story"], 0.9, "topic"),
    ("malicious compliance at a corporate job", ["reddit_story"], 0.9, "topic"),
    ("wedding drama that split a family", ["reddit_story"], 0.85, "topic"),
    ("caught a roommate stealing food", ["reddit_story"], 0.8, "topic"),
    ("entitled customer vs minimum wage worker", ["reddit_story"], 0.85, "topic"),
    ("neighbor dispute over a fence", ["reddit_story"], 0.7, "topic"),
    ("group project betrayal in college", ["reddit_story"], 0.75, "topic"),
    ("a crocodile fused with a fighter jet", ["ai_brainrot"], 0.9, "topic"),
    ("a shark wearing three pairs of sneakers", ["ai_brainrot"], 0.85, "topic"),
    ("an espresso cup that gained sentience", ["ai_brainrot"], 0.8, "topic"),
    ("a cursed vending machine deity", ["ai_brainrot"], 0.8, "topic"),
    ("a pigeon that runs an underground bank", ["ai_brainrot"], 0.8, "topic"),
    ("a lighthouse that is secretly a giant", ["ai_brainrot"], 0.75, "topic"),
    ("the last Blockbuster on the moon", ["ai_brainrot", "reddit_story"], 0.7, "topic"),
]

# Public figures for the anime_figure format — recognizable, meme-friendly,
# safe to parody as a "public persona".
_FIGURES: list[str] = [
    "Barack Obama", "Elon Musk", "Keanu Reeves", "Gordon Ramsay", "The Rock",
    "Bernie Sanders", "Snoop Dogg", "Nikola Tesla", "Julius Caesar",
    "Genghis Khan", "Shaquille O'Neal", "Bill Nye", "Steve Jobs",
    "Abraham Lincoln", "David Attenborough", "Mr Beast", "Danny DeVito",
]

for _name in _FIGURES:
    _BANK.append((_name, ["anime_figure"], 0.85, "figure"))


def signals(limit: int = 10, seed: int | None = None) -> list[TrendSignal]:
    rng = random.Random(seed)
    picks = rng.sample(_BANK, k=min(limit, len(_BANK)))
    return [
        TrendSignal(
            source="topic_bank",
            kind=kind,
            title=topic,
            score=weight,
            format_hints=hints,
        )
        for topic, hints, weight, kind in picks
    ]
