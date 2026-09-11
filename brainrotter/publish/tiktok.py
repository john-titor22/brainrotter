"""TikTok via the Content Posting API — your own developer app.

Setup (once):
  1. developers.tiktok.com → Manage apps → create an app
  2. Add products: **Login Kit** and **Content Posting API**
  3. Login Kit → Redirect URI:  http://localhost:8721/callback
  4. .env:
       TIKTOK_CLIENT_KEY=...
       TIKTOK_CLIENT_SECRET=...
  5. `brainrotter publish-auth tiktok`

IMPORTANT: until TikTok **audits** your app (gated review), posts can only go out
as PRIVATE (self-only). Once audited, ``publish.tiktok_privacy`` "auto" makes
them public. The allowed privacy levels are queried per account.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

import httpx

from ..config import get_settings
from . import oauth
from .base import DEFAULT_ACCOUNT, Meta, list_accounts, load_token, save_token, token_expired

log = logging.getLogger("brainrotter.publish")
NAME = "tiktok"

_AUTH = "https://www.tiktok.com/v2/auth/authorize/"
_TOKEN = "https://open.tiktokapis.com/v2/oauth/token/"
_API = "https://open.tiktokapis.com/v2"
_SCOPE = "user.info.basic,video.publish"


def _creds() -> tuple[str, str]:
    s = get_settings()
    return s.tiktok_client_key or "", s.tiktok_client_secret or ""


def configured() -> bool:
    return all(_creds())


def accounts() -> list[str]:
    return list_accounts(NAME)


def authed(account: str = DEFAULT_ACCOUNT) -> bool:
    return bool(load_token(NAME, account).get("refresh_token"))


def auth(open_browser: bool = True, account: str = DEFAULT_ACCOUNT) -> str:
    ck, cs = _creds()
    if not (ck and cs):
        return "set TIKTOK_CLIENT_KEY / TIKTOK_CLIENT_SECRET in .env first"
    # TikTok uses client_key (not client_id) in the auth URL, and requires
    # PKCE for a Desktop-registered redirect URI (not needed for Web).
    tok = oauth.run_flow(
        auth_url=_AUTH, token_url=_TOKEN, client_id=ck, client_secret=cs,
        scope=_SCOPE, open_browser=open_browser, pkce=True,
        extra_auth={"client_key": ck},
        extra_token={"client_key": ck},
    )
    if "refresh_token" not in tok:
        return f"no refresh token: {tok}"
    save_token(NAME, tok, account)
    return f"tiktok authorized (account: {account})"


def _access_token(account: str = DEFAULT_ACCOUNT) -> str:
    tok = load_token(NAME, account)
    if not tok.get("refresh_token"):
        raise RuntimeError("not authorized — run `brainrotter publish-auth tiktok`")
    if token_expired(tok):
        ck, cs = _creds()
        r = httpx.post(_TOKEN, data={
            "client_key": ck, "client_secret": cs,
            "grant_type": "refresh_token", "refresh_token": tok["refresh_token"],
        }, headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=30)
        r.raise_for_status()
        fresh = r.json()
        fresh["expires_at"] = time.time() + float(fresh.get("expires_in", 3600))
        fresh.setdefault("refresh_token", tok["refresh_token"])
        tok.update(fresh)
        save_token(NAME, tok, account)
    return tok["access_token"]


def _privacy(access: str) -> str:
    want = get_settings().publish.tiktok_privacy
    try:
        r = httpx.post(f"{_API}/post/publish/creator_info/query/",
                       headers={"Authorization": f"Bearer {access}"}, timeout=20)
        opts = r.json().get("data", {}).get("privacy_level_options", [])
    except Exception:
        opts = []
    if want and want != "auto" and want.upper() in opts:
        return want.upper()
    for p in ("PUBLIC_TO_EVERYONE", "SELF_ONLY"):
        if p in opts:
            return p
    return "SELF_ONLY"


def upload(video: Path, meta: Meta, account: str = DEFAULT_ACCOUNT) -> dict:
    try:
        access = _access_token(account)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}

    size = video.stat().st_size
    chunk = min(size, 64 * 1024 * 1024)
    n_chunks = max(1, -(-size // chunk))
    title = (meta.title + " " + " ".join("#" + t.lstrip("#")
             for t in meta.tags[:6] + ["Shorts"])).strip()[:2200]
    body = {
        "post_info": {
            "title": title,
            "privacy_level": _privacy(access),
            "disable_comment": False, "disable_duet": False, "disable_stitch": False,
        },
        "source_info": {
            "source": "FILE_UPLOAD", "video_size": size,
            "chunk_size": chunk, "total_chunk_count": n_chunks,
        },
    }
    try:
        with httpx.Client(timeout=None) as c:
            init = c.post(f"{_API}/post/publish/video/init/", json=body, headers={
                "Authorization": f"Bearer {access}",
                "Content-Type": "application/json; charset=UTF-8",
            })
            init.raise_for_status()
            d = init.json()["data"]
            publish_id, upload_url = d["publish_id"], d["upload_url"]

            data = video.read_bytes()
            for i in range(n_chunks):
                lo = i * chunk
                hi = min(size, lo + chunk) - 1
                c.put(upload_url, content=data[lo:hi + 1], headers={
                    "Content-Range": f"bytes {lo}-{hi}/{size}",
                    "Content-Type": "video/mp4",
                    "Content-Length": str(hi - lo + 1),
                })

            for _ in range(40):
                time.sleep(5)
                st = c.post(f"{_API}/post/publish/status/fetch/",
                            json={"publish_id": publish_id},
                            headers={"Authorization": f"Bearer {access}",
                                     "Content-Type": "application/json; charset=UTF-8"}).json()
                s = st.get("data", {}).get("status")
                if s in ("PUBLISH_COMPLETE", "SEND_TO_USER_INBOX"):
                    return {"ok": True, "url": "https://www.tiktok.com/ (see your profile / inbox)"}
                if s == "FAILED":
                    return {"ok": False, "error": f"tiktok publish failed: {st.get('data')}"}
        return {"ok": True, "url": "tiktok upload sent (processing)"}
    except httpx.HTTPStatusError as exc:
        return {"ok": False, "error": f"{exc.response.status_code}: {exc.response.text[:400]}"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}
