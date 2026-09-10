"""Fast native renderer for AI-still 'storyboard' videos — ffmpeg only.

MoneyPrinterTurbo's MoviePy caption burn is ~8 minutes on a modest box. For the
generated-visuals formats we don't need it: the video is just Ken-Burns'd stills
held per beat, word-pop captions, narration and ducked music — all of which
ffmpeg does in seconds.

``render()`` returns ``{"path", "duration", "seconds"}`` — the same shape the
MPT path returns — so ``orchestrator.produce`` can swap it in transparently.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import time
from pathlib import Path

from ..config import get_settings
from ..models import CaptionStyle, RenderPlan, Script
from .mpt_config import ENGINE_ROOT
from .mpt_engine import EngineError, _bootstrap, _ken_burns

_RTL_LANGS = {"ar", "ary"}


def _font_family(font_name: str) -> str:
    """The family name libass needs, read straight from the .ttf (MPT stores
    caption fonts by filename)."""
    path = ENGINE_ROOT / "resource" / "fonts" / font_name
    try:
        from PIL import ImageFont

        fam, _style = ImageFont.truetype(str(path), 20).getname()
        if fam:
            return fam
    except Exception:
        pass
    return font_name.rsplit(".", 1)[0]


def _ff() -> str:
    return shutil.which(get_settings().ffmpeg_bin) or "ffmpeg"


def _probe_duration(path: Path) -> float:
    probe = shutil.which("ffprobe") or "ffprobe"
    try:
        out = subprocess.run(
            [probe, "-v", "quiet", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=30,
        ).stdout.strip()
        return float(out) if out else 0.0
    except Exception:
        return 0.0


def _tts(text: str, plan: RenderPlan, out_mp3: Path) -> float:
    """Narration audio. Real Darija XTTS voice for ``ary`` when installed,
    otherwise MPT's edge-tts. Returns the audio duration in seconds."""
    if plan.language in _RTL_LANGS:
        try:
            from .. import darija_tts

            if darija_tts.available():
                wav = out_mp3.with_suffix(".dtts.wav")
                got = darija_tts.synthesize(text, wav,
                                            temperature=0.7 if plan.voice_rate <= 1.0 else 0.75)
                if got:
                    ff = _ff()
                    rate = max(0.85, min(1.15, plan.voice_rate))
                    subprocess.run(
                        [ff, "-y", "-i", str(got), "-filter:a", f"atempo={rate:.3f}",
                         "-c:a", "libmp3lame", "-q:a", "3", str(out_mp3)],
                        capture_output=True, timeout=180,
                    )
                    got.unlink(missing_ok=True)
                    d = _probe_duration(out_mp3)
                    if d > 0:
                        return d
        except Exception as exc:  # noqa: BLE001
            import logging
            logging.getLogger("brainrotter.engine").warning(
                "darija tts failed, using edge-tts: %s", exc)

    _bootstrap()
    from app.services import voice as mpt_voice  # type: ignore

    sm = mpt_voice.tts(
        text=text,
        voice_name=mpt_voice.parse_voice_name(plan.voice_name),
        voice_rate=plan.voice_rate,
        voice_file=str(out_mp3),
    )
    if sm is None or not out_mp3.is_file():
        raise EngineError("TTS failed for storyboard narration")
    dur = _probe_duration(out_mp3)
    if dur <= 0:
        raise EngineError("TTS produced an unreadable audio file")
    return dur


# --- captions --------------------------------------------------------------

def _ass_color(hex_color: str) -> str:
    """#RRGGBB  ->  &H00BBGGRR  (ASS is AABBGGRR, AA=00 opaque)."""
    m = re.fullmatch(r"#?([0-9a-fA-F]{6})", (hex_color or "").strip())
    if not m:
        return "&H00FFFFFF"
    r, g, b = m.group(1)[0:2], m.group(1)[2:4], m.group(1)[4:6]
    return f"&H00{b}{g}{r}".upper()


def _word_times(text: str, duration: float, rtl: bool) -> list[tuple[str, float, float]]:
    """One (word, start, end) per word — time split by character weight (what
    MPT does when edge-tts gives no per-word boundaries). Punctuation-only
    tokens are folded into the previous word so nothing flashes."""
    raw = [w for w in re.split(r"\s+", (text or "").strip()) if w]
    if not raw:
        return []
    if rtl:
        try:
            import arabic_reshaper
            from bidi.algorithm import get_display

            raw = [get_display(arabic_reshaper.reshape(w)) for w in raw]
        except Exception:
            pass
    lengths = [max(1, len(w)) for w in raw]
    total = sum(lengths)
    span = max(0.1, duration - 0.15)
    out: list[tuple[str, float, float]] = []
    t = 0.0
    for w, ln in zip(raw, lengths):
        seg = span * ln / total
        out.append((w, round(t, 3), round(t + seg, 3)))
        t += seg
    return out


