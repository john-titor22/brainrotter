"""Typed configuration loaded from config.toml + .env.

Load once with `get_settings()`; it is cached. Paths are resolved relative to the
project root (the directory containing config.toml / this package's parent).
"""

from __future__ import annotations

import functools
import os
import tomllib
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, Field

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class WriterCfg(BaseModel):
    # "ollama" = fully local, no key (default). "groq" = Groq's free API tier
    # (fast, Qwen3 27B — much stronger than local 8B, frees the GPU; needs
    # GROQ_API_KEY). "anthropic" = Claude API (needs ANTHROPIC_API_KEY).
    provider: str = "ollama"
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.1:8b"
    ollama_fast_model: str = "llama3.1:8b"
    # Darija ("ary") is routed to a dedicated local model REGARDLESS of provider
    # (general models — llama, qwen, aya — drift to MSA). Atlas-Chat is the
    # purpose-built Moroccan Darija model (Gemma-2 based). Pull it:
    #   ollama pull hf.co/mradermacher/Atlas-Chat-9B-GGUF:Q4_K_M   (~6 GB, fits 8 GB)
    # Falls back to the main provider if it isn't pulled / Ollama is down.
    darija_model: str = "hf.co/mradermacher/Atlas-Chat-9B-GGUF:Q4_K_M"
    multilingual_model: str = "aya-expanse:8b"     # legacy fallback for non-en/ary
    # Groq free tier — https://console.groq.com/keys . Generous limits, does not
    # train on your data. qwen3.8-27b: strong, 131k ctx, no reasoning overhead,
    # good brainrot voice + decent Darija. (gpt-oss-* are reasoning models that
    # eat the token budget on short calls — avoid for the fast helpers.)
    groq_model: str = "qwen/qwen3.8-27b"
    groq_fast_model: str = "qwen/qwen3.8-27b"
    anthropic_model: str = "claude-opus-5"
    anthropic_fast_model: str = "claude-sonnet-5"
    temperature: float = 0.95           # brainrot rewards creativity over precision
    # A local model on a busy machine (GPU also rendering) can take minutes on a
    # long script. Give it room, and retry once on a timeout before failing.
    request_timeout: int = 300
    # One extra fast pass that checks the script hangs together (each line
    # follows from the last, the ending pays off the hook) and tightens the
    # pacing. Costs ~15-30s on a local model; turn off for speed.
    coherence_pass: bool = True


class LanguageCfg(BaseModel):
    # The Director picks one language per video from these weights. Only two are
    # supported: en (English) and ary (Moroccan Darija — written by the LLM in
    # Arabic script, spoken by an ar-MA voice). Default English-only.
    weights: dict[str, float] = Field(default_factory=lambda: {"en": 1.0})
    # Darija-only: run a second LLM pass that rewrites the script into pure
    # street Darija (local models drift back to MSA on "epic" register). Costs
    # one extra fast call per ary video.
    darija_polish: bool = True
    # Speak Darija with a real Darija-fine-tuned XTTS-v2 voice instead of
    # edge-tts's MSA-accented ar-MA voice. Needs `brainrotter darija-tts-setup`
    # (~2 GB); falls back to edge-tts until then.
    darija_tts: bool = True
    darija_tts_timeout: int = 600

    @property
    def enabled(self) -> list[str]:
        return [c for c, w in self.weights.items() if w > 0] or ["en"]


class MusicCfg(BaseModel):
    # Self-sourced, mood-tagged background music (like footage). The Director
    # picks a mood per video and a specific track inside it, so the feed stops
    # sounding like one looped song.
    enabled: bool = True
    allow_youtube: bool = True          # download "no copyright" tracks with yt-dlp
    per_mood: int = 4                   # keep this many tracks per mood
    seconds_per_track: int = 90         # trim each download to this
    auto_sync: bool = True              # fetch a mood on demand when it's empty
    sync_budget_seconds: int = 150
    moods: list[str] = Field(default_factory=lambda: [
        "hype", "tense", "eerie", "epic", "chill", "funny",
        "sad", "dramatic", "nostalgic", "phonk", "dreamy", "quirky",
    ])
    # Per video, the Director writes a specific music brief ("dark trap for a
    # revenge story", "warped music-box for a haunted toy") and Brainrotter
    # fetches a track for exactly that, on top of the mood pool.
    per_video_query: bool = True
    per_video_tracks: int = 2           # how many to fetch per unique brief


