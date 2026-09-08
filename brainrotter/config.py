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
    # "ollama" = fully local, no API key (default). "anthropic" = Claude API.
    provider: str = "ollama"
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.1:8b"
    ollama_fast_model: str = "llama3.1:8b"
    # Used for any non-English script (French / Arabic / Darija). llama3.1 is
    # weak outside English; Aya is trained for exactly this. Pull it with
    # `ollama pull aya-expanse:8b`. Falls back to ollama_model if absent.
    multilingual_model: str = "aya-expanse:8b"
    anthropic_model: str = "claude-opus-5"
    anthropic_fast_model: str = "claude-sonnet-5"
    temperature: float = 0.95           # brainrot rewards creativity over precision


class LanguageCfg(BaseModel):
    # The Director picks one language per video from these weights. Codes:
    # en (English), fr (French), ar (Modern Standard Arabic), ary (Moroccan
    # Darija — written by the LLM in Arabic script, spoken by the ar-MA voice).
    weights: dict[str, float] = Field(default_factory=lambda: {"en": 1.0})
    # Darija-only: run a second LLM pass that rewrites the script into pure
    # street Darija (local models drift back to MSA on "epic" register). Costs
    # one extra fast call per ary video.
    darija_polish: bool = True

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
    ])


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


class FootageCfg(BaseModel):
    # Let Brainrotter download its own background footage from YouTube
    # "no copyright / free to use" gameplay channels. See docs for the ToS note.
    allow_youtube: bool = True
    max_resolution: int = 720           # background gets cropped + captioned over
    seconds_per_clip: int = 120         # trim each download to this (ffmpeg, post-dl)
    per_category: int = 2               # how many source videos to keep per category
    auto_sync: bool = True              # fetch on demand when a category is empty
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


class TtsCfg(BaseModel):
    voice: str = "en-US-AndrewNeural"
    rate: str = "+18%"
    pitch: str = "+0Hz"


class CaptionsCfg(BaseModel):
    style: str = "word_pop"
    font: str = "Impact"
    font_size: int = 96
    primary_color: str = "&H00FFFFFF"
    outline_color: str = "&H00000000"
    outline: int = 6
    max_words_on_screen: int = 3
    position: str = "center"


class WhisperCfg(BaseModel):
    model: str = "base"
    device: str = "auto"
    compute_type: str = "int8"


class AssetsCfg(BaseModel):
    backgrounds_dir: str = "assets/backgrounds"
    music_dir: str = "assets/music"
    sfx_dir: str = "assets/sfx"
    music_gain_db: float = -20.0


class DirectorCfg(BaseModel):
    default_weights: dict[str, float] = Field(
        default_factory=lambda: {
            "reddit_story": 0.45, "ai_brainrot": 0.3, "anime_figure": 0.25,
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


class Settings(BaseModel):
    out_dir: str = "workspace/out"
    writer: WriterCfg = WriterCfg()
    language: LanguageCfg = LanguageCfg()
    footage: FootageCfg = FootageCfg()
    music: MusicCfg = MusicCfg()
    avatar: AvatarCfg = AvatarCfg()
    video: VideoCfg = VideoCfg()
    tts: TtsCfg = TtsCfg()
    captions: CaptionsCfg = CaptionsCfg()
    whisper: WhisperCfg = WhisperCfg()
    assets: AssetsCfg = AssetsCfg()
    director: DirectorCfg = DirectorCfg()
    trends: TrendsCfg = TrendsCfg()

    # --- secrets / env ---
    anthropic_api_key: str | None = None
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
    def sfx_path(self) -> Path:
        return self._ensure(PROJECT_ROOT / self.assets.sfx_dir)

    @property
    def cache_path(self) -> Path:
        return self._ensure(PROJECT_ROOT / "assets" / "cache")

    @property
    def footage_path(self) -> Path:
        return self._ensure(PROJECT_ROOT / "assets" / "cache" / "footage")

    @property
    def music_cache_path(self) -> Path:
        return self._ensure(PROJECT_ROOT / "assets" / "cache" / "music")

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
    settings.reddit_client_id = os.getenv("REDDIT_CLIENT_ID") or settings.reddit_client_id
    settings.reddit_client_secret = (
        os.getenv("REDDIT_CLIENT_SECRET") or settings.reddit_client_secret
    )
    settings.reddit_user_agent = os.getenv("REDDIT_USER_AGENT") or settings.reddit_user_agent
    settings.ffmpeg_bin = os.getenv("FFMPEG_BIN") or settings.ffmpeg_bin
    return settings
