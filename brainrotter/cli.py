"""Brainrotter CLI."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):  # Windows consoles default to cp1252
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass

import typer
from rich.console import Console
from rich.table import Table

from . import assets, db
from .config import PROJECT_ROOT, get_settings
from .engine.mpt_config import ENGINE_ROOT
from .formats import registry
from .writer import llm

app = typer.Typer(add_completion=False, help="Brainrotter — the brainrot video factory.")
console = Console()


@app.command()
def run(
    format: str = typer.Option(None, "--format", "-f", help="Force a format id."),
    topic: str = typer.Option(None, "--topic", "-t", help="Force a topic."),
    language: str = typer.Option(None, "--language", "-l",
                                 help="Force a language: en | ary (Moroccan Darija)."),
    voice: str = typer.Option(None, "--voice", help="Force an edge-tts voice (see `brainrotter voices`)."),
    visuals: str = typer.Option(None, "--visuals", help="Force visual treatment: footage | generated."),
    count: int = typer.Option(1, "--count", "-n", help="How many videos."),
    series: bool = typer.Option(False, "--series", help="Related story: produce Part 1..N of one arc."),
    parts: int = typer.Option(None, "--parts", help="Target parts for --series (capped at series.max_parts)."),
    seed: int = typer.Option(None, help="Deterministic seed."),
    publish: str = typer.Option(None, help="Comma-separated platforms to publish to."),
):
    """Produce one or more videos. With no options, the Director decides everything."""
    from . import orchestrator

    db.init_db()
    if db.queue_summary()["in_flight"] > 0:
        console.print("[yellow]note:[/] a job is already rendering — this run will "
                      "queue behind it (renders are one at a time).")
    platforms = [p.strip() for p in publish.split(",")] if publish else None

    if series:
        info, results = orchestrator.run_series(
            topic=topic, parts=parts, format_id=format, language=language,
            voice=voice, seed=seed, publish_to=platforms,
        )
        console.rule(f"[bold]series: {info['title']}")
        console.print(f"  {len(results)}/{info['n_parts']} parts produced   "
                      f"format [cyan]{info['format_id']}[/]   voice {info['voice']}")
        for i, res in enumerate(results, 1):
            console.print(f"  [green]✓[/] Part {i}: {res.script.title}   "
                          f"{res.duration:.0f}s   → {res.path}")
        console.print(f"\n[bold]series {info['series_id']}[/] → {get_settings().out_path}")
        return

    made = 0
    for i in range(count):
        console.rule(f"[bold]job {i + 1}/{count}")
        try:
            res = orchestrator.run_once(
                format_id=format, topic=topic, language=language, voice=voice,
                visual_treatment=visuals,
                seed=(seed + i if seed is not None else None),
                publish_to=platforms,
            )
        except Exception as exc:
            console.print(f"[red]failed:[/] {exc}")
            continue
        made += 1
        console.print(f"[green]✓[/] {res.path}")
        console.print(f"  format: [cyan]{res.brief.format_id}[/]   "
                      f"topic: {res.brief.topic}")
        console.print(f"  why: [dim]{res.brief.rationale}[/]")
        console.print(f"  title: {res.script.title}   "
                      f"{res.duration:.0f}s   render {res.timings.get('render', '?')}s")
    console.print(f"\n[bold]{made}/{count} produced[/] → {get_settings().out_path}")


@app.command()
def serve(host: str = "localhost", port: int = 8000):
    """Run the local dashboard server (stays up until Ctrl+C).

    Binds 'localhost' so both 127.0.0.1 and ::1 work.
    """
    import uvicorn

    uvicorn.run("brainrotter.server.app:app", host=host, port=port, reload=False)


@app.command("app")
def app_launch():
    """Open Brainrotter as a desktop window. Closing the window stops the server."""
    from . import launcher

    raise typer.Exit(launcher.run())


queue_app = typer.Typer(help="Inspect and control the job queue.")
app.add_typer(queue_app, name="queue")


@queue_app.command("status")
def queue_status():
    """Show the queue: how many waiting, running, done."""
    db.init_db()
    s = db.queue_summary()
    state = "[yellow]PAUSED[/]" if s["paused"] else "[green]running[/]"
    console.print(
        f"queue {state}   waiting: [bold]{s['queued']}[/]   in flight: {s['in_flight']}"
        f"   done: {s['done']}   failed: {s['failed']}   canceled: {s['canceled']}"
    )
    t = Table("state", "format", "topic")
    for j in db.list_jobs(15):
        if j["state"] in ("queued", *db.IN_FLIGHT_STATES):
            t.add_row(j["state"], j.get("format_id") or "-", (j.get("topic") or "")[:50])
    if t.row_count:
        console.print(t)


@queue_app.command("pause")
def queue_pause():
    """Stop starting new jobs (the current one finishes)."""
    db.init_db()
    db.set_paused(True)
    console.print("[yellow]queue paused[/] — the running job finishes; nothing new starts.")


@queue_app.command("resume")
def queue_resume():
    """Resume processing the queue."""
    db.init_db()
    db.set_paused(False)
    console.print("[green]queue resumed[/]")


@queue_app.command("clear")
def queue_clear():
    """Cancel all waiting jobs (does not touch the running one)."""
    db.init_db()
    n = db.cancel_queued()
    console.print(f"canceled [bold]{n}[/] waiting job(s)")


@queue_app.command("clear-history")
def queue_clear_history(keep: int = typer.Option(3, help="Keep this many recent finished jobs.")):
    """Delete finished jobs + their video files + stale engine scratch."""
    from . import maintenance

    db.init_db()
    r = maintenance.clear_history(keep_last=keep)
    console.print(
        f"removed [bold]{r['removed_jobs']}[/] jobs, {r['removed_files']} video files, "
        f"{r['engine_dirs_cleaned']} engine scratch folders"
    )


movie_app = typer.Typer(help="Movie-recap format: cut a local film into shorts.")
app.add_typer(movie_app, name="movie")


@movie_app.command("list")
def movie_list():
    """Show movie files available for `run -f movie_recap`."""
    from .engine import movie as _m

    root = get_settings().movies_path
    files = [p for p in root.rglob("*") if p.suffix.lower() in _m.VIDEO_EXTS]
    if not files:
        console.print(f"[dim]no movies in {root} — drop an .mp4/.mkv there[/]")
        return
    t = Table("file", "length", "recap title")
    for p in sorted(files):
        d = _m.probe_duration(p)
        from .formats.movie_recap import _title_from_file

        t.add_row(p.name, f"{int(d // 60)}m{int(d % 60):02d}s", _title_from_file(p))
    console.print(t)


comp_app = typer.Typer(help="Compilation format: themed supercuts of sourced clips.")
app.add_typer(comp_app, name="compilation")


@comp_app.command("themes")
def compilation_themes():
    """List compilation themes and their cached clip pools."""
    from .compilation import sources as csrc
    from .compilation.themes import THEMES

    t = Table("theme", "topic", "cached", "fresh", "subreddits")
    for k, th in THEMES.items():
        clips = csrc.cached_clips(k)
        used = csrc.load_used(k)
        fresh = sum(1 for p in clips if used.get(p.stem, 0) == 0)
        t.add_row(k, th.label, str(len(clips)), str(fresh),
                  ", ".join("r/" + s for s in th.subreddits[:3]) + "…")
    console.print(t)
    src = "Reddit API" if csrc.reddit_api_available() else "Reddit RSS + YouTube search"
    console.print(f"[dim]sourcing via {src}[/]")


@comp_app.command("sync")
def compilation_sync(
    theme: str = typer.Argument(..., help="theme key (see `compilation themes`)"),
    n: int = typer.Option(10, "-n", help="how many new clips to fetch"),
):
    """Pre-download source clips for a theme."""
    from .compilation import sources as csrc
    from .compilation.themes import THEMES

    if theme not in THEMES:
        console.print(f"[red]unknown theme '{theme}'[/] — one of: {', '.join(THEMES)}")
        raise typer.Exit(1)
    console.print(f"sourcing up to {n} clips for [cyan]{theme}[/]…")
    got = csrc.harvest(theme, n)
    console.print(f"[green]+{len(got)}[/] clip(s) → {csrc.theme_dir(theme)}")


series_app = typer.Typer(help="Inspect 'related story' multi-part series.")
app.add_typer(series_app, name="series")


@app.command("publish-auth")
def publish_auth_cmd(
    provider: str = typer.Argument(..., help="youtube | meta | tiktok"),
    account: str = typer.Option("default", "--account", "-a",
                                help="Label for this account, e.g. a second channel/Page. "
                                     "Omit for the first/only account on this platform."),
    no_browser: bool = typer.Option(False, "--no-browser", help="Just print the URL."),
):
    """One-time OAuth for a native publish provider (opens your browser).

    Run this again with a different --account to authorize a SECOND account
    on the same platform (e.g. a second YouTube channel) — publish() then
    round-robins across every authorized account on that platform for extra
    daily capacity, instead of overwriting the first account's token."""
    from .publish import meta as _m
    from .publish import tiktok as _t
    from .publish import youtube as _y

    mods = {"youtube": _y, "meta": _m, "instagram": _m, "facebook": _m, "tiktok": _t}
    mod = mods.get(provider)
    if not mod:
        console.print("[red]provider must be: youtube | meta | tiktok[/]")
        raise typer.Exit(1)
    if not mod.configured():
        console.print(mod.auth(open_browser=False))     # prints the "set X in .env" hint
        raise typer.Exit(1)
    console.print(mod.auth(open_browser=not no_browser, account=account))