class AvatarCfg(BaseModel):
    # Talking-head (SadTalker) for anime_figure. Off until `brainrotter
    # avatar-setup` has installed it; falls back to plain footage otherwise.
    enabled: bool = True
    device: str = "cuda"           # cuda | cpu
    preprocess: str = "full"       # crop | resize | full  (full keeps the whole photo)
    size: int = 256               # 256 (fast) | 512 (sharper, ~2x slower)
    # GFPGAN face restoration on every frame — sharper, but roughly triples the
    # per-video time (≈4 min -> ≈13 min on an 8 GB GPU). "" / "none" to skip it.
    enhancer: str = "gfpgan"
    top_fraction: float = 0.55     # how much of the 9:16 frame the head fills


class VisualsCfg(BaseModel):
    # Local AI image generation (Stable Diffusion via `diffusers`, in an isolated
    # venv — `brainrotter visual-setup`). When available, the Director can choose
    # a "generated" visual treatment: a still per script beat, generated from the
    # beat's on-screen description, then Ken-Burns zoomed + sequenced by the
    # engine. Until installed, `available()` is False and everything falls back
    # to footage — no behaviour change.
    enabled: bool = True
    # "pollinations" = free keyless FLUX via image.pollinations.ai (much more
    # realistic than local SDXL-Turbo — proper hands/forms; ~30-40s/image, needs
    # network). "local" = the isolated SDXL venv (`brainrotter visual-setup`).
    provider: str = "pollinations"
    pollinations_model: str = "flux"        # flux | flux-realism | turbo | ...
    pollinations_token: str = ""            # optional free token (image.pollinations.ai) for priority
    model: str = "stabilityai/sdxl-turbo"   # local provider only
    steps: int = 2                          # local SDXL-Turbo: 1-4
    guidance: float = 0.0                   # turbo models want 0.0
    width: int = 768
    height: int = 1152                      # ~9:16, lighter on VRAM than 832x1216
    max_images: int = 4                     # cap per video; beats past this are grouped onto one still
    min_seconds_per_image: float = 1.8      # floor so a 2-word beat still reads on screen
    device: str = "cuda"                    # cuda | cpu (cpu is very slow)
    gpu_resident: bool = False              # keep the model on the GPU (needs a card with room to spare)
    timeout_seconds: int = 900
    # object_story: give the object a cartoon face and lip-sync it (SadTalker).
    # Off by default — image models tend to draw a whole person instead of "the
    # object with a face", and Ken-Burns over realistic stills of the ACTUAL
    # object reads better anyway.
    talking_subject: bool = False
    negative_prompt: str = (
        "text, watermark, signature, blurry, lowres, low quality, jpeg artifacts, "
        "deformed, extra limbs, disfigured, cropped, frame, border"
    )
    # kept short — SDXL's text encoder only reads ~77 tokens, and the subject +
    # scene must fit first.
    style_suffix: str = "cinematic photo, moody lighting, shallow focus, film grain"


class FootageCfg(BaseModel):
    # Let Brainrotter download its own background footage from YouTube
    # "no copyright / free to use" gameplay channels. See docs for the ToS note.
    allow_youtube: bool = True
    max_resolution: int = 720           # background gets cropped + captioned over
    seconds_per_clip: int = 120         # trim each download to this (ffmpeg, post-dl)
    per_category: int = 2               # how many source videos to keep per category
    auto_sync: bool = True              # fetch on demand when a category is empty
    # After a footage video finishes, if fewer than this many gameplay
    # categories are cached, quietly download one more in the background so the
    # feed stops reusing the same 2-3 clips. Set 0 to disable.
    auto_grow_to: int = 5
    # YouTube gates most downloads behind a login. Provide cookies one of two ways:
    #  - cookies_file: path to a Netscape cookies.txt exported from your browser
    #    (most reliable — use the "Get cookies.txt LOCALLY" extension).
    #  - cookies_from_browser: "chrome" | "edge" | "brave" | "firefox" — read live
    #    from that browser. On Windows/Chromium you must fully close the browser
    #    first, and Chrome 127+ app-bound encryption can still block it.
    cookies_file: str = ""
    cookies_from_browser: str = ""
    sync_budget_seconds: int = 150      # give up a sync after this long


