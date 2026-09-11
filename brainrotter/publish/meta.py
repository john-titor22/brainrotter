"""Instagram Reels + Facebook Reels via the Meta Graph API — one developer app
covers both.

Setup (once):
  1. developers.facebook.com → Create app → type **Business**
  2. Add products: **Facebook Login for Business** and **Instagram Graph API**
  3. Your Instagram must be a **Business/Creator** account linked to a **Facebook Page**
  4. Facebook Login settings → Valid OAuth Redirect URIs:
       http://localhost:8721/callback
  5. .env:
       META_APP_ID=...
       META_APP_SECRET=...
  6. `brainrotter publish-auth meta`   (browser consent — grant the Page + IG)

The Page id + IG account id are auto-discovered and cached. ~50 IG posts / 24h.
Instagram fetches the video from a temporary public URL (litterbox/catbox).
"""

from __future__ import annotations

import logging
import time
import urllib.parse
from pathlib import Path

import httpx

from ..config import get_settings
from . import hosting, oauth
from .base import DEFAULT_ACCOUNT, Meta, list_accounts, load_token, save_token

log = logging.getLogger("brainrotter.publish")
NAME = "meta"
GV = "v21.0"
_G = f"https://graph.facebook.com/{GV}"
_AUTH = f"https://www.facebook.com/{GV}/dialog/oauth"
_SCOPE = ("instagram_basic,instagram_content_publish,pages_show_list,"
          "pages_read_engagement,pages_manage_posts,business_management")


def _creds() -> tuple[str, str]:
    s = get_settings()
    return s.meta_app_id or "", s.meta_app_secret or ""


def configured() -> bool:
    return all(_creds())


def accounts() -> list[str]:
    return list_accounts(NAME)


def authed(account: str = DEFAULT_ACCOUNT) -> bool:
    t = load_token(NAME, account)
    return bool(t.get("page_token") and t.get("page_id"))


def auth(open_browser: bool = True, account: str = DEFAULT_ACCOUNT) -> str:
    cid, csec = _creds()
    if not (cid and csec):
        return "set META_APP_ID / META_APP_SECRET in .env first"

    tok = oauth.run_flow(
        auth_url=_AUTH, token_url=f"{_G}/oauth/access_token",
        client_id=cid, client_secret=csec, scope=_SCOPE,
        open_browser=open_browser,
    )
    user_token = tok["access_token"]

    # short-lived -> long-lived (60 days)
    try:
        r = httpx.get(f"{_G}/oauth/access_token", params={
            "grant_type": "fb_exchange_token", "client_id": cid,
            "client_secret": csec, "fb_exchange_token": user_token,
        }, timeout=30)
        r.raise_for_status()
        user_token = r.json()["access_token"]
    except Exception as exc:  # noqa: BLE001
        log.warning("long-lived exchange failed (%s) — using short-lived", exc)

    # find the Page + linked IG business account
    r = httpx.get(f"{_G}/me/accounts", params={
        "fields": "name,id,access_token,instagram_business_account{id,username}",
        "access_token": user_token,
    }, timeout=30)
    r.raise_for_status()
    pages = r.json().get("data", [])
    if not pages:
        return "no Facebook Page found on this account — create/select one and retry"
    pg = pages[0]
    ig = (pg.get("instagram_business_account") or {})
    save_token(NAME, {
        "user_token": user_token,
        "page_token": pg["access_token"],
        "page_id": pg["id"],
        "page_name": pg.get("name"),
        "ig_user_id": ig.get("id"),
        "ig_username": ig.get("username"),
        "saved_at": time.time(),
    }, account)
    tail = f" + Instagram @{ig.get('username')}" if ig.get("id") else " (no IG linked)"
    return f"meta authorized (account: {account}): Page '{pg.get('name')}'{tail}"


def account_label(account: str = DEFAULT_ACCOUNT, *, platform: str = "facebook") -> str:
    """Human-readable name for this account — the Page name (for facebook)
    or the @handle (for instagram) once known, else just the account id
    you gave it at auth time."""
    t = load_token(NAME, account)
    if platform == "instagram" and t.get("ig_username"):
        return f"@{t['ig_username']}"
    return t.get("page_name") or account


