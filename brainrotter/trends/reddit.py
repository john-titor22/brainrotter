"""Reddit trend source — real stories from the story subreddits.

Two paths, in order:
  1. PRAW, if REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET are set (best).
  2. Reddit's public ``.json`` endpoints — no keys, just a User-Agent.
Returns [] silently on any failure so the Director falls back to the topic bank.
"""

from __future__ import annotations

import time

from ..config import get_settings
from ..models import TrendSignal

_cache: dict[str, tuple[float, list[TrendSignal]]] = {}


def available() -> bool:
    s = get_settings()
    return bool(s.reddit_client_id and s.reddit_client_secret)


def _mk(sub: str, d: dict) -> TrendSignal | None:
    if d.get("stickied") or d.get("over_18") or not (d.get("selftext") or "").strip():
        return None
    body = d["selftext"]
    if len(body) < 200:                      # skip one-liners / link posts
        return None
    return TrendSignal(
        source="reddit", kind="story", title=d.get("title", "").strip(),
        body=body[:6000], url="https://reddit.com" + (d.get("permalink") or ""),
        score=min(1.0, (d.get("score") or 0) / 20000),
        format_hints=["reddit_story"],
        meta={"subreddit": sub, "ups": d.get("score"), "comments": d.get("num_comments")},
    )


def _praw_signals(limit: int) -> list[TrendSignal]:
    s = get_settings()
    try:
        import praw

        reddit = praw.Reddit(
            client_id=s.reddit_client_id, client_secret=s.reddit_client_secret,
            user_agent=s.reddit_user_agent, check_for_async=False,
        )
        out: list[TrendSignal] = []
        per = max(2, limit // max(1, len(s.director.subreddits)))
        for sub in s.director.subreddits:
            for post in reddit.subreddit(sub).top(
                time_filter=s.trends.reddit_time_filter, limit=per
            ):
                sig = _mk(sub, {
                    "stickied": post.stickied, "over_18": post.over_18,
                    "selftext": post.selftext, "title": post.title,
                    "permalink": post.permalink, "score": post.score,
                    "num_comments": post.num_comments,
                })
                if sig:
                    out.append(sig)
        return out
    except Exception:
        return []


_ATOM = "{http://www.w3.org/2005/Atom}"


def _public_signals(limit: int) -> list[TrendSignal]:
    """Reddit's public JSON is now walled off, but the ``.rss`` feeds still work
    keyless. Parse those."""
    import html
    import re
    import xml.etree.ElementTree as ET

    import httpx

    s = get_settings()
    key = f"reddit:{s.trends.reddit_time_filter}"
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < s.trends.web_cache_minutes * 60:
        return hit[1]

    per = max(4, limit // max(1, len(s.director.subreddits)))
    headers = {"User-Agent": s.reddit_user_agent or "brainrotter/0.1"}
    out: list[TrendSignal] = []
    with httpx.Client(headers=headers, timeout=8, follow_redirects=True) as c:
        for sub in s.director.subreddits:
            try:
                r = c.get(f"https://www.reddit.com/r/{sub}/top/.rss",
                          params={"t": s.trends.reddit_time_filter, "limit": per})
                r.raise_for_status()
                root = ET.fromstring(r.text)
                for e in root.findall(f"{_ATOM}entry")[:per]:
                    title = (e.findtext(f"{_ATOM}title") or "").strip()
                    link_el = e.find(f"{_ATOM}link")
                    link = link_el.get("href") if link_el is not None else None
                    body = html.unescape(re.sub(r"<[^>]+>", " ",
                                                e.findtext(f"{_ATOM}content") or ""))
                    body = re.sub(r"\s+", " ", body).strip()
                    if title and len(body) > 200 and "submitted by" not in title.lower():
                        out.append(TrendSignal(
                            source="reddit", kind="story", title=title,
                            body=body[:6000], url=link, score=0.5,
                            format_hints=["reddit_story"], meta={"subreddit": sub},
                        ))
            except Exception:
                continue
            time.sleep(0.3)
    if out:
        _cache[key] = (time.time(), out)
    return out


def signals(limit: int | None = None) -> list[TrendSignal]:
    s = get_settings()
    limit = limit or s.trends.reddit_limit
    if available():
        praw_out = _praw_signals(limit)
        if praw_out:
            return praw_out
    if s.trends.use_web:
        return _public_signals(limit)
    return []