@app.command("accounts")
def accounts_cmd(provider: str = typer.Argument(None, help="youtube | meta | tiktok. Omit for all.")):
    """List every authorized account per publish provider."""
    from . import publish as pub

    names = [provider] if provider else ["youtube", "instagram", "facebook", "tiktok"]
    for name in names:
        accs = pub.accounts_for(name if name != "meta" else "instagram")
        if not accs:
            console.print(f"[dim]{name}: no accounts authorized yet[/]")
            continue
        console.print(f"[bold]{name}[/]")
        authed = pub.authed_accounts_for(name if name != "meta" else "instagram")
        for a in accs:
            ready = "authorized" if a in authed else "not authorized"
            console.print(f"  {a}  [dim]{ready}[/]")


@app.command("publish")
def publish_cmd(
    job_id: str = typer.Argument(None, help="Job id to publish. Omit to publish the last unpublished video."),
    platforms: str = typer.Option(None, "-p", "--platforms",
                                  help="Comma list: youtube,instagram,facebook,tiktok,upload_post. Default = config."),
    all_pending: bool = typer.Option(False, "--all", help="Publish every unpublished video."),
):
    """Publish finished videos as Shorts and (optionally) free local space."""
    from . import publish as pub

    db.init_db()
    if not pub.any_ready():
        console.print("[yellow]No provider ready.[/] Run `brainrotter publish-auth youtube` "
                      "(and/or meta, tiktok), or set UPLOAD_POST_API_KEY. Doctor shows status.")
        raise typer.Exit(1)
    plats = [p.strip() for p in platforms.split(",")] if platforms else None

    if all_pending:
        with db.connect() as c:
            rows = c.execute("SELECT id FROM videos WHERE published_at IS NULL "
                             "AND local_deleted = 0 ORDER BY created_at").fetchall()
        console.print(f"publishing {len(rows)} video(s)…")
        for r in rows:
            res = pub.publish(r["id"], plats)
            console.print(("[green]✓[/] " if res["ok"] else "[red]✗[/] ") + r["id"]
                          + " " + (", ".join(res.get("urls", {}).values()) or res.get("error", "")))
        return

    if job_id:
        res = pub.publish_job(job_id, plats)
    else:
        with db.connect() as c:
            r = c.execute("SELECT id FROM videos WHERE published_at IS NULL "
                          "AND local_deleted = 0 ORDER BY created_at DESC LIMIT 1").fetchone()
        if not r:
            console.print("nothing unpublished")
            raise typer.Exit(0)
        res = pub.publish(r["id"], plats)
    if res["ok"]:
        console.print("[green]published[/] → " + (", ".join(res.get("urls", {}).values()) or "(no urls returned)"))
        if res.get("deleted_local"):
            console.print("  [dim]local file deleted[/]")
    else:
        console.print(f"[red]failed:[/] {res.get('error')}")
        raise typer.Exit(1)


