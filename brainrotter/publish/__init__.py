"""Publish finished videos as Shorts to YouTube / Instagram / Facebook / TikTok
(your own developer apps — unlimited), then optionally delete the local file.

Providers (config ``publish.providers``): ``youtube``, ``instagram``,
``facebook``, ``tiktok`` (native, need ``brainrotter publish-auth <p>`` once),
and ``upload_post`` (upload-post.com, one key, 10/mo free).
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from .. import db
from ..config import get_settings
from . import meta as _meta_prov
from . import tiktok as _tiktok_prov
from . import upload_post
from . import youtube as _yt_prov
from .base import DEFAULT_ACCOUNT, Meta

log = logging.getLogger("brainrotter.publish")

# provider name in config -> module (+ whether it's an "only" target of `meta`)
_NATIVE = {
    "youtube": (_yt_prov, None),
    "instagram": (_meta_prov, "instagram"),
    "facebook": (_meta_prov, "facebook"),
    "tiktok": (_tiktok_prov, None),
}

# How long to skip an account after it fails before trying it again. Platforms
# don't tell us their real cooldown, so this is a best-effort backoff, not a
# guarantee — a fresh account just means "hasn't failed recently", not
# "definitely won't fail again".
_ACCOUNT_COOLDOWN_SECONDS = 2 * 3600


def _enabled_providers() -> list[str]:
    return [p for p in get_settings().publish.providers if p in _NATIVE or p == "upload_post"]


def accounts_for(name: str) -> list[str]:
    """Every account with a saved token for this platform."""
    if name == "upload_post":
        return [DEFAULT_ACCOUNT] if upload_post.configured() else []
    mod, _ = _NATIVE[name]
    return mod.accounts()


def authed_accounts_for(name: str) -> list[str]:
    """Public wrapper — every account on this platform that's actually authorized."""
    return _authed_accounts(name)


def _authed_accounts(name: str) -> list[str]:
    if name == "upload_post":
        return accounts_for(name)
    mod, _ = _NATIVE[name]
    return [a for a in mod.accounts() if mod.authed(a)]


def _ready_platforms() -> set[str]:
    """Every enabled platform that can actually publish right now (has an
    authorized account, or is upload_post with a key). Used to decide when a
    video has been posted *everywhere it's going* — not just to whichever
    platform you happened to click first — before its local file is freed."""
    out = set()
    for name in _enabled_providers():
        if name == "upload_post":
            if upload_post.configured():
                out.add(name)
        elif _authed_accounts(name):
            out.add(name)
    return out


def _account_key(name: str, account: str) -> str:
    """Always a distinct key per account (including the default one) — never
    alias to the bare platform key. Earlier this returned ``name`` unchanged
    for the default account, which meant a *different* account's attempt
    would silently overwrite what the default account's own cooldown check
    reads, corrupting rotation fairness between accounts, not just the
    display."""
    return f"{name}::{account}"


def _record_attempt(key: str, ok: bool, error: str | None) -> None:
    """Last publish attempt per provider (or provider+account), so the
    dashboard can tell you *why* uploads are stuck and roughly when they last
    worked — that's the only honest way to know "when can we upload again":
    platforms don't tell us their cooldown, we just show you the last real
    error."""
    db.meta_set(f"publish_last_{key}", json.dumps({
        "ok": ok, "at": datetime.now(timezone.utc).isoformat(), "error": error,
    }))


_legacy_keys_migrated = False


def _migrate_legacy_attempt_keys() -> None:
    """One-time: the default account's last-attempt used to be stored under
    the bare platform key (publish_last_<name>) instead of a namespaced one
    — see _account_key. Copy it forward once so a real recent failure (e.g.
    a just-hit quota) isn't silently forgotten by the key-format change,
    which would otherwise make that account look fresh again and get
    reselected immediately."""
    global _legacy_keys_migrated
    if _legacy_keys_migrated:
        return
    _legacy_keys_migrated = True
    for name in _NATIVE:
        legacy = db.meta_get(f"publish_last_{name}")
        new_key = f"publish_last_{_account_key(name, DEFAULT_ACCOUNT)}"
        if legacy and not db.meta_get(new_key):
            db.meta_set(new_key, legacy)


def _last_attempt(key: str) -> dict | None:
    raw = db.meta_get(f"publish_last_{key}")
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


def _select_account(name: str, accs: list[str]) -> str:
    """Round-robins across a platform's authorized accounts (extra daily
    capacity beyond one account's quota), skipping any that failed within
    the cooldown window unless every account is currently cooling down."""
    _migrate_legacy_attempt_keys()
    if len(accs) == 1:
        return accs[0]
    cursor_key = f"publish_rotation_{name}"
    try:
        start = int(db.meta_get(cursor_key, "0") or "0") % len(accs)
    except ValueError:
        start = 0
    order = accs[start:] + accs[:start]
    now = datetime.now(timezone.utc)
    fresh = []
    for acc in order:
        last = _last_attempt(_account_key(name, acc))
        if last and not last.get("ok"):
            try:
                age = (now - datetime.fromisoformat(last["at"])).total_seconds()
            except Exception:
                age = _ACCOUNT_COOLDOWN_SECONDS  # unparsable timestamp -> don't block on it
            if age < _ACCOUNT_COOLDOWN_SECONDS:
                continue
        fresh.append(acc)
    chosen = fresh[0] if fresh else order[0]
    db.meta_set(cursor_key, str((accs.index(chosen) + 1) % len(accs)))
    return chosen


