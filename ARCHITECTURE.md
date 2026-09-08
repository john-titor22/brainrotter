# Brainrotter — Architecture

## Pipeline (one video)

```
                 ┌─────────────┐
 trend signals ─▶│  DIRECTOR   │  picks: format, topic, hook, style spec, target length
 past metrics  ─▶│  (Brain)    │
                 └──────┬──────┘
                        │  Brief
                        ▼
                 ┌─────────────┐
                 │   WRITER    │  LLM → Script (title, narration beats, on-screen text,
                 │             │        visual directions, SFX cues)
                 └──────┬──────┘
                        │  Script
                        ▼
        ┌───────────────────────────────────┐
        │   FACTORY  (vendored engine)       │
        │                                   │
        │  assets.py     pick background footage by category
        │  engine/       RenderPlan → vendored MoneyPrinterTurbo:
        │                  edge-tts narration + word boundaries
        │                  → word-by-word "pop" captions (no Whisper)
        │                  → ffmpeg composite → 1080×1920 MP4
        └───────────────┬───────────────────┘
                        │  Video
                        ▼
                 ┌─────────────┐
                 │  PUBLISHER  │  upload to TikTok / YT Shorts / Reels (later)
                 └──────┬──────┘
                        │
                        ▼
                 ┌─────────────┐
                 │  FEEDBACK   │  collect views/retention/likes → update Director weights
                 └─────────────┘
```

## Modules

| Package | Responsibility |
|---------|----------------|
| `brainrotter.config` | Load `config.toml` + `.env` into a typed `Settings` object. |
| `brainrotter.models` | Pydantic domain types: `Brief`, `Script`, `ScriptBeat`, `CaptionWord`, `RenderSpec`, `VideoResult`. The contracts every layer passes. |
| `brainrotter.db` | SQLite: `jobs`, `videos`, `metrics`, `format_stats`. Job queue + history. |
| `brainrotter.trends.*` | Signal sources. `reddit.py` (hot posts by subreddit), `topic_bank.py` (curated fallback). Each returns `TrendSignal`s. |
| `brainrotter.footage.*` | Self-sources background gameplay with `yt-dlp` from "no copyright" channels. `registry.py` = per-category search queries; `harvester.py` = download → ffmpeg-trim to a small silent loop → cache in `assets/cache/footage/<cat>/`. |
| `brainrotter.director` | Consumes signals + `format_stats`, emits a `Brief` (incl. background category + style). v1: heuristic + local-LLM scoring. v2: learned model. |
| `brainrotter.writer` | `llm.py` — provider abstraction: **`ollama`** (local, default, no key) or `anthropic` (opt-in). `scriptwriter.py` turns a `Brief` into a `Script`; offline template stub if no LLM is reachable. |
| `brainrotter.formats.*` | One module per format. Each implements: `writer_system_prompt()`, `writer_user_prompt(brief)`, `parse_script(raw)`, `build_plan(brief, script, clips) -> RenderPlan`. Registered in `registry.py`. |
| `brainrotter.assets` | Background footage library — index `assets/backgrounds/<category>/` and pick clips. |
| `brainrotter.engine` | Adapter over the vendored MoneyPrinterTurbo. `render_plan(plan, job_id)` builds MPT's `VideoParams`, calls `app.services.task.start()` in-process, copies the finished MP4 to `workspace/out/`. `mpt_config.py` writes MPT's `config.toml` from our `Settings` before import; `voices.py` is the curated edge-tts catalog. |
| `brainrotter.publish.*` | Upload adapters. Stub for now. |
| `brainrotter.feedback` | `scorer.py` — engagement model. v1: records metrics + simple rollups. v2: predictive. |
| `brainrotter.orchestrator` | Ties it together: `produce(brief) -> VideoResult`, `run_once()`, `run_batch(n)`. |
| `brainrotter.server` | FastAPI app + static dashboard (also a PWA — `/manifest.webmanifest`, installable). Wraps orchestrator, exposes the job queue, serialises rendering (`db.claim_next_job` + a machine-wide worker mutex on port 47654), pause/clear/clear-history. |
| `brainrotter.maintenance` | Housekeeping — purge finished jobs, delete their MP4s + orphans, clean engine scratch. |
| `brainrotter.cli` | `typer` CLI: `run`, `serve`, `formats`, `trends`, `doctor`, `init`, `footage {sync,list}`, `queue {status,pause,resume,clear,clear-history}`. |
| `extension/` | Chrome MV3 extension — toolbar popup + queue-badge that opens/controls the dashboard. Not a Python package. |

## Key contracts (`brainrotter/models.py`)

- **`Brief`** — Director's output. `format_id`, `topic`, `angle`, `hook`, `target_seconds`,
  `tone`, `style` (dict of knobs: caption style, voice, bg category, cut cadence), `rationale`,
  `source_signal`.
- **`Script`** — Writer's output. `title`, `beats: list[ScriptBeat]`, `hashtags`, `cta`.
  A `ScriptBeat` has `narration` (spoken), `on_screen` (optional overlay text),
  `visual` (direction), `sfx` (optional cue).
- **`RenderPlan`** — what the engine needs: `subject`, `script_text`, `voice_name`,
  `voice_rate`, `background_clips`, `clip_duration` (cut cadence), `CaptionStyle`,
  `music`, `aspect`. Maps 1:1 to MPT's `VideoParams`.
- **`VideoResult`** — `path`, `duration`, `brief`, `script`, `plan`, `timings`, `cost`, `job_id`.

## No API keys

Every layer runs locally and free:

| Layer | Tool | Key? |
|-------|------|------|
| Writer / Director | local LLM via **Ollama** (`llama3.1:8b`) | none |
| Voice | `edge-tts` (Microsoft neural voices) | none |
| Background footage | `yt-dlp` from no-copyright gameplay channels | none |
| Captions / render | vendored MoneyPrinterTurbo + `ffmpeg` | none |
| Trends | built-in topic bank (Reddit API optional) | none |

`writer.provider = "anthropic"` swaps in Claude if you want it; nothing else changes.

## Why formats are plugins

The Director is supposed to *choose* the format and eventually *invent* new ones. Keeping each
format as a self-contained module (prompt + asset logic + render recipe) means:
- adding a format = one file + one registry line;
- the Director can A/B formats and read per-format performance from `format_stats`;
- a future "format synthesizer" can emit a new module from a spec.

## Data & files

- `workspace/brainrotter.db` — state.
- `workspace/jobs/<job_id>/` — per-job scratch: `narration.mp3`, `words.json`, `captions.ass`, `render.mp4`.
- `workspace/out/` — finished, published-ready videos.
- `assets/backgrounds/<category>/` — user-supplied gameplay loops (subway, parkour, satisfying…).
- `assets/music/`, `assets/sfx/` — audio beds and stingers.
- `assets/cache/` — downloaded / generated assets (AI images, TTS cache).
