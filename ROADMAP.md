# Brainrotter — Roadmap

## Phase 0 — Skeleton  ✅
- Project structure, config, domain models, SQLite, CLI, dashboard.

## Phase 1 — The Factory works end to end  ✅
Goal: `brainrotter run --format reddit_story --topic X` produces a watchable MP4.
- [x] Vendored MoneyPrinterTurbo as the render engine (`vendor/`, MIT).
- [x] `engine/` adapter — `RenderPlan` → in-process `task.start()` → MP4 in `workspace/out/`.
- [x] `engine/mpt_config.py` — generate MPT config from our `Settings`.
- [x] `engine/voices.py` — curated edge-tts catalog.
- [x] `assets.py` — index `assets/backgrounds/<category>/`, pick clips.
- [x] `formats/reddit_story.py`, `formats/ai_brainrot.py` — prompts + `build_plan`.
- [x] `writer/scriptwriter.py` — Brief → Script via Claude, offline stub fallback.
- [x] Smoke test: CLI + dashboard both produce videos, no manual steps.

## Phase 1.5 — No API keys  🟡
Goal: full pipeline runs local + free, no keys anywhere.
- [x] `writer/llm.py` — provider abstraction; **Ollama** (local) default, Anthropic opt-in.
- [x] `footage/` — self-source background gameplay with `yt-dlp` + ffmpeg trim + cache.
- [x] `assets.py` merges hand-added + downloaded footage; auto-syncs empty categories.
- [x] `brainrotter footage sync|list`, `doctor` shows Ollama + footage status.
- [ ] Validate with Ollama actually installed (llama3.1:8b) — script quality pass.
- [ ] Tune footage download: file-size cap, vertical-format preference, dedupe across categories.
- [ ] Seed the footage registry with more categories + verified sources.

## Phase 2 — The Director chooses  🟡
Goal: `brainrotter run` with no args makes a sensible video.
- [x] `trends/topic_bank.py` — curated fallback topics tagged by format fit.
- [x] `trends/reddit.py` — hot posts from configured subreddits (needs API keys).
- [x] `director/director.py` — format allocation (config prior + perf EWMA + ε-explore),
      signal→topic, background-category choice, angle/hook via local LLM. Rationale logged + shown.
- [ ] Validate Director quality with a real local model (currently stub-tested).
- [x] `formats/anime_figure.py` — real public figure → anime-protagonist brainrot.
- [ ] Local AI image generation for visuals (SD/ComfyUI) — anime portraits of the
      figure for `anime_figure`, creature art for `ai_brainrot`.
- [ ] Tune per-format style priors from early results.

## Phase 3 — Automation + operations  🟡
- [x] Job queue + background worker (`brainrotter serve`).
- [x] Dashboard: queue jobs, watch progress, preview videos, see Director rationale.
- [ ] Approve/reject + re-roll from the dashboard.
- [ ] Batch/overnight mode polish (`brainrotter run --count N`).
- [ ] Publish adapters: TikTok, YT Shorts, Reels (MPT bundles an upload-post integration to wire in).

## Phase 4 — The Brain / feedback loop  🔴
- [ ] `feedback/` — pull post-publish metrics on a schedule.
- [ ] `format_stats` rollups: per format/topic/hook → retention, view velocity.
- [ ] Director consumes stats; shifts allocation toward winners.
- [ ] Engagement predictor: score a Script *before* rendering; kill weak ones.

## Phase 5 — Understanding brainrot  🔴
- [ ] "Brainrot corpus": labelled examples (hook type, pacing, audio meme, visual trope).
- [ ] Feature extraction from top-performing videos (ours + scraped references).
- [ ] Format synthesizer: Director proposes a *new* format module from learned patterns;
      it gets A/B'd against incumbents.
- [ ] Self-tuning style knobs (caption speed, zoom cadence, voice pitch) via bandit search.

## Cross-cutting / infra
- [ ] Cost tracking per video (LLM tokens, any paid APIs).
- [ ] Local caching for TTS + AI assets.
- [ ] `plagiarism`/originality check on generated scripts before publish.
- [ ] Content-safety filter (platform TOS, copyright on bg footage).
