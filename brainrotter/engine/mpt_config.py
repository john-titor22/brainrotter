"""Generate the vendored engine's config.toml from Brainrotter settings.

MoneyPrinterTurbo reads ``vendor/MoneyPrinterTurbo/config.toml`` once, at import
of ``app.config.config``. So this must run *before* anything imports the engine.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import toml

from ..config import PROJECT_ROOT, Settings

ENGINE_ROOT = PROJECT_ROOT / "vendor" / "MoneyPrinterTurbo"
ENGINE_CONFIG = ENGINE_ROOT / "config.toml"


def _resolve_ffmpeg(settings: Settings) -> str:
    if settings.ffmpeg_bin and Path(settings.ffmpeg_bin).is_file():
        return settings.ffmpeg_bin
    found = shutil.which(settings.ffmpeg_bin or "ffmpeg")
    return found or ""


def write_engine_config(settings: Settings) -> Path:
    """Render config.toml for the engine. Idempotent; safe to call every run."""
    if not ENGINE_ROOT.is_dir():
        raise FileNotFoundError(
            f"Vendored engine not found at {ENGINE_ROOT}. "
            "Run: git clone --depth 1 https://github.com/harry0703/MoneyPrinterTurbo.git "
            "vendor/MoneyPrinterTurbo"
        )

    device = settings.whisper.device
    if device == "auto":
        try:
            import ctranslate2  # noqa: F401  (bundled with faster-whisper)

            device = "cuda" if _cuda_available() else "cpu"
        except Exception:
            device = "cpu"

    cfg = {
        "project_name": "Brainrotter Engine",
        "listen_host": "127.0.0.1",
        "listen_port": 8080,
        "log_level": "WARNING",
        "app": {
            # We feed our own script + local materials, so no LLM / stock keys needed.
            "video_source": "local",
            "subtitle_provider": "edge",   # word timings from TTS; no model download
            "ffmpeg_path": _resolve_ffmpeg(settings),
            "enable_redis": False,
            "max_concurrent_tasks": 3,
            "hide_config": True,
            "disable_auto_update": True,
        },
        "whisper": {
            "model_size": settings.whisper.model,
            "device": device,
            "compute_type": settings.whisper.compute_type,
        },
        "ui": {"hide_log": True},
    }

    ENGINE_CONFIG.write_text(toml.dumps(cfg), encoding="utf-8")
    return ENGINE_CONFIG


def _cuda_available() -> bool:
    try:
        from ctranslate2 import get_cuda_device_count

        return get_cuda_device_count() > 0
    except Exception:
        return False
