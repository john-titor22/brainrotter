"""Reddit trend source — hot posts from configured subreddits.

Optional: needs REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET. Returns [] silently if
unconfigured or unreachable so the Director can fall back to the topic bank.
"""

from __future__ import annotations

from ..config import get_settings
from ..models import TrendSignal


def available() -> bool:
    s = get_settings()
    return bool(s.reddit_client_id and s.reddit_client_secret)


def signals(limit: int | None = None) -> list[TrendSignal]:
    if not available():
        return []
    s = get_settings()
    limit = limit or s.trends.reddit_limit
    try:
        import praw

        reddit = praw.Reddit(
            client_id=s.reddit_client_id,
            client_secret=s.reddit_client_secret,
            user_agent=s.reddit_user_agent,
            check_for_async=False,
        )
        out: list[TrendSignal] = []
        per_sub = max(1, limit // max(1, len(s.director.subreddits)))
        for sub in s.director.subreddits:
            for post in reddit.subreddit(sub).top(
                time_filter=s.trends.reddit_time_filter, limit=per_sub
            ):
                if post.stickied or not post.selftext:
                    continue
                out.append(
                    TrendSignal(
                        source="reddit",
                        kind="story",
                        title=post.title,
                        body=post.selftext[:6000],
                        url=f"https://reddit.com{post.permalink}",
                        score=min(1.0, post.score / 20000),
                        format_hints=["reddit_story"],
                        meta={"subreddit": sub, "ups": post.score,
                              "comments": post.num_comments},
                    )
                )
        return out
    except Exception:
        return []