class VideoCfg(BaseModel):
    width: int = 1080
    height: int = 1920
    fps: int = 30
    min_seconds: int = 15
    max_seconds: int = 90


class WhisperCfg(BaseModel):
    # Only used to fill the vendored engine's config (its subtitle fallback).
    # Our own voice/caption/whisper paths don't read this.
    model: str = "base"
    device: str = "auto"
    compute_type: str = "int8"


class AssetsCfg(BaseModel):
    backgrounds_dir: str = "assets/backgrounds"
    music_dir: str = "assets/music"


class MovieCfg(BaseModel):
    # "movie_recap" format: drop a film in movies_dir, and `brainrotter run
    # -f movie_recap -t "<name>" --series` cuts it into Part 1..N — muted movie
    # montage + a brainrot narrator recapping each chunk (dialogue transcribed
    # locally with faster-whisper). Copyright note: same as footage — your call.
    movies_dir: str = "assets/movies"
    movie_seconds_per_part: int = 330   # how much film each part covers (~5.5 min)
    video_seconds_per_part: int = 55    # target length of each finished short
    max_parts: int = 24
    scene_threshold: float = 0.35       # ffmpeg scene-cut sensitivity (0-1)
    shot_seconds: float = 3.5           # how long to hold each detected shot in the montage
    whisper_model: str = "base"         # tiny | base | small — faster-whisper, auto-downloaded
    blur_pad: bool = True               # 16:9 -> 9:16 by blurred pillarbox (vs hard centre crop)
    music_volume: float = 0.06          # very low — the narrator carries it


class CompilationCfg(BaseModel):
    # "compilation" format: the Director picks a theme ("funny", "satisfying",
    # "animal fails", "wholesome"...) and Brainrotter sources short REAL clips
    # for it (Reddit API + yt-dlp), cuts a 2-5s slice from each, and hard-cuts
    # them into one ~35s vertical video with the clips' own audio plus a quiet
    # music bed. No narration, no captions, no generated visuals.
    enabled: bool = True
    target_seconds: int = 45            # finished video length (soft — a short
                                        # pool makes a shorter video, not repeats)
    clip_min_seconds: float = 3.0       # a clip shorter than this after trimming is skipped
    clip_max_seconds: float = 14.0      # clips shown ~in full; only overrun past this is cut
    pool_per_theme: int = 80            # source clips to keep cached per theme —
                                        # deep enough that many videos in a row
                                        # never repeat a clip
    fetch_per_build: int = 12           # max NEW downloads per sourcing pass
    candidates_per_fetch: int = 80      # post URLs to consider per sourcing pass
    recency_exclude: int = 3            # don't reuse a clip seen in the last N videos
    music_volume: float = 0.05          # very low bed; also ducks under clip audio
    min_source_seconds: float = 3.0     # skip sources shorter than this
    max_source_seconds: float = 1800.0  # skip only the truly huge (livestreams,
                                        # movies) — long "X compilation" uploads
                                        # ARE useful: we re-clip individual
                                        # moments out of them (like real comp
                                        # channels do)
    download_seconds: int = 90          # only fetch + analyse the first N s of any
                                        # source (keeps downloads small and moment
                                        # analysis from stalling on 150s decodes)
    single_clip_max: float = 42.0       # source <= this (after trim) = one clip;
                                        # longer = a compilation, split into moments
    blur_pad: bool = False              # 9:16 fit: False = centre-crop, True = blurred pillarbox
    # Reddit "top" windows to rotate through for variety (day|week|month|year|all)
    time_filters: list[str] = Field(default_factory=lambda: ["week", "month", "year"])
    allow_youtube: bool = True          # also pull from YouTube search, not only Reddit
    sync_budget_seconds: int = 420      # give up a sourcing pass after this long
    # Auto-review the finished cut with a local vision model (Ollama): drop
    # slices that are off-theme (e.g. game footage in a 'funny' comp), black, or
    # frozen, then re-pick + re-render once. Needs a vision model pulled
    # (llama3.2-vision / llava); structural checks run regardless.
    review: bool = True
    vision_model: str = "llama3.2-vision:11b"