def provider_status() -> list[dict]:
    """For the dashboard / doctor — each provider's readiness plus its last
    publish attempt (ok/error/when), so a stuck platform is visible at a
    glance. ``accounts`` breaks the same down per authorized account."""
    _migrate_legacy_attempt_keys()
    out = []
    for name in _enabled_providers():
        if name == "upload_post":
            entry = {"name": "upload_post", "configured": upload_post.configured(),
                     "authed": upload_post.configured(),
                     "hint": "UPLOAD_POST_API_KEY + UPLOAD_POST_USER", "accounts": []}
        else:
            mod, _ = _NATIVE[name]
            acc_entries = []
            for a in mod.accounts():
                label = (mod.account_label(a, platform=name) if mod is _meta_prov
                         else mod.account_label(a))
                ae = {"account": a, "label": label, "authed": mod.authed(a)}
                last = _last_attempt(_account_key(name, a))
                if last:
                    ae["last_ok"] = last.get("ok")
                    ae["last_at"] = last.get("at")
                    ae["last_error"] = last.get("error")
                acc_entries.append(ae)
            entry = {"name": name, "configured": mod.configured(),
                     "authed": any(ae["authed"] for ae in acc_entries),
                     "hint": f"`brainrotter publish-auth {'meta' if mod is _meta_prov else name}`",
                     "accounts": acc_entries}
            # Platform-level rollup = whichever account was tried most
            # recently (each account has its own independent key — see
            # _account_key — so this is a read-only summary, not a separate
            # write that could go stale or get overwritten by another
            # account's attempt).
            with_ts = [ae for ae in acc_entries if ae.get("last_at")]
            if with_ts:
                latest = max(with_ts, key=lambda ae: ae["last_at"])
                entry["last_ok"] = latest.get("last_ok")
                entry["last_at"] = latest.get("last_at")
                entry["last_error"] = latest.get("last_error")
            out.append(entry)
            continue
        last = _last_attempt("upload_post")
        if last:
            entry["last_ok"] = last.get("ok")
            entry["last_at"] = last.get("at")
            entry["last_error"] = last.get("error")
        out.append(entry)
    return out


def any_ready() -> bool:
    return any(p["authed"] or (p["name"] == "upload_post" and p["configured"])
               for p in provider_status())


def available() -> bool:
    return get_settings().publish.enabled and any_ready()


def status() -> str:
    ps = provider_status()
    if not ps:
        return "no providers configured ([publish] providers)"
    ready = [p["name"] for p in ps if p["authed"] or (p["name"] == "upload_post" and p["configured"])]
    pend = [p["name"] for p in ps if p["name"] not in ready]
    bits = []
    if ready:
        bits.append("ready: " + ", ".join(ready))
    if pend:
        bits.append("needs setup: " + ", ".join(pend))
    if not get_settings().publish.enabled:
        bits.append("([publish] enabled = false)")
    elif auto_publish_on():
        bits.append("auto-publish ON")
    return " · ".join(bits)


def auto_publish_on() -> bool:
    m = db.meta_get("publish_auto")
    if m is not None:
        return m == "1"
    return get_settings().publish.auto_publish


def set_auto_publish(on: bool) -> None:
    db.meta_set("publish_auto", "1" if on else "0")


# --- metadata from the script ---------------------------------------------

def _meta_for(video: dict) -> Meta:
    cfg = get_settings().publish
    title = (video.get("topic") or "").strip()
    tags: list[str] = []
    cta = ""
    try:
        with db.connect() as conn:
            j = conn.execute("SELECT script_json FROM jobs WHERE id = ?",
                             (video["job_id"],)).fetchone()
        script = json.loads(j["script_json"]) if j and j["script_json"] else {}
        title = (script.get("title") or title).strip()
        tags = [t.lstrip("#") for t in (script.get("hashtags") or [])][: cfg.hashtags]
        cta = (script.get("cta") or "").strip()
    except Exception:
        pass
    if not tags:
        tags = ["shorts", "fyp", video.get("format_id") or "video"]
    desc = " ".join(x for x in (title, cta) if x)
    return Meta(title=title[: cfg.title_max], description=desc, tags=tags)


# --- publish -------------------------------------------------------------

