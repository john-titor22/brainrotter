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
# Separate lock for GPU image generation — an 8 GB card can't run two SDXL
# passes at once (OOM / stall), and one video's stills shouldn't fight another's.
_GPU_LOCK_PORT = 47657


@contextlib.contextmanager
def _port_lock(port: int, *, wait: bool = True, poll: float = 3.0, what: str = "operation"):
    s = socket.socket()
    try:
        while True:
            try:
                s.bind(("127.0.0.1", port))
                s.listen(1)
                break
            except OSError:
                if not wait:
                    raise RuntimeError(f"another {what} is in progress")
                time.sleep(poll)
        yield
    finally:
        s.close()


def _render_lock(wait: bool = True, poll: float = 3.0):
    return _port_lock(_RENDER_LOCK_PORT, wait=wait, poll=poll, what="render")


def _gpu_lock(wait: bool = True, poll: float = 3.0):
    return _port_lock(_GPU_LOCK_PORT, wait=wait, poll=poll, what="image generation")


def _maybe_publish(video_id: str, explicit: list[str] | None) -> None:
    """Publish the video (explicit request OR auto-publish is on). Runs in a
    daemon thread — a slow upload must not hold up the worker."""
    from .config import get_settings

    from . import publish as _pub

    pcfg = get_settings().publish
    do = bool(explicit) or (pcfg.enabled and _pub.any_ready() and _pub.auto_publish_on())
    if not do:
        return
    providers = explicit or None
    import threading

    def _go():
        try:
            from . import publish as _publish

            _publish.publish(video_id, providers)
        except Exception as exc:  # noqa: BLE001
            import logging
            logging.getLogger("brainrotter.publish").warning(
                "publish thread failed for %s: %s", video_id, exc)

    threading.Thread(target=_go, name="publish", daemon=True).start()


def plan_brief(*, format_id: str | None = None, topic: str | None = None,
               language: str | None = None, voice: str | None = None,
               visual_treatment: str | None = None,
               seed: int | None = None) -> Brief:
    signals = trends.gather(seed=seed)
    return director.decide(signals, format_id=format_id, topic=topic,
                           language=language, voice=voice,
                           visual_treatment=visual_treatment, seed=seed)