class SeriesCfg(BaseModel):
    # "Related story" mode: the Director plans one story as an arc of parts and
    # produces Part 1..N as sequential videos, with continuity carried between
    # them. The story must WRAP UP by the last part.
    max_parts: int = 9              # hard ceiling on parts per series
    default_parts: int = 9          # target when the caller doesn't say
    min_parts: int = 3              # planner won't go below this
    lock_voice: bool = True         # same narrator across the whole series
    target_seconds: int = 40        # per part
    # Series backgrounds are Creative-Commons b-roll that fits the narrative
    # (not gameplay). Require the CC "reuse allowed" licence on downloads; if a
    # part finds nothing CC it falls back to gameplay footage so it still renders.
    require_cc: bool = True
    broll_per_part: int = 3


class PublishCfg(BaseModel):
    # Publish finished videos as Shorts. Providers you've set up (own developer
    # apps = unlimited) OR "upload_post" (upload-post.com, 10/mo free).
    enabled: bool = False
    providers: list[str] = Field(default_factory=lambda: ["youtube", "instagram",
                                                          "facebook", "tiktok"])
    youtube_privacy: str = "public"       # public | unlisted | private
    youtube_category: str = "24"          # 24 = Entertainment, 22 = People & Blogs
    tiktok_privacy: str = "auto"          # auto = public if the app is audited, else self-only
    ig_share_to_feed: bool = True
    # Publish every finished video automatically.
    auto_publish: bool = False
    # After a successful publish, delete the local .mp4 to free space — the
    # DB keeps the row + the platform links.
    delete_local_after_publish: bool = True
    hashtags: int = 4                     # how many of the script's hashtags to append
    title_max: int = 90
    # temp host for platforms that fetch the video from a URL (Instagram):
    # "catbox" (permanent, 200 MB) | "litterbox" (72 h, then auto-deletes)
    upload_host: str = "litterbox"


class DirectorCfg(BaseModel):
    default_weights: dict[str, float] = Field(
        default_factory=lambda: {
            "reddit_story": 0.30, "ai_brainrot": 0.20,
            "anime_figure": 0.16, "object_story": 0.18, "compilation": 0.16,
        }
    )
    subreddits: list[str] = Field(
        default_factory=lambda: [
            "AmItheAsshole",
            "tifu",
            "pettyrevenge",
            "MaliciousCompliance",
            "confession",
        ]
    )
    exploration: float = 0.2


class TrendsCfg(BaseModel):
    reddit_time_filter: str = "day"
    reddit_limit: int = 25
    # Pull fresh topics from the open web (no API keys): Reddit's public JSON,
    # Google Trends RSS, Wikipedia's most-read. Keeps the Director from cycling
    # the same curated topic bank.
    use_web: bool = True
    geo: str = "US"                     # Google Trends region (US, GB, MA, FR, …)
    web_cache_minutes: int = 90         # don't re-fetch the same feeds every job