@series_app.command("list")
def series_list_cmd(limit: int = 20):
    """Show planned / running / finished series."""
    db.init_db()
    rows = db.list_series(limit)
    if not rows:
        console.print("[dim]no series yet — `brainrotter run --series`[/]")
        return
    t = Table("id", "title", "format", "lang", "parts", "state")
    for s in rows:
        import json as _j

        try:
            title = _j.loads(s.get("plan_json") or "{}").get("title") or s["topic"]
        except Exception:
            title = s["topic"]
        t.add_row(s["id"], (title or "")[:40], s["format_id"] or "-",
                  s["language"] or "en",
                  f"{s['parts_done']}/{s['n_parts']}"
                  + (f" (+{s['parts_failed']}✗)" if s["parts_failed"] else ""),
                  s["state"])
    console.print(t)


@series_app.command("show")
def series_show_cmd(series_id: str):
    """Show a series' arc and per-part status."""
    db.init_db()
    s = db.get_series(series_id)
    if not s:
        console.print(f"[red]no such series {series_id}[/]")
        raise typer.Exit(1)
    plan = db.series_plan(series_id)
    jobs = {j["part"]: j for j in db.series_jobs(series_id)}
    console.print(f"[bold]{plan.get('title', s['topic'])}[/]  "
                  f"[dim]({s['state']}, {s['format_id']}, {s['language']})[/]")
    console.print(f"[dim]{plan.get('premise', '')}[/]\n")
    t = Table("part", "state", "goal")
    for p in plan.get("parts", []):
        j = jobs.get(p["n"], {})
        t.add_row(str(p["n"]), j.get("state", "-"), (p.get("goal") or "")[:80])
    console.print(t)


