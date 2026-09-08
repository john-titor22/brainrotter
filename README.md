# Brainrotter

A software that **manufactures brainrot videos** — and, over time, learns to **invent its own**.

Brainrotter is a self-directing content factory for short-form vertical video. You run it;
it decides what to make, writes it, assembles it, renders it, publishes it, watches how it
performs, and adjusts. The long-term goal is a system that *understands* brainrot well enough
to generate genuinely novel formats, characters, and bits — not just fill in templates.

**No API keys.** Everything runs locally and for free: a local LLM via [Ollama](https://ollama.com)
writes the scripts, `edge-tts` does the voice, `yt-dlp` sources the background footage itself,
`ffmpeg` renders. Claude API is an optional opt-in, not a requirement.

## The three layers

| Layer | What it does | Status |
|-------|--------------|--------|
| **The Factory** | Turns a script + style spec into a finished 1080×1920 MP4: TTS narration, word-synced captions, background footage / AI visuals, SFX, cuts, render. | 🟡 building |
| **The Writer** | Generates the script and shot list for a chosen format (Reddit story, AI "Italian brainrot", etc.) using an LLM. | 🟡 building |
| **The Brain** | Decides *what* to make and *why* — which format, topic, hook, length, pacing — from trend signals and past performance. Later: an engagement model trained on real metrics that closes the loop. | 🔴 stub |

## What it does

Run it and the Director picks a format + topic itself, the Writer scripts it, and
a finished 1080×1920 MP4 lands in `workspace/out/` — no human steps. Operated from
a local dashboard (queue, previews, per-video rationale).

- **Formats:** `reddit_story` (AITA/revenge readalong), `ai_brainrot` (absurd
  invented creature lore), `anime_figure` (a real public figure recast as an
  anime protagonist — a lip-synced talking head of them narrates it, stacked on
  gameplay). The Director chooses per video; new formats are one file.
- **Writer:** local LLM via Ollama (`llama3.1:8b`; `aya-expanse:8b` for
  non-English). No key, no egress.
- **Languages:** the Director picks one per video from `[language]` weights —
  English, French, Arabic (MSA), and **Moroccan Darija** (`ary`, Arabic script,
  spoken by the Moroccan neural voice). Arabic/Darija captions are reshaped +
  bidi'd and drawn in a bundled Arabic font.
- **Voice + music:** the Director picks a specific voice *and* a specific music
  track per video, matched to the format, language and mood, and biased away
  from what the last few videos used — the feed stops sounding like one voice
  over one loop. Music is self-sourced by mood (`hype`/`tense`/`eerie`/`epic`/
  `chill`/`funny`), same as footage.
- **Footage:** self-sourced with `yt-dlp` — "no copyright" gameplay for most
  formats, clips *of the figure* for `anime_figure`. Cached and reused. Or drop
  your own into `assets/backgrounds/<category>/`.
- **Captions:** word-by-word "pop" from TTS timing (no Whisper).
- **Render:** `ffmpeg`, via a vendored copy of MoneyPrinterTurbo.

## Install

Requires **Python 3.11+**, **git**, **ffmpeg** on PATH, and **Node.js** (for
YouTube footage). Windows is the tested platform.

```powershell
git clone <this repo> Brainrotter
cd Brainrotter

python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\pip install -e .

.venv\Scripts\brainrotter setup        # vendors the engine, makes config + shortcut

# local model for the Writer  (no API key)
winget install Ollama.Ollama
ollama pull llama3.1:8b
ollama pull aya-expanse:8b             # only if you enable non-English languages

.venv\Scripts\brainrotter doctor       # check what's missing

# optional: talking-head figures for anime_figure (big one-time download)
.venv\Scripts\brainrotter avatar-setup
```

Then get background footage (see the Footage note below for the one-time cookie
step), and open the app:

```powershell
.venv\Scripts\brainrotter footage sync
```

Double-click the **Brainrotter** shortcut, or `brainrotter app`. Close the window
and the server stops; open it again to start back up.

CLI, if you prefer: `brainrotter run` (Director decides) or
`brainrotter run -f anime_figure -t "Gordon Ramsay" -n 3`.

> **Footage.** `footage sync` uses `yt-dlp` against YouTube uploads that creators
> publish as "no copyright / free to use" for edits. Downloading from YouTube is
> contrary to YouTube's ToS and per-creator terms vary — your call
> (`footage.allow_youtube = false` disables it).
>
> **YouTube needs two things now:**
>
> 1. **A cookie file.** Install the **"Get cookies.txt LOCALLY"** extension
>    (Chrome / Edge Web Store), open `youtube.com` while signed in → **Export** →
>    save as `assets/cookies.txt`. Picked up automatically; gitignored; re-export
>    every few weeks when it goes stale.
> 2. **A JS runtime** (Node.js or Deno) to solve YouTube's `n` challenge — the
>    harvester auto-detects `node` on PATH, and `pip install "yt-dlp[default]"`
>    installs the solver script.
>
> Clips you drop straight into `assets/backgrounds/<category>/` skip all of this.
>
> Downloads grab only the first ~2 min of each source and can be slow (YouTube
> throttles); they cache and get reused, so it's a one-time cost per game.
> `brainrotter footage sync` up front (or overnight) to fill the pool.
>
> **`anime_figure`** generates a lip-synced talking head of the figure narrating
> the script (SadTalker, run locally) and stacks it on top of gameplay — top
> figure, bottom gameplay, a fresh render every video. Install it once with
> `brainrotter avatar-setup` (~5 GB: a CUDA PyTorch + ~2 GB of checkpoints).
> Without it, the format falls back to downloaded footage *of the figure*
> (speeches, interviews) over gameplay. Same ToS caveat applies to both.

**Background variety.** The Director picks 1–2 games per video from a pool of
~12 (subway, parkour, GTA, Temple Run, Trackmania, Geometry Dash, slope, Roblox
obby, CS surf, satisfying, …), weighted *away* from what the last few videos
used, and the engine hard-cuts between them mid-video. Widen the pool by editing
`brainrotter/footage/registry.py` + each format's `BG_CATEGORIES`.

`brainrotter formats` shows per-format performance; `brainrotter trends` shows
what the Director currently sees.

**Languages.** Set `[language]` weights in `config.toml` and the Director rolls a
language per video:

```toml
[language]
weights = { en = 0.3, fr = 0.3, ary = 0.4 }   # English / French / Moroccan Darija
```

Codes: `en`, `fr`, `ar` (Modern Standard Arabic), `ary` (Darija — written in
Arabic script, spoken by the `ar-MA` neural voice; there is no keyless *true*
Darija TTS, so quality rides on the writer model). Pull `aya-expanse:8b` for
non-English — `llama3.1` is weak outside English. Force one with
`brainrotter run -l fr`. Arabic/Darija captions are letter-joined + bidi'd and
rendered in the bundled `NotoNaskhArabic-Bold.ttf`.

**Voice & music per video.** The Director picks a specific catalog voice and a
specific music track for every video (see `brainrotter/engine/voices.py`),
matched to format + language + mood and weighted away from the last few videos'
choices. Music is mood-tagged and self-sourced like footage:
`brainrotter music sync` (or it fetches a mood on first use), `brainrotter music
list`, or drop MP3s into `assets/music/<mood>/`.

**One video at a time.** The dashboard worker renders jobs strictly
sequentially — the next starts only when the current one finishes. Queue as many
as you like; control the flow with the dashboard's Pause / Clear buttons or
`brainrotter queue {status,pause,resume,clear}`. Run only **one** `brainrotter
serve` at a time (a machine-wide lock stops a second one from double-processing).

**Housekeeping.** `brainrotter queue clear-history` (or the dashboard button)
deletes finished jobs, their MP4s, and the engine's scratch folders — keeps the
3 most recent by default.

**As an app.** `brainrotter app` (or the **Brainrotter** shortcut on the Desktop
/ Start menu, which has the logo icon) opens the dashboard in a dedicated Chrome
window and ties the server's life to it — **close the window, the server stops;
open the shortcut, it starts.** Nothing runs in the background when it's closed.

The `extension/` toolbar add-on (see `extension/README.md`) is optional — a quick
queue badge + controls for when you also have a normal browser window open. It
can't start the server; use the app shortcut for that.

## Give it to someone else

```powershell
.\pack.ps1        # builds dist\Brainrotter-Setup.zip  (~1.3 MB)
```

Send them that zip (Drive / WeTransfer / Discord / the GitHub Release). They:

1. unzip it
2. open the `Brainrotter` folder
3. right-click **`install.ps1`** → **Run with PowerShell**

`install.ps1` winget-installs Python / git / ffmpeg / Node / Ollama, builds the
venv, vendors the engine, pulls `llama3.1:8b`, and drops a Desktop shortcut. The
only thing it can't automate is the YouTube `cookies.txt` (it prints how).

> The repo is private, so the GitHub Release download link only works for people
> you've given repo access. A file-share link works for anyone.

## Notes

- **No API keys.** Everything runs locally. `writer.provider = "anthropic"` in
  `config.toml` swaps in Claude if you want it; nothing else changes.
- **Footage & rights.** Downloading from YouTube is contrary to YouTube's ToS;
  per-creator terms vary. That's your call — `footage.allow_youtube = false`
  disables it and you supply clips yourself.
- **`anime_figure` is parody** of a public persona. The prompt blocks fabricated
  scandals, private-life claims, and casting other real people as villains. Still
  your responsibility what you publish.
- The `extension/` toolbar add-on is optional (queue badge + quick controls);
  see `extension/README.md`. It can't start the server — use the app shortcut.

See `ARCHITECTURE.md` for how the pieces fit and `ROADMAP.md` for status.