def _build_ass(words: list[tuple[str, float, float]], cap: CaptionStyle,
               width: int, height: int) -> str:
    family = _font_family(cap.font_name)
    align = {"top": 8, "center": 5, "bottom": 2}.get(cap.position, 5)
    margin_v = int(height * (0.12 if align == 2 else 0.0)) or 40
    fs = int(cap.font_size)
    primary = _ass_color(cap.fore_color)
    outline = _ass_color(cap.stroke_color)
    ow = max(1, round(cap.stroke_width))

    head = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {width}", f"PlayResY: {height}",
        "WrapStyle: 2", "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, "
        "ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
        "MarginL, MarginR, MarginV, Encoding",
        f"Style: pop,{family},{fs},{primary},{primary},{outline},&H64000000,"
        f"-1,0,0,0,100,100,0,0,1,{ow},0,{align},60,60,{margin_v},1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]

    def ts(s: float) -> str:
        h = int(s // 3600)
        m = int((s % 3600) // 60)
        sec = s % 60
        return f"{h:d}:{m:02d}:{sec:05.2f}"

    lines = []
    for i, (w, start, end) in enumerate(words):
        end = max(end, start + 0.12)
        if cap.word_by_word and i + 1 < len(words):
            end = max(end, words[i + 1][1])          # hold until the next word
        pop = (r"{\fad(40,0)\fscx62\fscy62\t(0,110,\fscx100\fscy100)}"
               if cap.animation == "pop_spring" else r"{\fad(40,0)}")
        text = w.replace("{", "(").replace("}", ")").replace("\\", "/")
        lines.append(f"Dialogue: 0,{ts(start)},{ts(end)},pop,,0,0,0,,{pop}{text}")
    return "\n".join(head + lines) + "\n"


# --- assembly -------------------------------------------------------------

def _ass_path_for_filter(p: Path) -> str:
    """ffmpeg filtergraph escaping for a Windows path inside ass=..."""
    s = str(p.resolve())
    s = s.replace("\\", "/").replace(":", "\\:")
    return s


def render(*, script: Script, images: list[str], weights: list[float],
           plan: RenderPlan, job_id: str, out_dir: Path) -> dict:
    t0 = time.time()
    settings = get_settings()
    w, h = settings.video.width, settings.video.height
    fps = settings.video.fps
    ff = _ff()

    imgs = [i for i in images if Path(i).is_file()]
    if len(imgs) < 1:
        raise EngineError("storyboard: no usable images")
    if len(weights) != len(imgs) or sum(weights) <= 0:
        weights = [1.0] * len(imgs)

    work = settings.cache_path / "storyboard" / job_id
    if work.exists():
        shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)

    audio = work / "narration.mp3"
    dur = _tts(script.narration_text, plan, audio)

    kb = _ken_burns(imgs, weights, dur, work / "kb.mp4", settings)

    out = out_dir / f"{job_id}.mp4"
    finish(kb, audio, dur, script.narration_text, plan, out, work)
    shutil.rmtree(work, ignore_errors=True)
    return {"path": str(out), "duration": round(dur, 2), "seconds": round(time.time() - t0, 1)}


def tts_narration(text: str, plan: RenderPlan, out_mp3: Path) -> float:
    """Public: narration mp3 + its duration (for callers that build their own
    video track, e.g. movie_recap)."""
    return _tts(text, plan, out_mp3)


def _one_clip_bg(clip: str, out: Path, dur: float, settings, *, seed: int = 0) -> Path:
    """ONE gameplay clip, cropped to 9:16, silent, exactly ``dur`` seconds — from
    a random start offset if the clip is long enough, otherwise looped. No cuts,
    no switching between clips: one continuous background that ends with the
    narration."""
    ff = _ff()
    w, h = settings.video.width, settings.video.height
    fps = settings.video.fps
    src = _probe_duration_any(clip)
    need = dur + 0.3
    stream_loop = "0"
    start = 0.0
    if src > need + 1.0:
        import random as _r

        start = round(_r.Random(seed).uniform(0.0, src - need - 0.5), 2)
    elif src > 1.0:
        stream_loop = "-1"                       # clip shorter than the video — loop it
    vf = (f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"
          f"setsar=1,fps={fps},format=yuv420p")
    cmd = [ff, "-y"]
    if stream_loop != "0":
        cmd += ["-stream_loop", stream_loop]
    cmd += ["-ss", f"{start:.2f}", "-i", str(clip), "-t", f"{need:.2f}",
            "-an", "-vf", vf, "-c:v", "libx264", "-preset", "veryfast",
            "-crf", "23", "-pix_fmt", "yuv420p", str(out)]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=600)
    except subprocess.CalledProcessError as exc:
        raise EngineError(f"gameplay bg prep failed: {(exc.stderr or '')[-400:]}") from exc
    if not out.is_file() or out.stat().st_size < 20_000:
        raise EngineError("gameplay bg prep produced nothing")
    return out


def _probe_duration_any(path) -> float:
    probe = shutil.which("ffprobe") or "ffprobe"
    try:
        o = subprocess.run([probe, "-v", "quiet", "-show_entries", "format=duration",
                            "-of", "csv=p=0", str(path)],
                           capture_output=True, text=True, timeout=30).stdout.strip()
        return float(o) if o else 0.0
    except Exception:
        return 0.0


