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


def _stage_bgm(path: str) -> str:
    """MPT only accepts a bgm file that lives in storage/bgm/ or resource/songs/.
    Mirror the Director's chosen track into storage/bgm/ and return its basename."""
    src = Path(path).resolve()
    if not src.is_file():
        return ""
    staging = ENGINE_ROOT / "storage" / "bgm"
    staging.mkdir(parents=True, exist_ok=True)
    st = src.stat()
    dst = staging / f"{abs(hash((str(src), st.st_size))) & 0xFFFFFFFF:08x}{src.suffix.lower()}"
    if not dst.exists():
        try:
            dst.hardlink_to(src)
        except (OSError, NotImplementedError):
            shutil.copy2(src, dst)
    return dst.name


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
        from app.services import voice as mpt_voice  # type: ignore
        from app.utils import utils as mpt_utils  # type: ignore
    except Exception as exc:  # pragma: no cover - env/install problem
        raise EngineError(f"failed to import vendored engine: {exc}") from exc

    settings = get_settings()
    out_dir = out_dir or settings.out_path
    out_dir.mkdir(parents=True, exist_ok=True)

    engine_task_id = uuid.uuid4().hex
    task_dir = Path(mpt_utils.task_dir(engine_task_id))
    task_dir.mkdir(parents=True, exist_ok=True)

    voice_preview = None
    concat_mode = "random"
    clip_duration = max(2, plan.clip_duration)

    if plan.portrait and plan.background_source == "local":
        # anime_figure: lip-synced talking head on top, gameplay on the bottom.
        material_names, voice_preview = _talking_head_material(
            plan, settings, task_dir, mpt_voice
        )
        concat_mode = "sequential"           # the composite must play straight
        clip_duration = 999                  # ...as one piece, not cut up
    else:
        material_names = []
        if plan.background_source == "local":
            material_names = _stage_materials(plan.background_clips)
            if not material_names:
                raise EngineError(
                    "no background clips found — drop gameplay loops into "
                    f"{settings.backgrounds_path} or set background_source='pexels'"
                )

    bgm_name = _stage_bgm(plan.music_file) if plan.music_file else ""

    cap = plan.caption
    params = VideoParams(
        video_subject=plan.subject or "brainrotter",
        video_script=plan.script_text.strip(),
        video_terms=[plan.subject] if plan.subject else ["gameplay"],
        video_source=plan.background_source,
        video_materials=[MaterialInfo(provider="local", url=n) for n in material_names] or None,
        video_aspect=plan.aspect,
        video_clip_duration=clip_duration,
        video_concat_mode=concat_mode,
        video_count=1,
        voice_name=plan.voice_name,
        voice_rate=plan.voice_rate,
        voice_volume=1.0 if voice_preview else plan.voice_volume,
        bgm_type=(plan.music or "random") if (plan.music or bgm_name) else "",
        bgm_file=bgm_name,
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

    t0 = time.time()
    result = mpt_task.start(engine_task_id, params, stop_at="video",
                            voice_preview=voice_preview)
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


def _talking_head_material(plan: RenderPlan, settings, task_dir: Path, mpt_voice):
    """Build the top(talking head)/bottom(gameplay) composite for anime_figure.

    Returns (material_names, voice_preview) — the composite is staged as MPT's
    single local material, and voice_preview hands MPT our pre-made TTS + its
    word timings so it still burns the pop captions.
    """
    from ..avatar import talking_head

    audio = task_dir / "audio.mp3"
    sub_maker = mpt_voice.tts(
        text=plan.script_text.strip(),
        voice_name=mpt_voice.parse_voice_name(plan.voice_name),
        voice_rate=plan.voice_rate,
        voice_file=str(audio),
    )
    if sub_maker is None or not audio.is_file():
        raise EngineError("TTS failed while preparing the talking head")
    duration = float(mpt_voice.get_audio_duration(str(audio)))

    head = talking_head(plan.portrait, audio, task_dir / "head.mp4")
    split = _compose_split(head, plan.background_clips, duration, task_dir, settings)

    voice_preview = {
        "script": plan.script_text.strip(),
        "voice_name": plan.voice_name,
        "voice_rate": plan.voice_rate,
        "voice_volume": 1.0,
        "audio_file": str(audio),
        "duration": duration,
        "sub_maker": sub_maker,
    }
    return _stage_materials([str(split)]), voice_preview


def _compose_split(head: Path, gameplay: list[str], duration: float,
                   task_dir: Path, settings) -> Path:
    ff = shutil.which(settings.ffmpeg_bin) or "ffmpeg"
    w, h = settings.video.width, settings.video.height
    top_h = (int(h * settings.avatar.top_fraction) // 2) * 2
    bot_h = h - top_h
    out = task_dir / "split.mp4"

    bottom = next((g for g in gameplay if Path(g).is_file()), None)
    if not bottom:
        # no gameplay — just letterbox the head to full frame
        cmd = [ff, "-y", "-stream_loop", "-1", "-i", str(head), "-t", f"{duration:.2f}",
               "-vf", f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},fps=30,setsar=1",
               "-an", "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", str(out)]
    else:
        cmd = [
            ff, "-y",
            "-stream_loop", "-1", "-i", str(head),
            "-stream_loop", "-1", "-i", str(bottom),
            "-filter_complex",
            (f"[0:v]scale={w}:{top_h}:force_original_aspect_ratio=increase,"
             f"crop={w}:{top_h},setsar=1[t];"
             f"[1:v]scale={w}:{bot_h}:force_original_aspect_ratio=increase,"
             f"crop={w}:{bot_h},setsar=1[b];"
             f"[t][b]vstack=inputs=2,fps=30[v]"),
            "-map", "[v]", "-t", f"{duration:.2f}",
            "-an", "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
            str(out),
        ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=600)
    except subprocess.CalledProcessError as exc:
        raise EngineError(f"split-screen compose failed: {(exc.stderr or '')[-400:]}") from exc
    return out


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
