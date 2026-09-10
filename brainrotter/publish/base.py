"""Shared bits for the native publish providers.

Each provider is a module in this package exposing:
    NAME            : str
    configured()    : bool         # app creds present
    authed()        : bool         # a usable token is stored
    auth(open_browser=True) -> str # run the one-time OAuth flow, returns a message
    upload(video: Path, meta: Meta) -> dict   # {"ok": bool, "url": str|None, "error": str|None}

Tokens live in ``workspace/publish/<name>.json`` (gitignored under workspace/).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..config import get_settings


def _store_dir() -> Path:
    d = get_settings().workspace / "publish"
    d.mkdir(parents=True, exist_ok=True)
    return d


def token_path(name: str) -> Path:
    return _store_dir() / f"{name}.json"


def load_token(name: str) -> dict:
    p = token_path(name)
    try:
        return json.loads(p.read_text("utf-8")) if p.is_file() else {}
    except Exception:
        return {}


def save_token(name: str, data: dict) -> None:
    token_path(name).write_text(json.dumps(data, indent=1), encoding="utf-8")


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
