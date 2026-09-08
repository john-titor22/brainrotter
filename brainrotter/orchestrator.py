"""Ties the layers together: Brief -> Script -> RenderPlan -> video."""

from __future__ import annotations

import contextlib
import socket
import time

from . import assets, db, director, trends, writer
from .config import get_settings
from .engine import render_isolated
from .formats import registry
from .models import Brief, JobState, VideoResult
from .writer import llm

# Machine-wide render lock. MoviePy crashes if two renders run at once on
# Windows, so every renderer (the dashboard worker AND `brainrotter run`)
# must hold this before touching the engine.
_RENDER_LOCK_PORT = 47656


@contextlib.contextmanager
def _render_lock(wait: bool = True, poll: float = 3.0):
    s = socket.socket()
    try:
        while True:
            try:
                s.bind(("127.0.0.1", _RENDER_LOCK_PORT))
                s.listen(1)
                break
            except OSError:
                if not wait:
                    raise RuntimeError("another render is in progress")
                time.sleep(poll)
        yield
    finally:
        s.close()


def plan_brief(*, format_id: str | None = None, topic: str | None = None,
               language: str | None = None, voice: str | None = None,
               seed: int | None = None) -> Brief:
    signals = trends.gather(seed=seed)
    return director.decide(signals, format_id=format_id, topic=topic,
                           language=language, voice=voice, seed=seed)


def produce(brief: Brief, *, job_id: str | None = None,
            publish_to: list[str] | None = None) -> VideoResult:
    settings = get_settings()
    job_id = job_id or db.create_job(format_id=brief.format_id, topic=brief.topic)
    timings: dict[str, float] = {}
    fmt = registry.get(brief.format_id)

    try:
        db.update_job(job_id, state=JobState.WRITING.value, brief=brief.model_dump())
        t = time.time()
        script = writer.write(brief)
        timings["write"] = round(time.time() - t, 1)
        db.update_job(job_id, script=script.model_dump())

        style = brief.style or {}
        cats = style.get("background_categories") or (
            [style["background_category"]] if style.get("background_category") else []
        )
        seed = hash(job_id) & 0xFFFF
        clips: list[str] = []
        portrait: str | None = None

        if getattr(fmt, "USES_FIGURE_FOOTAGE", False) and brief.topic:
            from . import avatar, footage

            if avatar.available():
                try:
                    portrait = avatar.get_portrait(brief.topic)
                except Exception:
                    portrait = None
            if not portrait:
                # no talking head — use footage OF the figure
                try:
                    clips = footage.ensure_figure(brief.topic, count=3)
                except Exception:
                    clips = []

        if not clips:
            # gameplay: talking head sits on top of it, or it's the whole bg
            clips = assets.pick_mixed(cats, count=3, seed=seed)

        plan = fmt.build_plan(brief, script, clips)
        if portrait:
            plan.portrait = portrait
            # talking head fills the top ~55%; keep captions in the gameplay
            # half so they don't land on the seam.
            plan.caption.position = "bottom"

        # Resolve the Director's music mood to an actual track — downloading a
        # few for that mood on first use (cached after), like footage.
        mood = style.get("music_mood")
        if mood and not plan.music_file and settings.music.enabled:
            from . import music
            import random as _r

            try:
                music.ensure(mood, count=settings.music.per_mood)
                pick = music.pick(mood, _r.Random(seed),
                                  exclude=tuple(db.recent_music(8)))
            except Exception:
                pick = None
            if pick:
                plan.music_file = pick
                brief.style["music_file"] = pick   # so recent_music() sees it
                db.update_job(job_id, brief=brief.model_dump())

        db.update_job(job_id, state=JobState.RENDERING.value)
        t = time.time()
        with _render_lock():
            out = render_isolated(plan, job_id=job_id, out_dir=settings.out_path)
        timings["render"] = out["seconds"]

        vid = db.record_video(
            job_id=job_id, format_id=brief.format_id, topic=brief.topic,
            path=out["path"], duration=out["duration"],
        )
        db.update_job(job_id, state=JobState.DONE.value,
                      result={"video_id": vid, **out})

        if publish_to:
            from . import publish as _publish

            _publish.publish(vid, publish_to)

        return VideoResult(
            job_id=job_id, path=out["path"], duration=out["duration"],
            brief=brief, script=script, plan=plan, timings=timings,
            cost={"llm": 0.0 if not llm.available() else -1.0},
        )
    except Exception as exc:
        db.update_job(job_id, state=JobState.FAILED.value, error=f"{type(exc).__name__}: {exc}")
        raise


def run_once(*, format_id: str | None = None, topic: str | None = None,
             language: str | None = None, voice: str | None = None,
             seed: int | None = None,
             publish_to: list[str] | None = None) -> VideoResult:
    db.init_db()
    brief = plan_brief(format_id=format_id, topic=topic, language=language,
                       voice=voice, seed=seed)
    return produce(brief, publish_to=publish_to)


def run_batch(n: int, **kw) -> list[VideoResult]:
    results = []
    for _ in range(n):
        try:
            results.append(run_once(**kw))
        except Exception as exc:  # keep going through a batch
            print(f"  ! job failed: {exc}")
    return results