def render_talking_subject(*, script: Script, hero_image: str, plan: RenderPlan,
                           job_id: str, out_dir: Path) -> dict:
    """object_story: one generated image of the object-with-a-face, lip-synced to
    the narration (SadTalker), full-frame 9:16, captions + music over it."""
    from ..avatar import talking_head

    t0 = time.time()
    settings = get_settings()
    w, h, fps = settings.video.width, settings.video.height, settings.video.fps
    ff = _ff()
    work = settings.cache_path / "storyboard" / job_id
    if work.exists():
        shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)

    audio = work / "narration.mp3"
    dur = _tts(script.narration_text, plan, audio)

    head = talking_head(hero_image, audio, work / "head.mp4",
                        timeout=int(max(300, dur * 12)), enhancer="")
    # SadTalker (preprocess=full) keeps the source aspect — just fit it to 9:16
    face = work / "face.mp4"
    vf = (f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"
          f"setsar=1,fps={fps},format=yuv420p")
    cmd = [ff, "-y", "-i", str(head), "-t", f"{dur + 0.2:.2f}", "-an",
           "-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
           "-pix_fmt", "yuv420p", str(face)]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=300)
    except subprocess.CalledProcessError as exc:
        raise EngineError(f"talking-subject fit failed: {(exc.stderr or '')[-400:]}") from exc

    out = out_dir / f"{job_id}.mp4"
    finish(face, audio, dur, script.narration_text, plan, out, work, loop_video=True)
    shutil.rmtree(work, ignore_errors=True)
    return {"path": str(out), "duration": round(dur, 2), "seconds": round(time.time() - t0, 1)}


def render_over_gameplay(*, script: Script, clip: str, plan: RenderPlan,
                         job_id: str, out_dir: Path, seed: int = 0) -> dict:
    """Native footage render: narration + word-pop captions + ducked music over
    ONE continuous gameplay clip (no mid-video cuts). ffmpeg only."""
    t0 = time.time()
    settings = get_settings()
    work = settings.cache_path / "storyboard" / job_id
    if work.exists():
        shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)

    audio = work / "narration.mp3"
    dur = _tts(script.narration_text, plan, audio)
    bg = _one_clip_bg(clip, work / "bg.mp4", dur, settings, seed=seed)

    out = out_dir / f"{job_id}.mp4"
    finish(bg, audio, dur, script.narration_text, plan, out, work)
    shutil.rmtree(work, ignore_errors=True)
    return {"path": str(out), "duration": round(dur, 2), "seconds": round(time.time() - t0, 1)}


def finish(video: Path, audio: Path, dur: float, narration: str,
           plan: RenderPlan, out: Path, work: Path, *,
           loop_video: bool = False) -> Path:
    """Burn word-pop captions + mix narration and (ducked) music onto a silent
    video track. ffmpeg only."""
    settings = get_settings()
    w, h = settings.video.width, settings.video.height
    fps = settings.video.fps
    ff = _ff()

    rtl = plan.language in _RTL_LANGS or plan.caption.rtl
    words = _word_times(narration, dur, rtl)
    ass = work / "caps.ass"
    ass.write_text(_build_ass(words, plan.caption, w, h), encoding="utf-8")

    music = plan.music_file if plan.music_file and Path(plan.music_file).is_file() else None
    fonts = ENGINE_ROOT / "resource" / "fonts"
    vf = f"[0:v]ass='{_ass_path_for_filter(ass)}':fontsdir='{_ass_path_for_filter(fonts)}'[v]"

    cmd = [ff, "-y"]
    if loop_video:
        cmd += ["-stream_loop", "-1"]
    cmd += ["-i", str(video), "-i", str(audio)]
    if music:
        cmd += ["-stream_loop", "-1", "-i", str(music)]
        vol = float(plan.music_volume or 0.15)
        af = (f"[1:a]volume=1.0[na];"
              f"[2:a]volume={vol:.2f},afade=t=out:st={max(0.0, dur - 1.5):.2f}:d=1.5[bg];"
              f"[na][bg]amix=inputs=2:duration=first:dropout_transition=0[a]")
        cmd += ["-filter_complex", f"{vf};{af}", "-map", "[v]", "-map", "[a]"]
    else:
        cmd += ["-filter_complex", vf, "-map", "[v]", "-map", "1:a"]

    cmd += [
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-pix_fmt", "yuv420p", "-r", str(fps), "-c:a", "aac", "-b:a", "160k",
        "-t", f"{dur + 0.15:.2f}", "-movflags", "+faststart", str(out),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=900)
    except subprocess.CalledProcessError as exc:
        raise EngineError(f"caption/audio mux failed: {(exc.stderr or '')[-500:]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise EngineError("caption/audio mux timed out") from exc
    if not out.is_file() or out.stat().st_size < 20_000:
        raise EngineError("mux produced no output")
    return out
