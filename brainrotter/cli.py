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
    count: int = typer.Option(1, "--count", "-n", help="How many videos."),
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
    made = 0
    for i in range(count):
        console.rule(f"[bold]job {i + 1}/{count}")
        try:
            res = orchestrator.run_once(
                format_id=format, topic=topic,
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

    t = Table("source", "kind", "score", "hints", "title")
    for s in tr.gather(limit=limit):
        t.add_row(s.source, s.kind, f"{s.score:.2f}",
                  ",".join(s.format_hints), s.title[:70])
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
          "ollama pull llama3.1:8b  (or set writer.provider) — stub writer used otherwise")
    check("yt-dlp installed (footage)", _has("yt_dlp"), "pip install 'yt-dlp[default]'")
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