@app.command()
def voices():
    """List the edge-tts voices the Director / `run --voice` can pick."""
    from .engine import voices as _v

    t = Table("language", "voice", "gender", "label")
    for lang, vs in _v.catalog_by_language().items():
        for v in vs:
            t.add_row(lang, v.name, v.gender, v.label)
    console.print(t)


@app.command()
def formats():
    """List available formats and their performance."""
    stats = db.format_stats()
    t = Table("id", "name", "published", "ewma score", "description")
    for f in registry.describe():
        s = stats.get(f["id"], {})
        t.add_row(f["id"], f["name"], str(s.get("n_published", 0)),
                  f"{s.get('ewma_score', 0):.3f}", f["description"])
    console.print(t)


@app.command()
def trends(limit: int = 15):
    """Show the trend signals the Director would see right now."""
    from . import trends as tr

    t = Table("source", "kind", "score", "title")
    for s in tr.gather(limit=limit):
        t.add_row(s.source, s.kind, f"{s.score:.2f}", s.title[:70])
    console.print(t)


@app.command()
def doctor():
    """Check the environment is ready."""
    s = get_settings()
    ok = True

    def check(label: str, good: bool, hint: str = ""):
        nonlocal ok
        ok = ok and good
        console.print(f"  {'[green]✓[/]' if good else '[red]✗[/]'} {label}"
                      + (f"  [dim]{hint}[/]" if not good and hint else ""))

    check("ffmpeg on PATH", bool(shutil.which(s.ffmpeg_bin)), "install ffmpeg")
    check("vendored engine present", ENGINE_ROOT.is_dir(),
          "git clone MoneyPrinterTurbo into vendor/")
    check("engine importable", (ENGINE_ROOT / "app" / "services" / "task.py").is_file())
    console.print(f"  [dim]Writer:[/] {llm.status()}")
    check(f"Writer ({llm.provider()}) usable", llm.available(),
          "set GROQ_API_KEY in .env (provider=groq), or `ollama pull llama3.1:8b` "
          "(provider=ollama) — stub writer used otherwise")
    langs = s.language.enabled
    if langs != ["en"]:
        console.print(f"  [dim]Languages:[/] {', '.join(langs)}   "
                      f"[dim]Darija writer:[/] {llm.multilingual_status()}")
        if "ary" in langs:
            from .darija_tts import generator as _dtts

            console.print(f"  [dim]Darija voice:[/] {_dtts.status()}")
    check("yt-dlp installed (footage / music)", _has("yt_dlp"), "pip install 'yt-dlp[default]'")
    if any(l in ("ar", "ary") for l in langs):
        from .formats.base import RTL_CAPTION_FONT

        rtl_ok = (ENGINE_ROOT / "resource" / "fonts" / RTL_CAPTION_FONT).is_file()
        check(f"Arabic caption font ({RTL_CAPTION_FONT})", rtl_ok,
              "run `brainrotter setup` to restore bundled fonts")
        check("RTL caption shaping (arabic-reshaper, python-bidi)",
              _has("arabic_reshaper") and _has("bidi"),
              "pip install arabic-reshaper python-bidi")
    if s.music.enabled:
        from . import music as _music

        n_music = sum(len(_music.library(m)) for m in _music.moods())
        console.print(f"  [dim]Music:[/] {n_music} track(s) across "
                      f"{len(_music.moods())} moods"
                      + ("  — `brainrotter music sync` to fill" if n_music < 3 else ""))
    from . import avatar, visuals
    console.print(f"  [dim]anime_figure:[/] "
                  + ("talking-head ready (SadTalker)" if avatar.is_installed()
                     else "footage mode — `brainrotter avatar-setup` for talking heads"))
    console.print(f"  [dim]generated visuals:[/] {visuals.status()}")
    from .engine import movie as _mv
    n_movies = len([p for p in s.movies_path.rglob("*") if p.suffix.lower() in _mv.VIDEO_EXTS])
    wh = "faster-whisper ready" if _has("faster_whisper") else "faster-whisper missing"
    console.print(f"  [dim]movie_recap:[/] {n_movies} movie(s) in {s.movies_path.name}/, {wh}")
    from .compilation import review as _crev
    from .compilation import sources as _csrc
    from .compilation.themes import THEMES as _CTHEMES

    n_pool = sum(len(_csrc.cached_clips(k)) for k in _CTHEMES)
    src = "Reddit API + YouTube" if _csrc.reddit_api_available() else "YouTube + keyless Reddit (.json/.rss)"
    vm = _crev.vision_model()
    rev = f"auto-review via {vm}" if vm else "structural review only (pull llama3.2-vision for off-theme detection)"
    console.print(f"  [dim]compilation:[/] {len(_CTHEMES)} themes, {n_pool} clip(s) cached, "
                  f"sourcing via {src}; {rev}")
    from . import publish as _pub
    console.print(f"  [dim]publish:[/] {_pub.status()}")
    for pr in _pub.provider_status():
        mark = "[green]✓[/]" if pr["authed"] or (pr["name"] == "upload_post" and pr["configured"]) else \
               ("[yellow]auth[/]" if pr["configured"] else "[dim]—[/]")
        console.print(f"    {mark} {pr['name']:<11} {pr['hint'] if mark != '[green]✓[/]' else ''}")
    cookie = Path(s.footage.cookies_file) if s.footage.cookies_file else s.root / "assets" / "cookies.txt"
    if s.footage.allow_youtube:
        check("JS runtime for YouTube (node/deno)",
              bool(shutil.which("node") or shutil.which("deno")),
              "install Node.js — YouTube's challenge needs it")
        has_cookies = cookie.is_file() or bool(s.footage.cookies_from_browser)
        check("YouTube cookies configured", has_cookies,
              "export youtube.com cookies to assets/cookies.txt (see README)")
    check("footage available", assets.has_any(),
          f"`brainrotter footage sync` or add clips to {s.backgrounds_path}")
    console.print(f"\n[{'green' if ok else 'yellow'}]"
                  f"{'ready' if ok else 'usable, with gaps above'}[/]")


