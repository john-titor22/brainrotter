"""Where Brainrotter looks for background gameplay footage.

Search queries are preferred over pinned video IDs — "no copyright gameplay"
uploads churn, but the search term stays valid. A few known-good long-form
uploads are kept as fallbacks.

To "change the game" often, add categories here and widen each format's
`BG_CATEGORIES` (in `brainrotter/formats/*.py`). The Director mixes clips from a
format's category set per video and avoids the categories it used most recently.
"""

from __future__ import annotations

CATEGORY_QUERIES: dict[str, list[str]] = {
    "subway": [
        "subway surfers gameplay no copyright",
        "subway surfers no copyright vertical",
    ],
    "parkour": [
        "minecraft parkour gameplay no copyright",
        "minecraft parkour no copyright smoothest",
    ],
    "gta": [
        "gta 5 ramp gameplay no copyright",
        "gta 5 stunts no copyright background",
    ],
    "temple_run": [
        "temple run 2 gameplay no copyright",
        "temple run gameplay no copyright vertical",
    ],
    "trackmania": [
        "trackmania gameplay no copyright",
        "trackmania no copyright background video",
    ],
    "geometry_dash": [
        "geometry dash gameplay no copyright",
        "geometry dash no copyright background",
    ],
    "slope": [
        "slope game gameplay no copyright",
        "slope unblocked gameplay no copyright",
    ],
    "roblox_obby": [
        "roblox obby gameplay no copyright",
        "roblox tower of hell no copyright",
    ],
    "surf": [
        "csgo surf gameplay no copyright",
        "counter strike surf no copyright background",
    ],
    "satisfying": [
        "satisfying gameplay no copyright",
        "hydraulic press satisfying no copyright",
        "asmr soap cutting no copyright",
    ],
    "cluster_rush": [
        "cluster rush gameplay no copyright",
        "steep gameplay no copyright",
    ],
    "cooking": [
        "cooking simulator gameplay no copyright",
        "powerwash simulator gameplay no copyright",
    ],
}

# Fallbacks: full watch URLs known to be long, vertical-friendly, reuse-permitted.
CATEGORY_FALLBACK_URLS: dict[str, list[str]] = {
    "subway": [
        "https://www.youtube.com/watch?v=tLhJDpkLFSs",
        "https://www.youtube.com/watch?v=DAtYDg_sBzE",
    ],
    "parkour": [
        "https://www.youtube.com/watch?v=BXUA2FncVPI",
        "https://www.youtube.com/watch?v=eBExtYRpuqI",
    ],
}

DEFAULT_CATEGORY = "subway"


def categories() -> list[str]:
    return list(CATEGORY_QUERIES)


def queries_for(category: str) -> list[str]:
    return CATEGORY_QUERIES.get(category, CATEGORY_QUERIES[DEFAULT_CATEGORY])


def fallback_urls_for(category: str) -> list[str]:
    return CATEGORY_FALLBACK_URLS.get(category, [])


def figure_queries(name: str) -> list[str]:
    """Search terms for footage OF a public figure (anime_figure backgrounds)."""
    return [
        f"{name} speech highlights",
        f"{name} interview",
        f"{name} best moments compilation",
        f"{name} edit 4k",
    ]