def produce(brief: Brief, *, job_id: str | None = None,
            publish_to: list[str] | None = None) -> VideoResult:
    settings = get_settings()
    if job_id is None:
        sc = brief.series
        job_id = db.create_job(
            format_id=brief.format_id, topic=brief.topic,
            series_id=(sc.series_id if sc else None),
            part=(sc.part if sc else None), seq=(sc.part if sc else 0),
        )
    timings: dict[str, float] = {}
    fmt = registry.get(brief.format_id)

    try:
        db.update_job(job_id, state=JobState.WRITING.value, brief=brief.model_dump())

        # Escape hatch: a format may own its ENTIRE video — assets, script and
        # render (a movie recap, a compilation cut, a 2D animation, a chat UI).
        # It gets the Brief and a RenderContext and returns the finished MP4.
        custom = getattr(fmt, "render_video", None)
        if callable(custom):
            from .models import Script
            from .formats.base import RenderContext

            workdir = settings.jobs_dir / job_id
            workdir.mkdir(parents=True, exist_ok=True)
            db.update_job(job_id, state=JobState.RENDERING.value)
            t = time.time()
            out = custom(brief, RenderContext(
                job_id=job_id, settings=settings, seed=hash(job_id) & 0xFFFF,
                workdir=workdir, render_lock=_render_lock,
            ))
            if not (out and out.get("path")):
                raise RuntimeError(f"{brief.format_id}.render_video produced nothing")
            timings["render"] = out.get("seconds", round(time.time() - t, 1))
            script = (Script(**out["script"]) if out.get("script")
                      else Script(title=brief.topic, beats=[]))
            db.update_job(job_id, script=script.model_dump())
            vid = db.record_video(
                job_id=job_id, format_id=brief.format_id, topic=brief.topic,
                path=out["path"], duration=float(out.get("duration", 0.0)),
            )
            db.update_job(job_id, state=JobState.DONE.value,
                          result={"video_id": vid, **{k: v for k, v in out.items() if k != "script"}})
            if brief.series:
                from . import series as _series

                _series.record_part(brief.series.series_id, brief.series.part,
                                    script, brief.language)
            _maybe_publish(vid, publish_to)
            return VideoResult(
                job_id=job_id, path=out["path"],
                duration=float(out.get("duration", 0.0)),
                brief=brief, script=script, plan=None, timings=timings,
                cost={"llm": 0.0 if not llm.available() else -1.0},
            )

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
        generated_stills = False
        gameplay_bg = False
        hero_image: str | None = None      # talking-object still (SadTalker)

        # A format that IS a subject (object_story = the object, ai_brainrot =
        # the creature) narrates itself with AI visuals.
        beat_weights: list[float] = []
        if style.get("visual_treatment") == "generated":
            from . import visuals

            if visuals.available():
                vdir = settings.visuals_cache_path / job_id

                # object_story: give the object a face and lip-sync it.
                if getattr(fmt, "TALKING_SUBJECT", False) and settings.visuals.talking_subject:
                    from . import avatar

                    if avatar.available():
                        try:
                            with _gpu_lock():
                                hero = visuals.generate(
                                    [visuals.hero_prompt(brief)], vdir / "hero",
                                    seed=seed,
                                    negative_extra=visuals.negative_extra(brief),
                                )
                        except Exception:
                            hero = {}
                        if hero:
                            hero_image = str(next(iter(hero.values())))

                if not hero_image:
                    prompts, weights = visuals.storyboard(brief, script)
                    try:
                        with _gpu_lock():
                            got = visuals.generate(
                                prompts, vdir, seed=seed,
                                negative_extra=visuals.negative_extra(brief),
                            )
                    except Exception:
                        got = {}
                    ordered = sorted(got)
                    if len(ordered) >= 2:
                        clips = [str(got[i]) for i in ordered]
                        beat_weights = [weights[i] if i < len(weights) else 1.0 for i in ordered]
                        generated_stills = True

        # Narration-only series parts use Creative-Commons b-roll that fits the
        # story (not gameplay); pads with gameplay if too little CC turns up.
        if not generated_stills and not hero_image and brief.series and brief.series.broll:
            from . import footage

            try:
                clips = footage.ensure_narrative(
                    brief.series.series_id, brief.series.part, brief.series.broll,
                )
            except Exception:
                clips = []
            if 0 < len(clips) < 2:
                clips = clips + assets.pick_mixed(
                    [], count=2, seed=seed,
                    exclude=tuple(db.recent_background_clips(12)),
                )

        if (not generated_stills and not hero_image and not clips
                and getattr(fmt, "USES_FIGURE_FOOTAGE", False) and brief.topic):
            from . import avatar, footage

            if avatar.available():
                try:
                    portrait = avatar.get_portrait(brief.topic)
                except Exception:
                    portrait = None
            if not portrait:
                try:
                    clips = footage.ensure_figure(brief.topic, count=3)
                except Exception:
                    clips = []

        if not clips and not hero_image:
            # plain gameplay: ONE clip, played straight (or looped) for the whole
            # video — no mid-video cuts, no switching games. Clips are ~120s so a
            # 30-60s video usually never even loops.
            one = assets.pick(cats[0] if cats else None, count=1, seed=seed,
                              exclude=tuple(db.recent_background_clips(12)))
            if not one:
                one = assets.pick(None, count=1, seed=seed,
                                  exclude=tuple(db.recent_background_clips(12)))
            clips = one
            gameplay_bg = bool(one) and not portrait

        # Remember which clips this video used so the next few videos rotate off
        # them (matters a lot when only a couple of categories are cached).
        if clips and not generated_stills:
            from pathlib import Path as _P

            brief.style["background_clips"] = [_P(c).name for c in clips]
            db.update_job(job_id, brief=brief.model_dump())

        plan = fmt.build_plan(brief, script, clips)
        if generated_stills:
            # play the stills in beat order, each held for its beat's narration
            plan.visual_treatment = "generated"
            plan.concat_mode = "sequential"
            plan.visual_beat_weights = beat_weights
        if portrait:
            plan.portrait = portrait
            # talking head fills the top ~55%; keep captions in the gameplay
            # half so they don't land on the seam.
            plan.caption.position = "bottom"

        # Resolve the Director's music mood to an actual track — downloading a
        # few for that mood on first use (cached after), like footage. Series
        # parts already have their track locked by series.build_part_brief.
        mood = style.get("music_mood")
        if mood and not plan.music_file and settings.music.enabled and not brief.series:
            from . import music
            import random as _r

            import os as _os

            rng = _r.Random(seed)
            recent = tuple(db.recent_music(8))
            pick = None
            # 1. the Director's specific per-video vibe
            query = style.get("music_query")
            if query and settings.music.per_video_query:
                try:
                    tracks = music.ensure_query(query)
                    fresh = [c for c in tracks if _os.path.basename(c) not in recent]
                    cand = fresh or tracks
                    if cand:
                        pick = rng.choice(cand)
                except Exception:
                    pick = None
            # 2. fall back to the mood pool
            if not pick:
                try:
                    music.ensure(mood, count=settings.music.per_mood)
                    pick = music.pick(mood, rng, exclude=recent)
                except Exception:
                    pick = None
            if pick:
                plan.music_file = pick
                brief.style["music_file"] = pick   # so recent_music() sees it
                db.update_job(job_id, brief=brief.model_dump())

        db.update_job(job_id, state=JobState.RENDERING.value)
        t = time.time()
        with _render_lock():
            from .engine import storyboard

            if hero_image:
                try:
                    out = storyboard.render_talking_subject(
                        script=script, hero_image=hero_image, plan=plan,
                        job_id=job_id, out_dir=settings.out_path,
                    )
                except Exception as exc:
                    # SadTalker couldn't find a face / timed out — fall back to
                    # Ken-Burns of the object still(s).
                    print(f"[orchestrator] talking subject failed ({exc}); Ken-Burns stills")
                    from . import visuals

                    pr, wt = visuals.storyboard(brief, script)
                    with _gpu_lock():
                        g2 = visuals.generate(pr, settings.visuals_cache_path / job_id,
                                              seed=seed, negative_extra=visuals.negative_extra(brief))
                    imgs = [str(g2[i]) for i in sorted(g2)] or [hero_image]
                    wts = [wt[i] if i < len(wt) else 1.0 for i in sorted(g2)] or [1.0]
                    plan.visual_treatment = "generated"
                    plan.concat_mode = "sequential"
                    plan.visual_beat_weights = wts
                    out = storyboard.render(script=script, images=imgs, weights=wts,
                                            plan=plan, job_id=job_id, out_dir=settings.out_path)
            elif generated_stills:
                try:
                    out = storyboard.render(
                        script=script, images=clips, weights=beat_weights,
                        plan=plan, job_id=job_id, out_dir=settings.out_path,
                    )
                except Exception as exc:
                    print(f"[orchestrator] native storyboard failed ({exc}); using engine")
                    out = render_isolated(plan, job_id=job_id, out_dir=settings.out_path)
            elif gameplay_bg and clips:
                try:
                    out = storyboard.render_over_gameplay(
                        script=script, clip=clips[0], plan=plan,
                        job_id=job_id, out_dir=settings.out_path, seed=seed,
                    )
                except Exception as exc:
                    print(f"[orchestrator] native gameplay render failed ({exc}); using engine")
                    out = render_isolated(plan, job_id=job_id, out_dir=settings.out_path)
            else:
                try:
                    out = render_isolated(plan, job_id=job_id, out_dir=settings.out_path,
                                          attempts=3 if brief.series else 2)
                except Exception as exc:
                    # the MPT engine choked (bad/no materials, MoviePy death…).
                    # Salvage with the native one-clip renderer over a known-good
                    # gameplay clip rather than failing the job.
                    print(f"[orchestrator] engine failed ({exc}); native gameplay salvage")
                    good = (assets.pick(None, count=1, seed=seed,
                                        exclude=tuple(db.recent_background_clips(12)))
                            or assets.pick(None, count=1))
                    if not good:
                        raise
                    out = storyboard.render_over_gameplay(
                        script=script, clip=good[0], plan=plan,
                        job_id=job_id, out_dir=settings.out_path, seed=seed,
                    )
        timings["render"] = out["seconds"]

        vid = db.record_video(
            job_id=job_id, format_id=brief.format_id, topic=brief.topic,
            path=out["path"], duration=out["duration"],
        )
        db.update_job(job_id, state=JobState.DONE.value,
                      result={"video_id": vid, **out})

        if brief.series:
            from . import series as _series

            _series.record_part(brief.series.series_id, brief.series.part,
                                script, brief.language)

        # Thin footage pool = the same background every video. Grow it by one
        # category in the background (never blocks, never raises).
        if not generated_stills and not brief.series and plan.background_source == "local":
            import threading

            from . import footage

            threading.Thread(target=footage.autogrow, name="footage-autogrow",
                             daemon=True).start()

        _maybe_publish(vid, publish_to)

        return VideoResult(
            job_id=job_id, path=out["path"], duration=out["duration"],
            brief=brief, script=script, plan=plan, timings=timings,
            cost={"llm": 0.0 if not llm.available() else -1.0},
        )
    except Exception as exc:
        db.update_job(job_id, state=JobState.FAILED.value, error=f"{type(exc).__name__}: {exc}")
        if brief.series:
            from . import series as _series

            try:
                _series.abort(brief.series.series_id,
                              f"part {brief.series.part} failed: {type(exc).__name__}: {exc}")
            except Exception:
                pass
        raise


