"""Keyless 'what's trending' sources — Google Trends RSS + Wikipedia most-read.

These give the Director a rotating pool of real, current topics. They come back
as ``kind="trend"`` raw phrases ("Taylor Swift", "quantum computing"); the
Director reshapes each into a format-appropriate premise with one cheap LLM call.
"""

from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from datetime import date, timedelta

from ..config import get_settings
from ..models import TrendSignal

# Wikimedia's API wants a descriptive UA with a contact; a bare token gets 403.
_UA = "brainrotter/0.1 (local self-hosted; https://github.com/john-titor22/brainrotter)"
_cache: dict[str, tuple[float, list[TrendSignal]]] = {}


def _cached(key: str):
    hit = _cache.get(key)
    ttl = get_settings().trends.web_cache_minutes * 60
    return hit[1] if hit and time.time() - hit[0] < ttl else None


def _store(key: str, val: list[TrendSignal]) -> list[TrendSignal]:
    if val:
        _cache[key] = (time.time(), val)
    return val


def google_trends(geo: str = "US", limit: int = 15) -> list[TrendSignal]:
    key = f"gt:{geo}"
    if (c := _cached(key)) is not None:
        return c
    import httpx

    try:
        r = httpx.get("https://trends.google.com/trending/rss",
                      params={"geo": geo}, headers={"User-Agent": _UA}, timeout=8)
        r.raise_for_status()
        root = ET.fromstring(r.text)
        ns = {"ht": "https://trends.google.com/trending/rss"}
        out: list[TrendSignal] = []
        for item in root.iter("item"):
            title = (item.findtext("title") or "").strip()
            if not title:
                continue
            traffic = (item.findtext("ht:approx_traffic", default="", namespaces=ns) or "").strip()
            news = item.findtext("ht:news_item/ht:news_item_title", default="", namespaces=ns) or ""
            out.append(TrendSignal(
                source="google_trends", kind="trend", title=title,
                body=(news or traffic)[:400], score=0.6,
                meta={"geo": geo, "traffic": traffic},
            ))
            if len(out) >= limit:
                break
        return _store(key, out)
    except Exception:
        return []


def wikipedia_hot(limit: int = 15) -> list[TrendSignal]:
    key = "wiki"
    if (c := _cached(key)) is not None:
        return c
    import httpx

    d = date.today() - timedelta(days=1)
    try:
        r = httpx.get(
            f"https://en.wikipedia.org/api/rest_v1/feed/featured/{d:%Y/%m/%d}",
            headers={"User-Agent": _UA}, timeout=8,
        )
        r.raise_for_status()
        import re

        arts = r.json().get("mostread", {}).get("articles", []) or []
        skip = re.compile(r"^(deaths in |list of |\d{4}\b)|"
                          r"\b(day|calendar|filmography|discography)$", re.I)
        out: list[TrendSignal] = []
        for a in arts:
            t = (a.get("titles", {}).get("normalized") or a.get("title", "")).replace("_", " ").strip()
            if not t or ":" in t or skip.search(t) or t in ("Main Page", "Special:Search"):
                continue
            out.append(TrendSignal(
                source="wikipedia", kind="trend", title=t,
                body=(a.get("extract") or "")[:500], score=0.55,
                meta={"views": a.get("views")},
            ))
            if len(out) >= limit:
                break
        return _store(key, out)
    except Exception:
        return []


def signals(limit: int = 20) -> list[TrendSignal]:
    if not get_settings().trends.use_web:
        return []
    geo = get_settings().trends.geo or "US"
    out = google_trends(geo) + wikipedia_hot()
    return out[:limit]
