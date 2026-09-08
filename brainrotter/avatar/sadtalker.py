"""Drive SadTalker (isolated venv) to make a talking-head clip."""

from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path

from ..config import PROJECT_ROOT, get_settings

SADTALKER_DIR = PROJECT_ROOT / "tools" / "sadtalker"
SADTALKER_PY = SADTALKER_DIR / ".venv" / "Scripts" / "python.exe"
CHECKPOINTS = SADTALKER_DIR / "checkpoints"


class AvatarError(RuntimeError):
    pass


def is_installed() -> bool:
    return (
        SADTALKER_PY.is_file()
        and (SADTALKER_DIR / "inference.py").is_file()
        and CHECKPOINTS.is_dir()
        and (any(CHECKPOINTS.glob("*.safetensors")) or any(CHECKPOINTS.glob("*.pth")))
    )


def available() -> bool:
    return get_settings().avatar.enabled and is_installed()


def _to_wav(audio: Path, work: Path) -> Path:
    ff = shutil.which(get_settings().ffmpeg_bin) or "ffmpeg"
    wav = work / "narration.wav"
    subprocess.run(
        [ff, "-y", "-i", str(audio), "-ar", "16000", "-ac", "1", str(wav)],
        check=True, capture_output=True, timeout=120,
    )
    return wav


def _probe_duration(path: Path) -> float:
    ff = shutil.which(get_settings().ffmpeg_bin) or "ffmpeg"
    probe = shutil.which("ffprobe") or ff.replace("ffmpeg", "ffprobe")
    try:
        out = subprocess.run(
            [probe, "-v", "quiet", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=30,
        ).stdout.strip()
        return float(out) if out else 0.0
    except Exception:
        return 0.0


def talking_head(portrait: str | Path, audio: str | Path, out_path: str | Path,
                 *, timeout: int = 600) -> Path:
    """portrait image + audio -> lip-synced mp4 at out_path."""
    if not is_installed():
        raise AvatarError("SadTalker is not installed - run `brainrotter avatar-setup`")

    settings = get_settings()
    portrait, audio, out_path = Path(portrait), Path(audio), Path(out_path)
    work = settings.cache_path / "avatar" / f"{int(time.time())}"
    work.mkdir(parents=True, exist_ok=True)
    result_dir = work / "out"
    result_dir.mkdir(exist_ok=True)

    wav = _to_wav(audio, work)
    av = settings.avatar
    cmd = [
        str(SADTALKER_PY), "inference.py",
        "--source_image", str(portrait.resolve()),
        "--driven_audio", str(wav.resolve()),
        "--result_dir", str(result_dir.resolve()),
        "--preprocess", av.preprocess,        # crop | resize | full
        "--size", str(av.size),               # 256 | 512
        "--still",                            # less head sway, cleaner
    ]
    if av.device == "cpu":
        cmd.append("--cpu")
    if av.enhancer and av.enhancer.lower() not in ("none", "off", ""):
        cmd += ["--enhancer", av.enhancer]    # gfpgan — sharper but ~3x slower

    try:
        subprocess.run(cmd, cwd=SADTALKER_DIR, check=True, capture_output=True,
                       text=True, timeout=timeout)
    except subprocess.CalledProcessError as exc:
        tail = (exc.stderr or exc.stdout or "")[-600:]
        raise AvatarError(f"SadTalker failed: {tail}") from exc
    except subprocess.TimeoutExpired as exc:
        raise AvatarError(f"SadTalker timed out after {timeout}s") from exc

    # SadTalker leaves scratch files behind (temp_*, *_enhanced partials that can
    # be truncated). Take a real, non-temp output with a sane duration — the
    # "_full" one (portrait + audio muxed) when present.
    cands = [p for p in result_dir.rglob("*.mp4") if not p.name.startswith("temp_")]
    cands.sort(key=lambda p: (("full" not in p.name), -p.stat().st_mtime))
    pick = next((p for p in cands if _probe_duration(p) > 0.5), None)
    if pick is None:
        raise AvatarError("SadTalker produced no usable output")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists():
        out_path.unlink()
    shutil.move(str(pick), out_path)
    shutil.rmtree(work, ignore_errors=True)
    return out_path