def run_once(*, format_id: str | None = None, topic: str | None = None,
             language: str | None = None, voice: str | None = None,
             visual_treatment: str | None = None,
             seed: int | None = None,
             publish_to: list[str] | None = None) -> VideoResult:
    db.init_db()
    brief = plan_brief(format_id=format_id, topic=topic, language=language,
                       voice=voice, visual_treatment=visual_treatment, seed=seed)
    return produce(brief, publish_to=publish_to)


def run_series(*, topic: str | None = None, parts: int | None = None,
               format_id: str | None = None, language: str | None = None,
               voice: str | None = None, seed: int | None = None,
               publish_to: list[str] | None = None) -> tuple[dict, list[VideoResult]]:
    """Plan a "related story" and produce Part 1..N in order (CLI path).

    Jobs are created one at a time, right before each part renders, so a running
    dashboard worker never races us for a queued part.
    """
    from . import series as _series

    db.init_db()
    info = _series.create(topic=topic, target_parts=parts, format_id=format_id,
                          language=language, voice=voice, seed=seed)
    results: list[VideoResult] = []
    for part in range(1, info["n_parts"] + 1):
        job_id = db.create_job(
            format_id=info["format_id"], topic=info["topic"],
            overrides={"language": info["language"]},
            series_id=info["series_id"], part=part, seq=part,
        )
        # Take the job out of 'queued' before the (possibly slow) part planning
        # so a live dashboard worker can't also claim and render it.
        db.update_job(job_id, state=JobState.DIRECTING.value)
        try:
            brief = _series.build_part_brief(info["series_id"], part)
            results.append(produce(brief, job_id=job_id, publish_to=publish_to))
        except Exception as exc:  # abort() already ran inside produce()
            print(f"  ! part {part} failed, stopping series: {exc}")
            break
    return info, results
