"""Publish adapters — upload finished videos to platforms.

Stub. Phase 3. The vendored engine already contains an upload-post integration
(TikTok / Instagram / Facebook / YouTube) we can wire in; for now `publish()`
just marks the video and records the intent.
"""

from __future__ import annotations

from datetime import datetime, timezone

from .. import db


def publish(video_id: str, platforms: list[str]) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    with db.connect() as conn:
        conn.execute(
            "UPDATE videos SET published_at = ?, platforms = ? WHERE id = ?",
            (now, ",".join(platforms), video_id),
        )
    return {"video_id": video_id, "platforms": platforms, "published_at": now,
            "status": "recorded (upload not yet implemented)"}