def _has(mod: str) -> bool:
    import importlib.util

    return importlib.util.find_spec(mod) is not None


footage_app = typer.Typer(help="Manage self-sourced background footage.")
app.add_typer(footage_app, name="footage")


@footage_app.command("sync")
def footage_sync(
    category: str = typer.Option(None, "--category", "-c", help="One category, or all."),
    count: int = typer.Option(None, "--count", "-n"),
):
    """Download background gameplay clips with yt-dlp."""
    from . import footage

    cats = [category] if category else footage.categories()
    for cat in cats:
        console.print(f"[cyan]{cat}[/] …", end=" ")
        try:
            got = footage.sync(cat, count=count)
            console.print(f"[green]+{len(got)}[/]")
        except Exception as exc:
            console.print(f"[red]{exc}[/]")


@footage_app.command("list")
def footage_list():
    """Show cached footage by category."""
    from . import footage

    t = Table("category", "clips", "source")
    idx = assets.index()
    cached = footage.cached()
    if not idx:
        console.print("[dim]no footage yet — run `brainrotter footage sync`[/]")
        return
    for cat, clips in sorted(idx.items()):
        n_dl = len(cached.get(cat, []))
        src = "downloaded" if n_dl == len(clips) else ("mixed" if n_dl else "manual")
        t.add_row(cat, str(len(clips)), src)
    console.print(t)


