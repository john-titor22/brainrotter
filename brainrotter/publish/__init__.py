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
from pathlib import Path

from .. import db
from ..config import get_settings
from . import meta as _meta_prov
from . import tiktok as _tiktok_prov
from . import upload_post
from . import youtube as _yt_prov
from .base import Meta

log = logging.getLogger("brainrotter.publish")

# provider name in config -> module (+ whether it's an "only" target of `meta`)
_NATIVE = {
    "youtube": (_yt_prov, None),
    "instagram": (_meta_prov, "instagram"),
    "facebook": (_meta_prov, "facebook"),
    "tiktok": (_tiktok_prov, None),
}


def _enabled_providers() -> list[str]:
    return [p for p in get_settings().publish.providers if p in _NATIVE or p == "upload_post"]


def provider_status() -> list[dict]:
    """For the dashboard / doctor — each provider's readiness."""
    out = []
    for name in _enabled_providers():
        if name == "upload_post":
            out.append({"name": "upload_post", "configured": upload_post.configured(),
                        "authed": upload_post.configured(),
                        "hint": "UPLOAD_POST_API_KEY + UPLOAD_POST_USER"})
            continue
        mod, _ = _NATIVE[name]
        out.append({"name": name, "configured": mod.configured(), "authed": mod.authed(),
                    "hint": f"`brainrotter publish-auth {'meta' if mod is _meta_prov else name}`"})
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

def publish(video_id: str, providers: list[str] | None = None) -> dict:
    s = get_settings().publish
    video = db.get_video(video_id)
    if not video:
        return {"ok": False, "error": f"no video {video_id}"}
    if video.get("published_at"):
        return {"ok": True, "already": True, "urls": _load_urls(video), "video_id": video_id}

    path = Path(video["path"])
    if not path.is_file():
        db.mark_published(video_id, error="local file already gone")
        return {"ok": False, "error": "local file missing"}

    names = providers or _enabled_providers()
    meta = _meta_for(video)
    urls: dict[str, str] = {}
    errors: list[str] = []
    log.info("publishing %s → %s", video_id, ", ".join(names))

    for name in names:
        try:
            if name == "upload_post":
                r = upload_post.upload(path, title=meta.title, description=meta.description,
                                       platforms=[p for p in s.providers if p != "upload_post"],
                                       tags=meta.tags)
                for k, u in (r.get("urls") or {}).items():
                    urls[k] = u
                if not r["ok"]:
                    errors.append(f"upload_post: {r.get('error')}")
                continue
            mod, only = _NATIVE[name]
            r = mod.upload(path, meta, only=only) if only else mod.upload(path, meta)
            if r.get("ok"):
                if r.get("urls"):
                    urls.update(r["urls"])
                elif r.get("url"):
                    urls[name] = r["url"]
            else:
                errors.append(f"{name}: {r.get('error')}")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{name}: {exc}")

    ok = bool(urls)
    deleted = False
    if ok and s.delete_local_after_publish:
        try:
            os.remove(path)
            deleted = True
            log.info("freed %s", path.name)
        except OSError as exc:
            log.warning("could not delete %s: %s", path, exc)

    db.mark_published(video_id,
                      platforms=list(urls) if urls else None,
                      urls=urls or None,
                      error="; ".join(errors) or None,
                      local_deleted=deleted)
    return {"ok": ok, "urls": urls, "errors": errors, "deleted_local": deleted,
            "video_id": video_id, "error": "; ".join(errors) or None}


def publish_job(job_id: str, providers: list[str] | None = None) -> dict:
    v = db.get_video_by_job(job_id)
    if not v:
        return {"ok": False, "error": f"no video for job {job_id}"}
    return publish(v["id"], providers)


def _load_urls(video: dict) -> dict:
    try:
        return json.loads(video.get("platform_urls") or "{}")
    except Exception:
        return {}


# back-compat: some callers still import upload_post via this package
__all__ = ["publish", "publish_job", "available", "status", "auto_publish_on",
           "set_auto_publish", "provider_status", "any_ready", "upload_post"]
