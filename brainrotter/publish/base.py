"""Shared bits for the native publish providers.

Each provider is a module in this package exposing:
    NAME            : str
    configured()    : bool         # app creds present
    authed()        : bool         # a usable token is stored
    auth(open_browser=True) -> str # run the one-time OAuth flow, returns a message
    upload(video: Path, meta: Meta) -> dict   # {"ok": bool, "url": str|None, "error": str|None}

Tokens live in ``workspace/publish/<name>/<account>.json`` (gitignored under
workspace/) — one file per platform per account, so a platform can have
several authorized accounts (e.g. two YouTube channels) that publish()
rotates across for extra daily capacity. ``account`` defaults to "default"
everywhere, so single-account use (the common case) needs no extra args.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..config import get_settings

DEFAULT_ACCOUNT = "default"


def _store_dir() -> Path:
    d = get_settings().workspace / "publish"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _account_dir(name: str) -> Path:
    d = _store_dir() / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def _migrate_legacy(name: str) -> None:
    """One-time upgrade from the old single-account layout
    (workspace/publish/<name>.json) into <name>/default.json."""
    legacy = _store_dir() / f"{name}.json"
    target = _account_dir(name) / f"{DEFAULT_ACCOUNT}.json"
    if legacy.is_file() and not target.is_file():
        target.write_text(legacy.read_text("utf-8"), encoding="utf-8")


def token_path(name: str, account: str = DEFAULT_ACCOUNT) -> Path:
    _migrate_legacy(name)
    return _account_dir(name) / f"{account}.json"


def list_accounts(name: str) -> list[str]:
    """Every account with a saved token for this platform, oldest/default first."""
    _migrate_legacy(name)
    accounts = sorted(p.stem for p in _account_dir(name).glob("*.json"))
    if DEFAULT_ACCOUNT in accounts:
        accounts.remove(DEFAULT_ACCOUNT)
        accounts.insert(0, DEFAULT_ACCOUNT)
    return accounts


def load_token(name: str, account: str = DEFAULT_ACCOUNT) -> dict:
    p = token_path(name, account)
    try:
        return json.loads(p.read_text("utf-8")) if p.is_file() else {}
    except Exception:
        return {}


def save_token(name: str, data: dict, account: str = DEFAULT_ACCOUNT) -> None:
    token_path(name, account).write_text(json.dumps(data, indent=1), encoding="utf-8")


def token_expired(tok: dict, skew: int = 120) -> bool:
    exp = tok.get("expires_at")
    return not exp or time.time() > (float(exp) - skew)


@dataclass
class Meta:
    """What every platform needs to describe the post."""
    title: str
    description: str
    tags: list[str] = field(default_factory=list)
    made_for_kids: bool = False
    synthetic: bool = True          # AI-generated — platforms want this disclosed
