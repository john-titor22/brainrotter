"""Generic OAuth2 authorization-code flow with a throwaway localhost listener.

``run_flow(...)`` opens the browser, catches the redirect on
``http://localhost:<REDIRECT_PORT>/callback``, exchanges the code for tokens and
returns the token dict. Providers plug in their own URLs / params.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import secrets
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx

log = logging.getLogger("brainrotter.publish")

REDIRECT_PORT = 8721
REDIRECT_URI = f"http://localhost:{REDIRECT_PORT}/callback"


class _Catcher(BaseHTTPRequestHandler):
    result: dict = {}

    def do_GET(self):  # noqa: N802
        q = urllib.parse.urlparse(self.path)
        if q.path != "/callback":
            self.send_response(404); self.end_headers(); return
        params = urllib.parse.parse_qs(q.query)
        _Catcher.result = {k: v[0] for k, v in params.items()}
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        ok = "code" in _Catcher.result
        self.wfile.write(
            (f"<h2>{'Authorized — you can close this tab.' if ok else 'Auth failed.'}</h2>"
             f"<p>{_Catcher.result.get('error_description', '')}</p>").encode())

    def log_message(self, *a):  # silence
        return


def _pkce_pair() -> tuple[str, str]:
    """RFC 7636: a random code_verifier + its S256 code_challenge."""
    verifier = secrets.token_urlsafe(64)[:128]
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def run_flow(*, auth_url: str, token_url: str, client_id: str, client_secret: str,
             scope: str, extra_auth: dict | None = None,
             extra_token: dict | None = None, open_browser: bool = True,
             pkce: bool = False) -> dict:
    state = secrets.token_urlsafe(16)
    params = {
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": scope,
        "state": state,
        **(extra_auth or {}),
    }
    code_verifier = None
    if pkce:
        code_verifier, code_challenge = _pkce_pair()
        params["code_challenge"] = code_challenge
        params["code_challenge_method"] = "S256"
    url = auth_url + "?" + urllib.parse.urlencode(params)

    _Catcher.result = {}
    srv = HTTPServer(("localhost", REDIRECT_PORT), _Catcher)
    t = threading.Thread(target=srv.handle_request, daemon=True)
    t.start()

    print(f"\nOpen this URL and approve:\n  {url}\n")
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass

    deadline = time.time() + 300
    while not _Catcher.result and time.time() < deadline:
        time.sleep(0.3)
    srv.server_close()

    res = _Catcher.result
    if "code" not in res:
        raise RuntimeError(f"no auth code returned: {res.get('error_description') or res}")
    if res.get("state") not in (state, None):
        raise RuntimeError("OAuth state mismatch")

    data = {
        "grant_type": "authorization_code",
        "code": res["code"],
        "redirect_uri": REDIRECT_URI,
        "client_id": client_id,
        "client_secret": client_secret,
        **(extra_token or {}),
    }
    if code_verifier:
        data["code_verifier"] = code_verifier
    r = httpx.post(token_url, data=data, timeout=30,
                   headers={"Accept": "application/json"})
    r.raise_for_status()
    tok = r.json()
    if "expires_in" in tok:
        tok["expires_at"] = time.time() + float(tok["expires_in"])
    return tok


def refresh(*, token_url: str, client_id: str, client_secret: str,
            refresh_token: str, extra: dict | None = None) -> dict:
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
        "client_secret": client_secret,
        **(extra or {}),
    }
    r = httpx.post(token_url, data=data, timeout=30,
                   headers={"Accept": "application/json"})
    r.raise_for_status()
    tok = r.json()
    if "expires_in" in tok:
        tok["expires_at"] = time.time() + float(tok["expires_in"])
    tok.setdefault("refresh_token", refresh_token)
    return tok