def publish(video_id: str, providers: list[str] | None = None,
           accounts: dict[str, str] | None = None) -> dict:
    """``accounts`` optionally forces a specific account per platform
    ({"youtube": "second"}) instead of letting _select_account rotate —
    the dashboard's per-platform account picker uses this. Falls back to
    rotation for any platform not named in it."""
    s = get_settings().publish
    video = db.get_video(video_id)
    if not video:
        return {"ok": False, "error": f"no video {video_id}"}

    existing_urls = _load_urls(video)
    requested = providers or _enabled_providers()
    # Skip platforms this video is already published to, but don't refuse
    # the whole call just because some OTHER platform succeeded earlier —
    # that used to permanently block "publish facebook" once youtube had
    # already gone out for the same video (checked video["published_at"],
    # which is set the moment ANY platform succeeds).
    pending = [n for n in requested if n not in existing_urls]
    if not pending:
        return {"ok": True, "already": True, "urls": existing_urls, "video_id": video_id}

    path = Path(video["path"])
    if not path.is_file():
        db.mark_published(video_id, error="local file already gone")
        return {"ok": False, "error": "local file missing", "urls": existing_urls}

    names = pending
    meta = _meta_for(video)
    urls: dict[str, str] = {}
    accounts_used: dict[str, str] = {}
    errors: list[str] = []
    log.info("publishing %s → %s", video_id, ", ".join(names))

    for name in names:
        account = None  # set once selected below; guards the except clause
        try:
            if name == "upload_post":
                r = upload_post.upload(path, title=meta.title, description=meta.description,
                                       platforms=[p for p in s.providers if p != "upload_post"],
                                       tags=meta.tags)
                for k, u in (r.get("urls") or {}).items():
                    urls[k] = u
                if not r["ok"]:
                    errors.append(f"upload_post: {r.get('error')}")
                _record_attempt("upload_post", bool(r["ok"]), r.get("error"))
                continue

            mod, only = _NATIVE[name]
            accs = _authed_accounts(name)
            if not accs:
                who = "meta" if mod is _meta_prov else name
                errors.append(f"{name}: not authorized — run `brainrotter publish-auth {who}`")
                continue
            forced = (accounts or {}).get(name)
            account = forced if forced in accs else _select_account(name, accs)
            r = (mod.upload(path, meta, only=only, account=account) if only
                 else mod.upload(path, meta, account=account))
            ok_this = bool(r.get("ok"))
            # Name the account whenever there's more than one to pick from —
            # "youtube: quota exceeded" is ambiguous once a second channel
            # exists; which one actually failed matters.
            acc_note = f" [{account}]" if len(accs) > 1 else ""
            if ok_this:
                if r.get("urls"):
                    urls.update(r["urls"])
                elif r.get("url"):
                    urls[name] = r["url"]
                accounts_used[name] = account
            else:
                errors.append(f"{name}: {r.get('error')}{acc_note}")
            _record_attempt(_account_key(name, account), ok_this, None if ok_this else r.get("error"))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{name}: {exc}")
            if account is not None:
                _record_attempt(_account_key(name, account), False, str(exc))

    ok = bool(urls)
    deleted = False
    merged_platforms = {**existing_urls, **urls}
    ready = _ready_platforms()
    # Only free the file once it's posted to every platform that's actually
    # ready to receive it — publishing to youtube alone used to delete the
    # source immediately, which meant a follow-up "publish facebook" click
    # on the same video would fail with "local file missing" even though
    # you clearly intended to post it there too.
    if ok and s.delete_local_after_publish and ready and ready.issubset(merged_platforms):
        try:
            os.remove(path)
            deleted = True
            log.info("freed %s (published to every ready platform: %s)",
                     path.name, sorted(merged_platforms))
        except OSError as exc:
            log.warning("could not delete %s: %s", path, exc)

    db.mark_published(video_id,
                      platforms=list(urls) if urls else None,
                      urls=urls or None,
                      accounts=accounts_used or None,
                      error="; ".join(errors) or None,
                      local_deleted=deleted)
    return {"ok": ok, "urls": urls, "errors": errors, "deleted_local": deleted,
            "video_id": video_id, "error": "; ".join(errors) or None}


def publish_job(job_id: str, providers: list[str] | None = None,
                accounts: dict[str, str] | None = None) -> dict:
    v = db.get_video_by_job(job_id)
    if not v:
        return {"ok": False, "error": f"no video for job {job_id}"}
    return publish(v["id"], providers, accounts)


def _load_urls(video: dict) -> dict:
    try:
        return json.loads(video.get("platform_urls") or "{}")
    except Exception:
        return {}


def delete_local(video_id: str) -> dict:
    """Manually free a video's local file, regardless of publish status —
    the dashboard's per-card delete button, for renders you don't want to
    keep taking up disk (whether or not you ever published them anywhere)."""
    video = db.get_video(video_id)
    if not video:
        return {"ok": False, "error": f"no video {video_id}"}
    path = Path(video["path"])
    if not video.get("local_deleted") and path.is_file():
        try:
            os.remove(path)
        except OSError as exc:
            return {"ok": False, "error": str(exc)}
    db.set_local_deleted(video_id, True)
    return {"ok": True}


def delete_local_by_job(job_id: str) -> dict:
    v = db.get_video_by_job(job_id)
    if not v:
        return {"ok": False, "error": f"no video for job {job_id}"}
    return delete_local(v["id"])


# back-compat: some callers still import upload_post via this package
__all__ = ["publish", "publish_job", "available", "status", "auto_publish_on",
           "set_auto_publish", "provider_status", "any_ready", "upload_post",
           "accounts_for", "authed_accounts_for", "delete_local", "delete_local_by_job"]
