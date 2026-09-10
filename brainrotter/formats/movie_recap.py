"""Movie recap: a whole film cut into shorts, muted, with a brainrot narrator.

Drop a movie in ``assets/movies/``. Then:

    brainrotter run -f movie_recap -t "<movie name>" --series

The series planner splits the film by time into Part 1..N; each part's
``render_video`` extracts that chunk, transcribes the dialogue locally
(faster-whisper), has the LLM write a fast recap narration, and stacks the
narration + word-pop captions over a silent 9:16 montage of the chunk's shots.

Copyright: a movie is someone's IP. The recap/commentary format lives in a grey
zone. Same deal as the footage sourcing — your call.
"""

from __future__ import annotations

import math
import re
from pathlib import Path

from ..config import get_settings
from ..models import Brief, CaptionStyle, RenderPlan, Script, ScriptBeat
from ..writer import llm

ID = "movie_recap"
NAME = "Movie Recap"
DESCRIPTION = "A local movie cut into muted 9:16 shorts with a brainrot narrator recapping each chunk."
SIGNAL_KINDS = ["topic"]
VOICE_TAGS = ("narrator", "storytime", "hype")
MUSIC_MOODS = ("dramatic", "tense", "epic", "eerie", "nostalgic")
DEFAULT_VISUAL_TREATMENT = "footage"      # n/a — render_video owns visuals
SUBJECT_KIND = "scene"
REQUIRES_EXPLICIT = True                  # needs a movie file + an explicit topic; Director never auto-picks it


def _mmss(sec: float) -> str:
    m, s = divmod(int(sec), 60)
    return f"{m}:{s:02d}"


def _title_from_file(p: Path) -> str:
    name = re.sub(r"[._]+", " ", p.stem)
    name = re.sub(r"\b(1080p|720p|2160p|4k|bluray|web ?dl|x264|x265|hevc|remux|hdr)\b",
                  "", name, flags=re.I)
    name = re.sub(r"\b(19|20)\d{2}\b.*$", "", name).strip(" -")
    return name.title() or p.stem


# --- series planner (called by series.create) -----------------------------

def plan_series(*, topic: str, target_parts: int | None = None,
                language: str = "en") -> dict:
    from ..engine import movie as M

    mv = M.find_movie(topic)
    if not mv:
        raise RuntimeError(
            f"movie_recap: no file in {get_settings().movies_path} matches "
            f"{topic!r}. Drop the movie there (e.g. 'The Matrix.mp4')."
        )
    dur = M.probe_duration(mv)
    if dur < 60:
        raise RuntimeError(f"movie_recap: {mv.name} is only {dur:.0f}s — not a movie?")

    cfg = get_settings().movie
    natural = max(3, math.ceil(dur / cfg.movie_seconds_per_part))
    n = min(cfg.max_parts, target_parts or natural)
    chunk = dur / n
    title = _title_from_file(mv)

    parts = []
    for i in range(n):
        s, e = round(i * chunk, 1), round(min(dur, (i + 1) * chunk), 1)
        parts.append({
            "n": i + 1,
            "goal": f"Recap {_mmss(s)}–{_mmss(e)} of {title}",
            "hook": "",
            "cliffhanger": "" if i == n - 1 else "and then it gets worse",
            "broll": [],
            "start": s, "end": e, "movie_title": title,
        })
    return {
        "title": f"{title} — the whole movie in {n} parts",
        "premise": f"A shot-by-shot recap of {title}, told in order across {n} shorts.",
        "characters": [],
        "mood": "dramatic",
        "parts": parts,
        "movie_path": str(mv.resolve()),
    }


# --- the render (owns the whole video) ------------------------------------

_NARR_SYS = (
    "You are a fast movie-recap narrator — the TikTok/Shorts kind that talks over "
    "muted movie clips ('so this guy walks in and pulls a gun', 'and THAT'S when "
    "she realises it was her brother the whole time'). You get the DIALOGUE "
    "TRANSCRIPT of one chunk of a film plus what you covered last time.\n"
    "Write the narration for THIS chunk, in order. Present tense, momentum, a bit "
    "of attitude. Every line is a COMPLETE spoken sentence (8-18 words) — never a "
    "bullet point or a fragment. Explain the plot a viewer needs; paraphrase, "
    "don't quote. Connect the beats ('but then', 'so', 'which means'). End on a "
    "mini-cliffhanger unless it's the final part.\n"
    "Example line: 'John shows up expecting a payout, but Marcus tells him the "
    "account got frozen that morning — the money was never real.'\n"
    "Return ONLY JSON: {\"title\": str, \"beats\": [str, ...], \"hashtags\": "
    "[str,...]} — 8-16 full sentences in 'beats', first is the hook, ~2.5 "
    "words/second total."
)


