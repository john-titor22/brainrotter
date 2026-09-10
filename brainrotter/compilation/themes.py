"""Compilation themes — what the Director picks between, and where each one's
clips come from.

Six built-in themes the Director rotates through. ANY other topic the user types
("jumpscares", "car crashes", "gym fails", "kids being kids") becomes an *ad-hoc*
theme: a small LLM call (+ Reddit's subreddit search when the API is configured)
turns the phrase into subreddits + YouTube queries + a music mood, cached under
``assets/cache/compilation/@<slug>/``.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

log = logging.getLogger("brainrotter.compilation")


@dataclass(frozen=True)
class Theme:
    key: str
    label: str                       # the Brief topic ("funny videos")
    subreddits: tuple[str, ...]
    youtube: tuple[str, ...]
    music_mood: str                  # mood for the quiet bed
    keywords: tuple[str, ...] = field(default_factory=tuple)  # for trend matching


_THEMES = [
    Theme(
        key="funny",
        label="funny videos",
        subreddits=(
            "funny", "Unexpected", "instantregret", "facepalm",
            "therewasanattempt", "WinStupidPrizes", "holdmyfeedingtube",
            "unexpectedlywholesome", "contagiouslaughter", "unexpected",
            "PublicFreakout", "funnyvideos", "AbruptChaos", "IdiotsInCars",
            "wellthatsucks", "kidsarefuckingstupid", "PeopleFuckingDying",
            "instant_regret", "youseeingthisshit", "nonononoyes",
        ),
        youtube=(
            "funny fails short", "try not to laugh clips short",
            "funny moments compilation clip", "caught on camera funny short",
        ),
        music_mood="funny",
        keywords=("funny", "fail", "laugh", "hilarious", "comedy", "joke", "prank"),
    ),
    Theme(
        key="animals",
        label="funny animal clips",
        subreddits=(
            "AnimalsBeingDerps", "AnimalsBeingJerks", "zoomies",
            "AnimalsBeingBros", "holdmycatnip", "catsstandingup",
            "WhatsWrongWithYourDog", "AnimalsBeingConfused", "snek",
            "IllegallySmolCats", "Chonkers", "dogswithjobs", "rarepuppers",
            "StartledCats", "catsareliquid", "tuckedinkitties",
            "AnimalsBeingGeniuses", "Zoomies", "dogswearinghats",
        ),
        youtube=(
            "funny animals short", "funny cats and dogs compilation clip",
            "funny pets short", "animals being derps short",
        ),
        music_mood="funny",
        keywords=("animal", "cat", "dog", "pet", "puppy", "kitten", "derp"),
    ),
    Theme(
        key="satisfying",
        label="oddly satisfying clips",
        subreddits=(
            "oddlysatisfying", "PerfectFit", "powerwashingporn",
            "ASMR", "SweatyPalms", "BeAmazed", "ArtisanVideos",
            "Damnthatsinteresting", "woodworking", "Kayosbrickworks",
            "chefknives", "Baking", "restoration", "hydraulicpresschannel",
            "SatisfyingChristmas", "toptalent",
        ),
        youtube=(
            "oddly satisfying short", "most satisfying video compilation clip",
            "satisfying asmr short", "restoration satisfying short",
        ),
        music_mood="chill",
        keywords=("satisfying", "asmr", "smooth", "clean", "perfect", "precise"),
    ),
    Theme(
        key="wholesome",
        label="wholesome moments",
        subreddits=(
            "MadeMeSmile", "HumansBeingBros", "aww", "Eyebleach",
            "wholesomememes", "rarepuppers", "AnimalsBeingBros",
            "HappyCryingDads", "wholesomegifs", "BeforeNAfterAdoption",
            "AnimalRescue", "toohumancat", "IllegallySmolCats",
            "petthedamndog", "SoulmateAnimals", "aFrickinLegend",
        ),
        youtube=(
            "wholesome moments short", "faith in humanity restored short",
            "heartwarming clips compilation", "kind strangers short",
        ),
        music_mood="nostalgic",
        keywords=("wholesome", "heartwarming", "kind", "smile", "cute", "sweet"),
    ),
    Theme(
        key="fails",
        label="epic fails",
        subreddits=(
            "Whatcouldgowrong", "instantregret", "WinStupidPrizes",
            "holdmyredbull", "AbruptChaos", "maybemaybemaybe",
            "therewasanattempt", "IdiotsInCars", "Wellthatsucks",
            "OSHA", "PraiseTheCameraMan", "instantkarma", "nevertellmetheodds",
            "kidsarefuckingstupid", "FailArmy", "holdmyjuicebox", "yesyesyesyesno",
        ),
        youtube=(
            "epic fails compilation short", "fail army clips short",
            "what could go wrong short", "instant regret short",
        ),
        music_mood="funny",
        keywords=("fail", "wrong", "chaos", "disaster", "regret", "crash", "wipeout"),
    ),
    Theme(
        key="amazing",
        label="next level moments",
        subreddits=(
            "nextfuckinglevel", "BeAmazed", "toptalent", "holdmyjuicebox",
            "sports", "Damnthatsinteresting", "interestingasfuck",
            "PraiseTheCameraMan", "sportsarefun", "flexin", "nevertellmetheodds",
            "gifsthatkeepongiving", "humansbeingbadass", "MadeMeSmile",
            "BeAmazed", "chemicalreactiongifs", "educationalgifs",
        ),
        youtube=(
            "next level moments short", "insane talent compilation clip",
            "people are awesome short", "unbelievable moments caught on camera short",
        ),
        music_mood="hype",
        keywords=("amazing", "insane", "talent", "skill", "incredible", "pro", "clutch"),
    ),
]

THEMES: dict[str, Theme] = {t.key: t for t in _THEMES}


def theme_keys() -> list[str]:
    return list(THEMES)


def match_theme(text: str | None) -> str | None:
    """Match a topic to a BUILT-IN theme ONLY on a near-exact match (the whole
    topic is the key, the label, or the label without its trailing noun). Any
    more specific phrase ('parkour fails', 'funny cat videos', 'jumpscare')
    returns None so the caller sources for that exact topic instead."""
    if not text:
        return None
    low = re.sub(r"\s+", " ", text.strip().lower())
    if low in THEMES:
        return low
    for t in _THEMES:
        label = t.label.lower()
        short = re.sub(r"\s*(videos|clips|moments)$", "", label).strip()
        if low in (label, short, t.key.lower()):
            return t.key
    return None


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:40] or "topic"


# --- ad-hoc themes -------------------------------------------------------

def _adhoc_cache_path():
    from ..config import get_settings

    return get_settings().compilation_cache_path / "_adhoc_themes.json"


def _load_adhoc() -> dict:
    try:
        return json.loads(_adhoc_cache_path().read_text("utf-8"))
    except Exception:
        return {}


def _save_adhoc(data: dict) -> None:
    try:
        _adhoc_cache_path().write_text(json.dumps(data, indent=1), encoding="utf-8")
    except Exception:
        pass


# big general "something happened on camera" video subs — a thin-pool floor for
# ad-hoc topics, scored LOW so topic-specific subs win when they have material.
GENERIC_SUBS = ("Unexpected", "nonononoyes", "maybemaybemaybe", "holdmyredbull",
                "ThatsInsane", "sweatypalms", "BeAmazed", "interestingasfuck")
_GENERIC_SUBS = GENERIC_SUBS
_GENERIC_LC = frozenset(s.lower() for s in GENERIC_SUBS)

_SPEC_SYS = (
    "You configure a short-form VIDEO compilation. Given a TOPIC, reply with "
    "ONLY JSON: {\"label\": str, \"subreddits\": [str,...], \"youtube\": "
    "[str,...], \"music_mood\": str}.\n"
    "- label: the topic as a short noun phrase (e.g. 'jumpscare clips')\n"
    "- subreddits: 6-8 REAL subreddit names (no 'r/') that are full of SHORT "
    "VIDEO CLIPS of this topic. Real footage, not text stories, not memes, not "
    "screenshots. If unsure a sub exists, don't include it.\n"
    "- youtube: 3 short natural search phrases (2-5 words) — what a person types "
    "to find real clips. NO the word 'compilation'. e.g. 'scary moments caught "
    "on camera', 'creepy security footage'.\n"
    "- music_mood: one of hype tense eerie epic chill funny sad dramatic "
    "nostalgic phonk dreamy quirky\n"
    "Topic 'car crashes' -> {\"label\":\"car crash clips\",\"subreddits\":"
    "[\"IdiotsInCars\",\"CarCrash\",\"Roadcam\",\"dashcamgifs\",\"roadcam\","
    "\"CarCrashes\"],\"youtube\":[\"dashcam car crash\",\"road rage caught on "
    "camera\",\"car accident caught on dashcam\"],\"music_mood\":\"tense\"}\n"
    "Topic 'scary' -> {\"label\":\"scary moments\",\"subreddits\":"
    "[\"Damnthatsscary\",\"scaredshitless\",\"creepy\",\"nonononoyes\","
    "\"ThatsInsane\",\"sweatypalms\"],\"youtube\":[\"scary moments caught on "
    "camera\",\"creepy security camera footage\",\"real ghost caught on "
    "camera\"],\"music_mood\":\"eerie\"}"
)


def _llm_spec(topic: str) -> dict | None:
    from ..writer import llm

    if not llm.available():
        return None
    try:
        d = llm.complete_json(_SPEC_SYS, f"Topic: {topic}", fast=True, max_tokens=300)
    except Exception:
        return None
    subs = [str(x).lstrip("r/").strip() for x in (d.get("subreddits") or [])
            if isinstance(x, str) and re.fullmatch(r"[A-Za-z0-9_]{2,24}", str(x).lstrip("r/").strip())]
    yt = [str(x).strip() for x in (d.get("youtube") or []) if isinstance(x, str) and x.strip()]
    if not subs and not yt:
        return None
    return {
        "label": str(d.get("label") or topic).strip()[:60],
        "subreddits": subs[:8],
        "youtube": yt[:4],
        "music_mood": str(d.get("music_mood") or "hype").strip().lower(),
    }


def _reddit_search_subs(topic: str, limit: int = 8) -> list[str]:
    """Real subreddits matching the topic, via the Reddit API (if configured)."""
    from ..config import get_settings

    s = get_settings()
    if not (s.reddit_client_id and s.reddit_client_secret):
        return []
    try:
        import praw

        r = praw.Reddit(client_id=s.reddit_client_id,
                        client_secret=s.reddit_client_secret,
                        user_agent=s.reddit_user_agent or "brainrotter/0.1",
                        check_for_async=False)
        r.read_only = True
        out = []
        for sr in r.subreddits.search(topic, limit=limit):
            name = getattr(sr, "display_name", "")
            over18 = getattr(sr, "over18", False)
            subs = getattr(sr, "subscribers", 0) or 0
            if name and not over18 and subs > 5000:
                out.append(name)
        return out
    except Exception as exc:  # noqa: BLE001
        log.warning("reddit subreddit search failed for %r: %s", topic, exc)
        return []


def build_adhoc(topic: str) -> Theme:
    """A Theme for an arbitrary user topic. Cached so it's only built once."""
    slug = slugify(topic)
    key = "@" + slug
    cache = _load_adhoc()
    if key in cache:
        c = cache[key]
        return Theme(key=key, label=c["label"], subreddits=tuple(c["subreddits"]),
                     youtube=tuple(c["youtube"]), music_mood=c["music_mood"],
                     keywords=tuple(c.get("keywords", ())))

    spec = _llm_spec(topic) or {}
    # topic-specific subs first (Reddit search > LLM guess); a couple of big
    # general video subs appended last as a thin-pool floor. `sources` scores
    # the generic ones lower so they only fill in when the topic subs are dry.
    topic_subs = list(dict.fromkeys(
        _reddit_search_subs(topic) + spec.get("subreddits", [])))
    subs = list(dict.fromkeys(topic_subs + list(_GENERIC_SUBS[:3])))
    yt = spec.get("youtube") or [
        f"{topic} caught on camera", f"{topic} clips", f"crazy {topic} moments"]
    label = spec.get("label") or f"{topic} clips"
    mood = spec.get("music_mood") or "hype"

    theme = Theme(key=key, label=label, subreddits=tuple(subs[:12]),
                  youtube=tuple(yt[:4]), music_mood=mood, keywords=tuple())
    cache[key] = {"label": label, "subreddits": list(theme.subreddits),
                  "youtube": list(theme.youtube), "music_mood": mood,
                  "topic": topic}
    _save_adhoc(cache)
    log.info("compilation: built ad-hoc theme %s -> r/%s", key,
             ", r/".join(theme.subreddits[:4]))
    return theme


def get(key: str) -> Theme:
    """Look up a theme by key — built-in or ad-hoc (``@slug``)."""
    if key in THEMES:
        return THEMES[key]
    c = _load_adhoc().get(key)
    if c:
        return Theme(key=key, label=c["label"], subreddits=tuple(c["subreddits"]),
                     youtube=tuple(c["youtube"]), music_mood=c["music_mood"])
    # unknown ad-hoc key with no cache entry — rebuild from the slug
    return build_adhoc(key.lstrip("@").replace("-", " "))


def resolve(topic: str | None) -> Theme:
    """Topic string -> the Theme to source for. Built-in when it clearly matches
    one, an ad-hoc theme otherwise. Falls back to 'funny' for an empty topic."""
    m = match_theme(topic)
    if m:
        return THEMES[m]
    if topic and topic.strip():
        return build_adhoc(topic.strip())
    return THEMES["funny"]