music_app = typer.Typer(help="Manage self-sourced, mood-tagged background music.")
app.add_typer(music_app, name="music")


@music_app.command("sync")
def music_sync(
    mood: str = typer.Option(None, "--mood", "-m", help="One mood, or all."),
    count: int = typer.Option(None, "--count", "-n"),
):
    """Download 'no copyright' background tracks with yt-dlp, tagged by mood."""
    from . import music

    moods = [mood] if mood else music.moods()
    for m in moods:
        console.print(f"[cyan]{m}[/] …", end=" ")
        try:
            got = music.sync(m, count=count)
            console.print(f"[green]+{len(got)}[/]")
        except Exception as exc:
            console.print(f"[red]{exc}[/]")


@music_app.command("list")
def music_list():
    """Show the music library by mood."""
    from . import music

    t = Table("mood", "tracks")
    total = 0
    for m in music.moods():
        n = len(music.library(m))
        total += n
        t.add_row(m, str(n))
    console.print(t)
    if not total:
        console.print("[dim]empty — `brainrotter music sync` or drop MP3s in "
                      "assets/music/<mood>/[/]")


@app.command("setup")
def setup_cmd():
    """First-run setup: vendor the engine, make configs, create the app shortcut."""
    from . import setup_cmd as _s

    raise typer.Exit(_s.run())


@app.command("install-shortcut")
def install_shortcut_cmd():
    """(Re)create the Brainrotter desktop + Start-menu shortcuts."""
    from . import setup_cmd as _s

    _s.install_shortcut()


@app.command("avatar-setup")
def avatar_setup_cmd():
    """Install SadTalker (talking-head generation for anime_figure). Big download."""
    from .avatar import setup as _a

    raise typer.Exit(_a.run())


@app.command("darija-tts-setup")
def darija_tts_setup_cmd(
    force: bool = typer.Option(False, "--force", help="Re-download the model."),
):
    """Install a real Moroccan Darija voice (XTTS-v2 fine-tune). ~2 GB one-time.

    Until this runs, Darija videos use edge-tts's ar-MA voice, which reads Darija
    text but sounds MSA-accented, not Darija.
    """
    from .darija_tts import generator as _d

    if not _d.TTS_PY.is_file():
        console.print("[yellow]creating the isolated venv + installing coqui-tts…[/]")
        import subprocess as _sp

        _d.TTS_DIR.mkdir(parents=True, exist_ok=True)
        for py in ("py -3.10", "py -3.11"):
            try:
                _sp.run([*py.split(), "-m", "venv", str(_d.TTS_DIR / ".venv")],
                        check=True, capture_output=True)
                break
            except Exception:
                continue
        if not _d.TTS_PY.is_file():
            console.print("[red]need Python 3.10 or 3.11 — install it (winget install Python.Python.3.10)[/]")
            raise typer.Exit(1)
        _sp.run([str(_d.TTS_PY), "-m", "pip", "install", "-q", "coqui-tts", "soundfile"],
                check=False)
    console.print(_d.run_setup(force=force))
    raise typer.Exit(0 if _d.is_installed() else 1)


@app.command("visual-setup")
def visual_setup_cmd(
    force: bool = typer.Option(False, "--force", help="Reinstall even if already set up."),
):
    """Install local AI image generation (Stable Diffusion). ~7 GB one-time.

    Once installed, the Director can pick 'generated' visuals — a still per beat
    of the actual subject — starting with the object_story format.
    """
    from .visuals import setup as _v

    raise typer.Exit(_v.run(force=force))


@app.command()
def init():
    """Create .env and config.toml from the examples."""
    for name in (".env", "config.toml"):
        dst = PROJECT_ROOT / name
        src = PROJECT_ROOT / f"{name.replace('.toml', '.example.toml').replace('.env', '.env.example')}"
        if dst.exists():
            console.print(f"  [dim]{name} exists, skipping[/]")
        elif src.exists():
            shutil.copyfile(src, dst)
            console.print(f"  [green]created[/] {name}")
    console.print("\nEdit .env to add ANTHROPIC_API_KEY, then `brainrotter doctor`.")


if __name__ == "__main__":
    app()