def _caption(meta: Meta) -> str:
    ht = " ".join("#" + t.lstrip("#") for t in meta.tags[:8] + ["Shorts"])
    return (meta.description + " " + ht).strip()[:2100]


def _publish_instagram(video: Path, meta: Meta, tok: dict) -> dict:
    ig = tok.get("ig_user_id")
    if not ig:
        return {"ok": False, "error": "no Instagram Business account linked to the Page"}
    url = hosting.host(video)
    if not url:
        return {"ok": False, "error": "could not host the video for Instagram to fetch"}
    tk = tok["page_token"]
    try:
        with httpx.Client(timeout=120) as c:
            r = c.post(f"{_G}/{ig}/media", data={
                "media_type": "REELS", "video_url": url,
                "caption": _caption(meta),
                "share_to_feed": str(get_settings().publish.ig_share_to_feed).lower(),
                "access_token": tk,
            })
            r.raise_for_status()
            creation = r.json()["id"]
            # poll until the fetched video is processed
            for _ in range(40):
                time.sleep(6)
                st = c.get(f"{_G}/{creation}", params={
                    "fields": "status_code", "access_token": tk}).json()
                if st.get("status_code") == "FINISHED":
                    break
                if st.get("status_code") == "ERROR":
                    return {"ok": False, "error": f"IG processing error: {st}"}
            pub = c.post(f"{_G}/{ig}/media_publish", data={
                "creation_id": creation, "access_token": tk})
            pub.raise_for_status()
            mid = pub.json()["id"]
            perm = c.get(f"{_G}/{mid}", params={
                "fields": "permalink", "access_token": tk}).json()
        return {"ok": True, "url": perm.get("permalink") or f"instagram media {mid}"}
    except httpx.HTTPStatusError as exc:
        return {"ok": False, "error": f"{exc.response.status_code}: {exc.response.text[:400]}"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


def _publish_facebook(video: Path, meta: Meta, tok: dict) -> dict:
    pid, tk = tok["page_id"], tok["page_token"]
    size = video.stat().st_size
    desc = _caption(meta)
    try:
        with httpx.Client(timeout=None) as c:
            r = c.post(f"{_G}/{pid}/video_reels", data={
                "upload_phase": "start", "access_token": tk})
            start = r.json()
            if "video_id" not in start:
                err = start.get("error", {})
                return {"ok": False, "error": f"start phase: "
                        f"{err.get('message') or start or r.text[:400]}"}
            video_id = start["video_id"]
            up_url = start["upload_url"]
            with video.open("rb") as fh:
                up = c.post(up_url, content=fh.read(), headers={
                    "Authorization": f"OAuth {tk}",
                    "offset": "0", "file_size": str(size),
                })
                if up.status_code >= 400:
                    return {"ok": False, "error": f"upload phase: {up.status_code}: {up.text[:400]}"}
            fin = c.post(f"{_G}/{pid}/video_reels", data={
                "upload_phase": "finish", "video_id": video_id,
                "video_state": "PUBLISHED", "description": desc,
                "access_token": tk,
            })
            fin.raise_for_status()
            fin_body = fin.json()
            if fin_body.get("success") is False:
                return {"ok": False, "error": f"finish phase: {fin_body}"}
        return {"ok": True, "url": f"https://facebook.com/reel/{video_id}"}
    except httpx.HTTPStatusError as exc:
        return {"ok": False, "error": f"{exc.response.status_code}: {exc.response.text[:400]}"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def upload(video: Path, meta: Meta, *, only: str | None = None,
          account: str = DEFAULT_ACCOUNT) -> dict:
    tok = load_token(NAME, account)
    if not tok.get("page_token"):
        return {"ok": False, "error": "not authorized — run `brainrotter publish-auth meta`"}
    results = {}
    targets = [only] if only else ["instagram", "facebook"]
    for plat in targets:
        results[plat] = (_publish_instagram if plat == "instagram" else _publish_facebook)(
            video, meta, tok)
    ok = any(v.get("ok") for v in results.values())
    urls = {p: v["url"] for p, v in results.items() if v.get("ok") and v.get("url")}
    errs = "; ".join(f"{p}: {v['error']}" for p, v in results.items() if not v.get("ok"))
    return {"ok": ok, "urls": urls, "url": next(iter(urls.values()), None),
            "error": errs or None, "per_platform": results}