def _write_narration(brief: Brief, transcript: str, target_seconds: int) -> Script:
    s = brief.series
    part_line = (f"This is Part {s.part} of {s.part_count}."
                 + ("  This is the FINAL part — wrap the movie up."
                    if s and s.is_finale else "")) if s else ""
    prev = f"\nLast time you covered:\n{s.story_so_far}" if s and s.story_so_far else ""
    title = (s.part_meta.get("movie_title") if s else None) or brief.topic
    budget = max(20, int(target_seconds * 2.5))

    user = (
        f"Film: {title}\n{part_line}{prev}\n\n"
        f"Aim for about {target_seconds} seconds (~{budget} words total).\n\n"
        f"Dialogue transcript of this chunk:\n{transcript or '(mostly silent — action scene)'}"
    )
    if llm.available():
        try:
            raw = llm.complete_json(_NARR_SYS, user, max_tokens=1600,
                                    language=brief.language)
            beats = []
            for b in raw.get("beats", []):
                txt = (b.get("narration") or b.get("text") or "") if isinstance(b, dict) else b
                txt = str(txt).strip()
                if txt:
                    beats.append(ScriptBeat(narration=txt))
            if len(beats) >= 3:
                script = Script(
                    title=str(raw.get("title", "")).strip() or f"{title} recap",
                    beats=beats,
                    hashtags=[h.lstrip("#") for h in raw.get("hashtags", [])][:6]
                    or ["movie", "recap", "film", "fyp"],
                )
                if get_settings().writer.coherence_pass:
                    from ..writer.scriptwriter import _coherence_pass

                    _coherence_pass(script, brief)
                return script
        except Exception:
            pass
    # fallback: read the transcript itself as the "recap"
    lines = [ln.split("] ", 1)[-1] for ln in (transcript or "").splitlines() if "] " in ln]
    beats = [ScriptBeat(narration=x) for x in lines[:12]] or [
        ScriptBeat(narration=f"Here's what happens next in {title}.")]
    return Script(title=f"{title} recap", beats=beats,
                  hashtags=["movie", "recap", "fyp"])


def _plan(brief: Brief, script: Script) -> RenderPlan:
    from .base import finalize_plan, resolve_voice

    style = brief.style or {}
    voice = resolve_voice(style, brief, *VOICE_TAGS)
    plan = RenderPlan(
        subject=script.title or brief.topic,
        script_text=script.narration_text,
        voice_name=voice.name,
        voice_rate=float(style.get("voice_rate", voice.default_rate)),
        caption=CaptionStyle(position="center", font_size=int(style.get("font_size", 84)),
                             word_by_word=True, animation="pop_spring"),
        music=style.get("music", "random"),
        music_volume=float(style.get("music_volume", get_settings().movie.music_volume)),
    )
    return finalize_plan(plan, brief, style)


def render_video(brief: Brief, ctx) -> dict:
    import time

    from ..engine import movie as M
    from ..engine import storyboard as SB

    t0 = time.time()
    cfg = ctx.settings.movie
    meta = brief.series.part_meta if brief.series else {}
    mpath = Path(meta.get("movie_path") or "")
    start, end = float(meta.get("start", 0.0)), float(meta.get("end", 0.0))
    if not mpath.is_file() or end <= start:
        raise RuntimeError("movie_recap needs a series part with a movie time range "
                           "(run it with --series)")

    work = ctx.workdir
    sl = M.extract_slice(mpath, start, end, work / "slice.mp4")
    transcript = M.transcribe(sl, cfg.whisper_model)

    script = _write_narration(brief, transcript, cfg.video_seconds_per_part)
    plan = _plan(brief, script)

    audio = work / "narration.mp3"
    dur = SB.tts_narration(script.narration_text, plan, audio)

    montage = M.build_montage(sl, work / "montage.mp4", target_seconds=dur)

    out = ctx.settings.out_path / f"{ctx.job_id}.mp4"
    with ctx.render_lock():
        SB.finish(montage, audio, dur, script.narration_text, plan, out, work,
                  loop_video=True)
    try:
        import shutil
        shutil.rmtree(work, ignore_errors=True)
    except Exception:
        pass
    return {"path": str(out), "duration": round(dur, 2),
            "seconds": round(time.time() - t0, 1), "script": script.model_dump()}


# --- Format protocol stubs (never reached — render_video owns everything) ---

def writer_system_prompt() -> str:
    return _NARR_SYS


def writer_user_prompt(brief: Brief) -> str:
    return f"Recap the film: {brief.topic}"


def parse_script(raw: dict) -> Script:
    return Script(title=str(raw.get("title", "Movie Recap")),
                  beats=[ScriptBeat(narration=str(b.get("narration", "")).strip())
                         for b in raw.get("beats", []) if b.get("narration")])


def build_plan(brief: Brief, script: Script, background_clips: list[str]) -> RenderPlan:
    return _plan(brief, script)
