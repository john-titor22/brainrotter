"""Drive the vendored MoneyPrinterTurbo pipeline in-process.

We call `app.services.task.start()` directly (no HTTP server, no Redis) with a
`VideoParams` built from our `RenderPlan`, then copy the finished MP4 into our
workspace. MPT's own LLM / stock-footage / upload paths are never touched.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

from ..config import get_settings
from ..models import RenderPlan
from .mpt_config import ENGINE_ROOT, write_engine_config

_POSITION_MAP = {"top": "top", "center": "center", "bottom": "bottom"}


def _stage_materials(clips: list[str]) -> list[str]:
    """MPT restricts local materials to its storage/local_videos/ dir. Mirror the
    chosen clips in there (hardlink where possible, else copy) and return the
    basenames MPT expects."""
    staging = ENGINE_ROOT / "storage" / "local_videos"
    staging.mkdir(parents=True, exist_ok=True)
    names: list[str] = []
    for c in clips:
        src = Path(c).resolve()
        if not src.is_file():
            continue
        st = src.stat()
        dst = staging / f"{abs(hash((str(src), st.st_mtime_ns, st.st_size))) & 0xFFFFFFFF:08x}_{src.name}"
        if not dst.exists():
            try:
                dst.hardlink_to(src)
            except (OSError, NotImplementedError):
                shutil.copy2(src, dst)
        names.append(dst.name)
    return names


class EngineError(RuntimeError):
    pass


_bootstrapped = False


def _bootstrap() -> None:
    """Write engine config and put the engine package on sys.path (once)."""
    global _bootstrapped
    if _bootstrapped:
        return
    write_engine_config(get_settings())
    if str(ENGINE_ROOT) not in sys.path:
        sys.path.insert(0, str(ENGINE_ROOT))
    _bootstrapped = True


def render_plan(plan: RenderPlan, *, job_id: str, out_dir: Path | None = None) -> dict:
    """Render one video. Returns {path, duration, engine_task_id, seconds}."""
    _bootstrap()

    # Imports are deferred until after _bootstrap() has written config.toml and
    # extended sys.path — importing app.config.config reads the file immediately.
    try:
        from app.models.schema import MaterialInfo, VideoParams  # type: ignore
        from app.services import task as mpt_task  # type: ignore
        from app.utils import utils as mpt_utils  # type: ignore
    except Exception as exc:  # pragma: no cover - env/install problem
        raise EngineError(f"failed to import vendored engine: {exc}") from exc

    settings = get_settings()
    out_dir = out_dir or settings.out_path
    out_dir.mkdir(parents=True, exist_ok=True)

    material_names: list[str] = []
    if plan.background_source == "local":
        material_names = _stage_materials(plan.background_clips)
        if not material_names:
            raise EngineError(
                "no background clips found — drop gameplay loops into "
                f"{settings.backgrounds_path} or set background_source='pexels'"
            )

    cap = plan.caption
    params = VideoParams(
        video_subject=plan.subject or "brainrotter",
        video_script=plan.script_text.strip(),
        video_terms=[plan.subject] if plan.subject else ["gameplay"],
        video_source=plan.background_source,
        video_materials=[MaterialInfo(provider="local", url=n) for n in material_names] or None,
        video_aspect=plan.aspect,
        video_clip_duration=max(2, plan.clip_duration),
        video_concat_mode="random",   # cut between the supplied clips
        video_count=1,
        voice_name=plan.voice_name,
        voice_rate=plan.voice_rate,
        voice_volume=plan.voice_volume,
        bgm_type=plan.music if plan.music else "",
        bgm_volume=plan.music_volume,
        subtitle_enabled=True,
        subtitle_position=_POSITION_MAP.get(cap.position, "center"),
        subtitle_display_mode="word_by_word" if cap.word_by_word else "sentence",
        subtitle_animation=cap.animation,
        font_name=cap.font_name,
        text_fore_color=cap.fore_color,
        stroke_color=cap.stroke_color,
        stroke_width=cap.stroke_width,
        font_size=cap.font_size,
        n_threads=2,   # MoviePy's multi-threaded writer is crash-prone on Windows
    )

    engine_task_id = uuid.uuid4().hex
    t0 = time.time()
    result = mpt_task.start(engine_task_id, params, stop_at="video")
    elapsed = time.time() - t0

    if not isinstance(result, dict) or not result.get("videos"):
        err = (result or {}).get("error", "unknown engine failure")
        stage = (result or {}).get("failed_stage", "?")
        raise EngineError(f"engine failed at stage '{stage}': {err}")

    src = Path(result["videos"][0])
    if not src.is_file():
        # MPT may return a URL-style path; resolve against its task dir.
        src = Path(mpt_utils.task_dir(engine_task_id)) / src.name
    if not src.is_file():
        raise EngineError(f"engine reported success but output is missing: {src}")

    dst = out_dir / f"{job_id}.mp4"
    _faststart_copy(src, dst)

    return {
        "path": str(dst),
        "duration": float(result.get("audio_duration", 0.0)),
        "engine_task_id": engine_task_id,
        "seconds": round(elapsed, 1),
        "engine_dir": str(Path(mpt_utils.task_dir(engine_task_id))),
    }


def _faststart_copy(src: Path, dst: Path) -> None:
    """Copy the final MP4 with the moov atom moved to the front so browsers can
    stream it (otherwise playback stalls / loops the buffered start until the
    whole file downloads — very visible on a slow connection)."""
    ff = shutil.which(get_settings().ffmpeg_bin) or "ffmpeg"
    try:
        subprocess.run(
            [ff, "-y", "-i", str(src), "-c", "copy", "-movflags", "+faststart", str(dst)],
            check=True, capture_output=True, timeout=120,
        )
        if dst.is_file() and dst.stat().st_size > 0:
            return
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        pass
    shutil.copy2(src, dst)  # fallback: at least deliver the file
