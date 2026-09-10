"""Cut a local movie file into a silent 9:16 montage + transcribe its dialogue.

Used by the ``movie_recap`` format (via its ``render_video`` hook). No network:
faster-whisper runs locally (the model auto-downloads once, ~150 MB for "base").
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from ..config import get_settings

VIDEO_EXTS = {".mp4", ".mkv", ".webm", ".mov", ".m4v", ".avi"}


class MovieError(RuntimeError):
    pass


def _ff() -> str:
    return shutil.which(get_settings().ffmpeg_bin) or "ffmpeg"


def _ffprobe() -> str:
    return shutil.which("ffprobe") or _ff().replace("ffmpeg", "ffprobe")


def probe_duration(path: Path) -> float:
    try:
        out = subprocess.run(
            [_ffprobe(), "-v", "quiet", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=60,
        ).stdout.strip()
        return float(out) if out else 0.0
    except Exception:
        return 0.0


def find_movie(topic: str) -> Path | None:
    """Fuzzy-match a file in ``assets/movies/`` against the topic."""
    root = get_settings().movies_path
    files = [p for p in root.rglob("*") if p.suffix.lower() in VIDEO_EXTS]
    if not files:
        return None
    want = re.sub(r"[^a-z0-9]+", "", (topic or "").lower())
    if not want:
        return files[0]

    def norm(p: Path) -> str:
        return re.sub(r"[^a-z0-9]+", "", p.stem.lower())

    exact = [p for p in files if norm(p) == want]
    if exact:
        return exact[0]
    part = [p for p in files if want in norm(p) or norm(p) in want]
    if part:
        return max(part, key=lambda p: len(norm(p)))
    # token overlap
    wt = set(re.findall(r"[a-z0-9]+", (topic or "").lower()))
    scored = sorted(files, key=lambda p: len(wt & set(re.findall(r"[a-z0-9]+", p.stem.lower()))),
                    reverse=True)
    best = scored[0]
    return best if wt & set(re.findall(r"[a-z0-9]+", best.stem.lower())) else None


def extract_slice(movie: Path, start: float, end: float, out: Path) -> Path:
    """The movie between ``start`` and ``end`` seconds, re-encoded (keeps audio,
    for transcription)."""
    dur = max(1.0, end - start)
    cmd = [
        _ff(), "-y", "-ss", f"{start:.2f}", "-i", str(movie), "-t", f"{dur:.2f}",
        "-map", "0:v:0", "-map", "0:a:0?", "-c:v", "libx264", "-preset", "veryfast",
        "-crf", "24", "-vf", "scale=-2:720", "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart", str(out),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=1200)
    except subprocess.CalledProcessError as exc:
        raise MovieError(f"slice extract failed: {(exc.stderr or '')[-400:]}") from exc
    if not out.is_file() or out.stat().st_size < 50_000:
        raise MovieError("slice extract produced nothing")
    return out


def transcribe(path: Path, model_name: str = "base") -> str:
    """Local dialogue transcript, timestamped by minute. Empty string if
    faster-whisper isn't available or the slice is silent."""
    try:
        from faster_whisper import WhisperModel
    except Exception:
        return ""
    try:
        model = WhisperModel(model_name, device="cpu", compute_type="int8")
        segments, _info = model.transcribe(str(path), vad_filter=True,
                                           condition_on_previous_text=False)
        lines: list[str] = []
        for seg in segments:
            txt = (seg.text or "").strip()
            if txt:
                m, s = divmod(int(seg.start), 60)
                lines.append(f"[{m}:{s:02d}] {txt}")
        return "\n".join(lines)
    except Exception as exc:  # noqa: BLE001
        raise MovieError(f"transcription failed: {exc}") from exc


def _scene_cuts(path: Path, threshold: float) -> list[float]:
    """Timestamps (seconds, relative to ``path``) where the shot changes."""
    try:
        r = subprocess.run(
            [_ff(), "-i", str(path), "-vf", f"select='gt(scene,{threshold})',showinfo",
             "-f", "null", "-"],
            capture_output=True, text=True, timeout=900,
        )
    except subprocess.TimeoutExpired:
        return []
    cuts = [float(m) for m in re.findall(r"pts_time:([0-9.]+)", r.stderr or "")]
    return sorted(set(round(c, 2) for c in cuts))


def _pad_filter(blur: bool, w: int, h: int, fps: int) -> str:
    if blur:
        return (
            f"split=2[bg][fg];"
            f"[bg]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"
            f"boxblur=22:2,eq=brightness=-0.06[bg2];"
            f"[fg]scale={w}:-2:force_original_aspect_ratio=decrease[fg2];"
            f"[bg2][fg2]overlay=(W-w)/2:(H-h)/2,setsar=1,fps={fps},format=yuv420p"
        )
    return (f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"
            f"setsar=1,fps={fps},format=yuv420p")


def build_montage(slice_path: Path, out: Path, target_seconds: float) -> Path:
    """Silent 9:16 montage of ``target_seconds`` — a slice of each detected shot,
    in order, cut hard. Falls back to even chunks if scene detection is thin."""
    settings = get_settings()
    mc = settings.movie
    w, h = settings.video.width, settings.video.height
    fps = settings.video.fps
    ff = _ff()
    sdur = probe_duration(slice_path)
    if sdur <= 1:
        raise MovieError("montage: slice has no duration")

    cuts = _scene_cuts(slice_path, mc.scene_threshold)
    starts = [c for c in cuts if 0.5 < c < sdur - mc.shot_seconds]
    if len(starts) < 4:  # thin detection — even chunks instead
        n = max(6, int(target_seconds / mc.shot_seconds))
        step = sdur / (n + 1)
        starts = [round(step * (i + 1), 2) for i in range(n)]

    tmp = out.parent / "_mont"
    if tmp.exists():
        shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True, exist_ok=True)
    vf = _pad_filter(mc.blur_pad, w, h, fps)

    segs: list[Path] = []
    got = 0.0
    for i, st in enumerate(starts):
        if got >= target_seconds:
            break
        d = min(mc.shot_seconds, max(1.5, sdur - st))
        seg = tmp / f"s{i:03d}.mp4"
        cmd = [ff, "-y", "-ss", f"{st:.2f}", "-i", str(slice_path), "-t", f"{d:.2f}",
               "-an", "-vf", vf, "-c:v", "libx264", "-preset", "veryfast",
               "-crf", "22", "-pix_fmt", "yuv420p", str(seg)]
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=180)
        except subprocess.CalledProcessError:
            continue
        if seg.is_file() and seg.stat().st_size > 8_000:
            segs.append(seg)
            got += d
    if not segs:
        raise MovieError("montage: no shots could be cut")

    listf = tmp / "list.txt"
    listf.write_text("".join(f"file '{s.resolve().as_posix()}'\n" for s in segs), encoding="utf-8")
    cmd = [ff, "-y", "-f", "concat", "-safe", "0", "-i", str(listf),
           "-t", f"{target_seconds + 1.0:.2f}", "-c:v", "libx264", "-preset", "veryfast",
           "-crf", "22", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out)]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=600)
    except subprocess.CalledProcessError as exc:
        raise MovieError(f"montage concat failed: {(exc.stderr or '')[-400:]}") from exc
    shutil.rmtree(tmp, ignore_errors=True)
    if not out.is_file() or out.stat().st_size < 20_000:
        raise MovieError("montage produced no output")
    return out