class Settings(BaseModel):
    out_dir: str = "workspace/out"
    writer: WriterCfg = WriterCfg()
    language: LanguageCfg = LanguageCfg()
    footage: FootageCfg = FootageCfg()
    visuals: VisualsCfg = VisualsCfg()
    music: MusicCfg = MusicCfg()
    avatar: AvatarCfg = AvatarCfg()
    video: VideoCfg = VideoCfg()
    whisper: WhisperCfg = WhisperCfg()
    assets: AssetsCfg = AssetsCfg()
    movie: MovieCfg = MovieCfg()
    compilation: CompilationCfg = CompilationCfg()
    series: SeriesCfg = SeriesCfg()
    director: DirectorCfg = DirectorCfg()
    trends: TrendsCfg = TrendsCfg()
    publish: PublishCfg = PublishCfg()

    # --- secrets / env ---
    anthropic_api_key: str | None = None
    groq_api_key: str | None = None
    upload_post_api_key: str | None = None
    upload_post_user: str | None = None
    # native publishing (own developer apps — unlimited)
    youtube_client_id: str | None = None
    youtube_client_secret: str | None = None
    meta_app_id: str | None = None
    meta_app_secret: str | None = None
    meta_ig_user_id: str | None = None      # numeric IG Business account id
    meta_fb_page_id: str | None = None      # numeric FB Page id
    tiktok_client_key: str | None = None
    tiktok_client_secret: str | None = None
    reddit_client_id: str | None = None
    reddit_client_secret: str | None = None
    reddit_user_agent: str = "brainrotter/0.1"
    ffmpeg_bin: str = "ffmpeg"

    # --- derived paths ---
    @property
    def root(self) -> Path:
        return PROJECT_ROOT

    @property
    def workspace(self) -> Path:
        return self._ensure(PROJECT_ROOT / "workspace")

    @property
    def jobs_dir(self) -> Path:
        return self._ensure(self.workspace / "jobs")

    @property
    def out_path(self) -> Path:
        return self._ensure(PROJECT_ROOT / self.out_dir)

    @property
    def db_path(self) -> Path:
        return self.workspace / "brainrotter.db"

    @property
    def backgrounds_path(self) -> Path:
        return self._ensure(PROJECT_ROOT / self.assets.backgrounds_dir)

    @property
    def music_path(self) -> Path:
        return self._ensure(PROJECT_ROOT / self.assets.music_dir)

    @property
    def cache_path(self) -> Path:
        return self._ensure(PROJECT_ROOT / "assets" / "cache")

    @property
    def footage_path(self) -> Path:
        return self._ensure(PROJECT_ROOT / "assets" / "cache" / "footage")

    @property
    def music_cache_path(self) -> Path:
        return self._ensure(PROJECT_ROOT / "assets" / "cache" / "music")

    @property
    def visuals_cache_path(self) -> Path:
        return self._ensure(PROJECT_ROOT / "assets" / "cache" / "visuals")

    @property
    def movies_path(self) -> Path:
        return self._ensure(PROJECT_ROOT / self.movie.movies_dir)

    @property
    def compilation_cache_path(self) -> Path:
        return self._ensure(PROJECT_ROOT / "assets" / "cache" / "compilation")

    @staticmethod
    def _ensure(p: Path) -> Path:
        p.mkdir(parents=True, exist_ok=True)
        return p


def _load_toml() -> dict:
    path = PROJECT_ROOT / "config.toml"
    if not path.exists():
        path = PROJECT_ROOT / "config.example.toml"
    if not path.exists():
        return {}
    with path.open("rb") as fh:
        raw = tomllib.load(fh)
    # Flatten the [general] table to top level.
    general = raw.pop("general", {})
    return {**general, **raw}


@functools.lru_cache
def get_settings() -> Settings:
    load_dotenv(PROJECT_ROOT / ".env")
    data = _load_toml()
    settings = Settings(**data)
    settings.anthropic_api_key = os.getenv("ANTHROPIC_API_KEY") or settings.anthropic_api_key
    settings.groq_api_key = os.getenv("GROQ_API_KEY") or settings.groq_api_key
    settings.upload_post_api_key = os.getenv("UPLOAD_POST_API_KEY") or settings.upload_post_api_key
    settings.upload_post_user = os.getenv("UPLOAD_POST_USER") or settings.upload_post_user
    for attr, envk in (
        ("youtube_client_id", "YOUTUBE_CLIENT_ID"),
        ("youtube_client_secret", "YOUTUBE_CLIENT_SECRET"),
        ("meta_app_id", "META_APP_ID"),
        ("meta_app_secret", "META_APP_SECRET"),
        ("meta_ig_user_id", "META_IG_USER_ID"),
        ("meta_fb_page_id", "META_FB_PAGE_ID"),
        ("tiktok_client_key", "TIKTOK_CLIENT_KEY"),
        ("tiktok_client_secret", "TIKTOK_CLIENT_SECRET"),
    ):
        setattr(settings, attr, os.getenv(envk) or getattr(settings, attr))
    settings.reddit_client_id = os.getenv("REDDIT_CLIENT_ID") or settings.reddit_client_id
    settings.reddit_client_secret = (
        os.getenv("REDDIT_CLIENT_SECRET") or settings.reddit_client_secret
    )
    settings.reddit_user_agent = os.getenv("REDDIT_USER_AGENT") or settings.reddit_user_agent
    settings.ffmpeg_bin = os.getenv("FFMPEG_BIN") or settings.ffmpeg_bin
    return settings
