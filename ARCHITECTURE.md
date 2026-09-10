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
| `brainrotter.db` | SQLite: `jobs`, `videos`, `metrics`, `format_stats`, `series`. Job queue + history. `jobs.series_id/part/seq` link a job to a series and order parts. |
| `brainrotter.visuals` | Local AI image generation (SDXL-Turbo via `diffusers`, isolated venv `tools/imagegen/.venv`, `brainrotter visual-setup`). `prompts.storyboard(brief, script)` → `(prompts, weights)`: the LLM writes one image prompt per beat from that beat's narration, anchored to `subject_look(topic, kind)` and the format's `SUBJECT_KIND`; `weights` = beat word counts. `generate(prompts, out_dir) -> {beat_idx: png}` via subprocess `_infer.py` (GPU-resident, falls to cpu-offload on OOM). `available()` False until installed. `orchestrator.produce` aligns images↔beats, sets `plan.visual_beat_weights`; `mpt_engine._storyboard_material` + `_ken_burns` build a single MP4 holding each still for its beat's spoken length, then MPT burns captions over it (pre-rendered TTS handed back via `voice_preview`, same trick as the talking head). |
| `brainrotter.series` | "Related story" mode. `create()` plans a story as an arc of up to `series.max_parts` (9) parts via the local LLM, locks voice + music; `enqueue()` creates the part-jobs; `build_part_brief()` assembles each part's `Brief` with `SeriesContext` (premise, story-so-far recap, per-part goal + CC b-roll queries); `record_part()` stores a recap for the next part; `abort()` stops a series if a part fails. |
| `brainrotter.trends.*` | Signal sources. `reddit.py` (hot posts by subreddit), `topic_bank.py` (curated fallback). Each returns `TrendSignal`s. |
| `brainrotter.footage.*` | Self-sources background gameplay with `yt-dlp` from "no copyright" channels. `registry.py` = per-category search queries; `harvester.py` = download → ffmpeg-trim to a small silent loop → cache in `assets/cache/footage/<cat>/`. |
| `brainrotter.director` | Consumes signals + `format_stats`, emits a `Brief` (incl. background category + style). v1: heuristic + local-LLM scoring. v2: learned model. |
| `brainrotter.writer` | `llm.py` — provider abstraction: **`ollama`** (local, default, no key) or `anthropic` (opt-in). `scriptwriter.py` turns a `Brief` into a `Script` (shared STORY RULES in `formats/base` enforce hook / logic / pacing / anti-generic), then an optional `_coherence_pass` fast edit for logic + payoff (`writer.coherence_pass`); offline template stub if no LLM. |
| `brainrotter.formats.*` | One module per format. Standard formats implement `writer_system_prompt()`, `writer_user_prompt(brief)`, `parse_script(raw)`, `build_plan(brief, script, clips) -> RenderPlan` and use the MPT engine. A format may instead (or also) define `render_video(brief, script, ctx: RenderContext) -> {path,duration,seconds}|None` to **own its entire render** (compilation cut, 2D animation, chat UI…) — `orchestrator.produce` calls it first and falls back to the standard path on `None`. Optional per-format hints: `DEFAULT_VISUAL_TREATMENT` (footage/generated), `SUBJECT_KIND` (object/person/creature), `VOICE_TAGS`, `MUSIC_MOODS`, `BG_CATEGORIES`. Registered in `registry.py`. |
| `brainrotter.assets` | Background footage library — index `assets/backgrounds/<category>/` and pick clips. |
| `brainrotter.engine.storyboard` | Native ffmpeg renderer (no MoviePy): `render()` for AI stills (Ken-Burns per beat), `render_over_gameplay()` for one continuous 9:16 gameplay clip, `finish()` = burn word-pop ASS captions + mix narration/ducked-music onto any silent video. ~10s vs MoviePy's minutes. `render_isolated`/MPT is now just the fallback + the anime_figure talking-head path. |
| `brainrotter.engine.movie` | For `movie_recap`: `find_movie()` (fuzzy-match `assets/movies/`), `extract_slice()`, `transcribe()` (faster-whisper, local), `build_montage()` (scene-detect → silent 9:16 shot montage). |
| `brainrotter.engine` | Adapter over the vendored MoneyPrinterTurbo. `render_plan(plan, job_id)` builds MPT's `VideoParams`, calls `app.services.task.start()` in-process, copies the finished MP4 to `workspace/out/`. `mpt_config.py` writes MPT's `config.toml` from our `Settings` before import; `voices.py` is the curated edge-tts catalog. |
| `brainrotter.publish.*` | Upload adapters. Stub for now. |
| `brainrotter.feedback` | `scorer.py` — engagement model. v1: records metrics + simple rollups. v2: predictive. |
| `brainrotter.orchestrator` | Ties it together: `produce(brief) -> VideoResult`, `run_once()`, `run_batch(n)`, `run_series()`. `produce` routes series parts to CC b-roll (`footage.ensure_narrative`) instead of gameplay. |
| `brainrotter.server` | FastAPI app + static dashboard (also a PWA — `/manifest.webmanifest`, installable). Wraps orchestrator, exposes the job queue, serialises rendering (`db.claim_next_job` + a machine-wide worker mutex on port 47654), pause/clear/clear-history. |
| `brainrotter.maintenance` | Housekeeping — purge finished jobs, delete their MP4s + orphans, clean engine scratch. |
| `brainrotter.cli` | `typer` CLI: `run` (`--series --parts N`), `serve`, `formats`, `trends`, `doctor`, `init`, `footage {sync,list}`, `music {sync,list}`, `queue {status,pause,resume,clear,clear-history}`, `series {list,show}`. |
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
