"""upload-post.com adapter — one call cross-posts a video as a Short to
TikTok / YouTube / Instagram / Facebook (and more).

Get a free key at https://app.upload-post.com (10 uploads/month, no card), set a
profile username, and connect your social accounts there once. Then:

  UPLOAD_POST_API_KEY=...      in .env
  UPLOAD_POST_USER=my-profile  in .env
"""

from __future__ import annotations

import logging
from pathlib import Path

import httpx

from ..config import get_settings

log = logging.getLogger("brainrotter.publish")

_URL = "https://api.upload-post.com/api/upload"


def configured() -> bool:
    s = get_settings()
    return bool(s.upload_post_api_key and s.upload_post_user)


def upload(video_path: Path, *, title: str, description: str,
           platforms: list[str], tags: list[str]) -> dict:
    """Returns {"ok": bool, "urls": {platform: url}, "raw": ..., "error": str}."""
    s = get_settings()
    if not configured():
        return {"ok": False, "error": "UPLOAD_POST_API_KEY / UPLOAD_POST_USER not set"}
    if not video_path.is_file():
        return {"ok": False, "error": f"file missing: {video_path}"}

    cfg = s.publish
    data: list[tuple[str, str]] = [
        ("user", s.upload_post_user),
        ("title", title[: cfg.title_max]),
    ]
    for p in platforms:
        data.append(("platform[]", p))
    if any(p.startswith("youtube") for p in platforms):
        data += [
            ("youtube_title", title[:100]),
            ("youtube_description", description[:4900]),
            ("privacyStatus", cfg.youtube_privacy),
            ("containsSyntheticMedia", "true"),
        ]
        for t in tags:
            data.append(("tags[]", t.lstrip("#")))
    if any(p in ("tiktok", "instagram", "facebook", "threads") for p in platforms):
        data.append(("description", description[:2100]))

    headers = {"Authorization": f"Apikey {s.upload_post_api_key}"}
    try:
        with video_path.open("rb") as fh, httpx.Client(timeout=600) as c:
            r = c.post(_URL, headers=headers,
                       data=data, files={"video": (video_path.name, fh, "video/mp4")})
        r.raise_for_status()
        body = r.json()
    except httpx.HTTPStatusError as exc:
        return {"ok": False, "error": f"{exc.response.status_code}: {exc.response.text[:300]}"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}

    # response shape: {"success": bool, "results": {platform: {..., "url"/"share_url"}}}
    ok = bool(body.get("success", True)) and "error" not in body
    urls: dict[str, str] = {}
    results = body.get("results") or body.get("result") or {}
    if isinstance(results, dict):
        for plat, res in results.items():
            if isinstance(res, dict):
                u = res.get("url") or res.get("share_url") or res.get("video_url") \
                    or res.get("post_url")
                if u:
                    urls[plat] = u
            elif isinstance(res, str):
                urls[plat] = res
    return {"ok": ok, "urls": urls, "raw": body,
            "error": None if ok else str(body.get("error") or body)}
