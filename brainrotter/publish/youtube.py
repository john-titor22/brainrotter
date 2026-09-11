"""YouTube Shorts via the YouTube Data API v3 — your own Google Cloud OAuth app.

Setup (once):
  1. console.cloud.google.com → new project
  2. "APIs & Services" → enable **YouTube Data API v3**
  3. "Credentials" → Create OAuth client ID → type **Web application**
     Authorized redirect URI:  http://localhost:8721/callback
  4. copy the client ID + secret into .env:
       YOUTUBE_CLIENT_ID=...
       YOUTUBE_CLIENT_SECRET=...
  5. `brainrotter publish-auth youtube`   (browser consent, once)

Free quota: 10,000 units/day, an upload costs 1,600 → ~6 uploads/day.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import httpx

from ..config import get_settings
from . import oauth
from .base import DEFAULT_ACCOUNT, Meta, list_accounts, load_token, save_token, token_expired

log = logging.getLogger("brainrotter.publish")
NAME = "youtube"

_AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN = "https://oauth2.googleapis.com/token"
# .readonly is only here so channels.list (the channel's display name, for
# account_label below) is allowed — upload itself only needs .upload.
_SCOPE = ("https://www.googleapis.com/auth/youtube.upload "
         "https://www.googleapis.com/auth/youtube.readonly")
_UPLOAD = ("https://www.googleapis.com/upload/youtube/v3/videos"
           "?uploadType=resumable&part=snippet,status")


def _creds() -> tuple[str, str]:
    s = get_settings()
    return s.youtube_client_id or "", s.youtube_client_secret or ""


def configured() -> bool:
    return all(_creds())


def accounts() -> list[str]:
    return list_accounts(NAME)


def authed(account: str = DEFAULT_ACCOUNT) -> bool:
    return bool(load_token(NAME, account).get("refresh_token"))


def auth(open_browser: bool = True, account: str = DEFAULT_ACCOUNT) -> str:
    cid, csec = _creds()
    if not (cid and csec):
        return "set YOUTUBE_CLIENT_ID / YOUTUBE_CLIENT_SECRET in .env first"
    tok = oauth.run_flow(
        auth_url=_AUTH, token_url=_TOKEN, client_id=cid, client_secret=csec,
        scope=_SCOPE, open_browser=open_browser,
        extra_auth={"access_type": "offline", "prompt": "consent"},
    )
    if "refresh_token" not in tok:
        return "no refresh token returned — revoke the app at myaccount.google.com and retry"
    channel_title = None
    try:
        r = httpx.get("https://www.googleapis.com/youtube/v3/channels",
                      params={"part": "snippet", "mine": "true"},
                      headers={"Authorization": f"Bearer {tok['access_token']}"}, timeout=20)
        r.raise_for_status()
        items = r.json().get("items", [])
        if items:
            channel_title = items[0]["snippet"]["title"]
            tok["channel_title"] = channel_title
            tok["channel_id"] = items[0]["id"]
    except Exception as exc:  # noqa: BLE001
        log.warning("couldn't fetch channel name for %s: %s", account, exc)
    save_token(NAME, tok, account)
    who = f" — {channel_title}" if channel_title else ""
    return f"youtube authorized (account: {account}){who}"


_label_fetch_tried: set[str] = set()  # per-process, avoid retrying a failing fetch every poll


def account_label(account: str = DEFAULT_ACCOUNT) -> str:
    """Human-readable name for this account — the real channel title once
    known, else just the account id you gave it at auth time. Accounts
    authorized before this existed have no cached title yet; back it in
    once (cheap: channels.list costs 1 quota unit) rather than requiring
    re-auth, but only try once per process so a failure doesn't add a
    network round-trip to every dashboard poll."""
    tok = load_token(NAME, account)
    title = tok.get("channel_title")
    if title or account in _label_fetch_tried or not tok.get("refresh_token"):
        return title or account
    _label_fetch_tried.add(account)
    try:
        access = _access_token(account)
        r = httpx.get("https://www.googleapis.com/youtube/v3/channels",
                      params={"part": "snippet", "mine": "true"},
                      headers={"Authorization": f"Bearer {access}"}, timeout=10)
        r.raise_for_status()
        items = r.json().get("items", [])
        if items:
            title = items[0]["snippet"]["title"]
            tok["channel_title"] = title
            tok["channel_id"] = items[0]["id"]
            save_token(NAME, tok, account)
    except Exception as exc:  # noqa: BLE001
        log.warning("couldn't backfill channel name for %s: %s", account, exc)
    return title or account


def _access_token(account: str = DEFAULT_ACCOUNT) -> str:
    tok = load_token(NAME, account)
    if not tok.get("refresh_token"):
        raise RuntimeError("not authorized — run `brainrotter publish-auth youtube`")
    if token_expired(tok):
        cid, csec = _creds()
        fresh = oauth.refresh(token_url=_TOKEN, client_id=cid, client_secret=csec,
                              refresh_token=tok["refresh_token"])
        tok.update(fresh)
        save_token(NAME, tok, account)
    return tok["access_token"]


def upload(video: Path, meta: Meta, account: str = DEFAULT_ACCOUNT) -> dict:
    try:
        access = _access_token(account)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}

    cfg = get_settings().publish
    privacy = {"public": "public", "unlisted": "unlisted", "private": "private"}.get(
        cfg.youtube_privacy, "public")
    body = {
        "snippet": {
            "title": meta.title[:100],
            "description": (meta.description + "\n\n#Shorts")[:4900],
            "tags": [t.lstrip("#") for t in meta.tags][:15],
            "categoryId": cfg.youtube_category,
        },
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": bool(meta.made_for_kids),
            "containsSyntheticMedia": bool(meta.synthetic),
        },
    }
    size = video.stat().st_size
    try:
        with httpx.Client(timeout=120) as c:
            # 1. start a resumable session
            r = c.post(_UPLOAD, headers={
                "Authorization": f"Bearer {access}",
                "Content-Type": "application/json; charset=UTF-8",
                "X-Upload-Content-Type": "video/*",
                "X-Upload-Content-Length": str(size),
            }, content=json.dumps(body))
            r.raise_for_status()
            session = r.headers["Location"]
            # 2. send the bytes
            with video.open("rb") as fh:
                up = c.put(session, timeout=None, content=fh.read(),
                           headers={"Content-Type": "video/*",
                                    "Content-Length": str(size)})
            up.raise_for_status()
            vid = up.json()["id"]
        return {"ok": True, "url": f"https://youtube.com/shorts/{vid}"}
    except httpx.HTTPStatusError as exc:
        return {"ok": False, "error": f"{exc.response.status_code}: {exc.response.text[:400]}"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}
