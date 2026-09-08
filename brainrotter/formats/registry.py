"""Format registry. Add a format = import it here and add to _MODULES."""

from __future__ import annotations

from types import ModuleType

from . import ai_brainrot, anime_figure, reddit_story

_MODULES: list[ModuleType] = [reddit_story, ai_brainrot, anime_figure]
_BY_ID: dict[str, ModuleType] = {m.ID: m for m in _MODULES}


def all_ids() -> list[str]:
    return list(_BY_ID)


def all_formats() -> list[ModuleType]:
    return list(_MODULES)


def get(format_id: str) -> ModuleType:
    if format_id not in _BY_ID:
        raise KeyError(f"unknown format '{format_id}'. Known: {', '.join(_BY_ID)}")
    return _BY_ID[format_id]


def describe() -> list[dict]:
    return [
        {"id": m.ID, "name": m.NAME, "description": m.DESCRIPTION,
         "signal_kinds": m.SIGNAL_KINDS}
        for m in _MODULES
    ]
