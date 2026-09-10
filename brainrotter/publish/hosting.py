"""Temporarily host a video at a public URL — Instagram's Graph API fetches the
file from a URL rather than accepting an upload.

catbox.moe (permanent, 200 MB) / litterbox (72 h then auto-deleted). Both free,
no account.
"""

from __future__ import annotations

import logging
from pathlib import Path

import httpx

from ..config import get_settings

log = logging.getLogger("brainrotter.publish")


def host(video: Path) -> str | None:
    which = (get_settings().publish.upload_host or "litterbox").lower()
    if which == "catbox":
        url = "https://catbox.moe/user/api.php"
        data = {"reqtype": "fileupload"}
    else:
        url = "https://litterbox.catbox.moe/resources/internals/api.php"
        data = {"reqtype": "fileupload", "time": "72h"}
    try:
        with video.open("rb") as fh, httpx.Client(timeout=600) as c:
            r = c.post(url, data=data,
                       files={"fileToUpload": (video.name, fh, "video/mp4")})
        r.raise_for_status()
        link = r.text.strip()
        if link.startswith("http"):
            return link
        log.warning("host upload returned: %s", link[:200])
    except Exception as exc:  # noqa: BLE001
        log.warning("temp host failed: %s", exc)
    return None
