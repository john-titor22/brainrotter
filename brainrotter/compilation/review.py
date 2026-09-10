"""Final QA on the assembled slices before they're concatenated.

Two layers, both best-effort (never raise — a failed check just passes the slice):

  structural  — ffmpeg blackdetect / freezedetect: catches black frames, frozen
                clips, dead slices.  Always runs.
  content     — a local vision model (Ollama, e.g. llama3.2-vision) looks at a
                labelled grid of one frame per slice and flags anything that
                isn't on-theme: video-game footage, screenshots, unrelated
                content.  Runs only if a vision model is pulled.

``review(slices, theme_key)`` returns ``{slice_index: reason}`` for every slice
that should be dropped.
"""

from __future__ import annotations

import base64
import io
import json
import logging
import re
import shutil
import subprocess
from pathlib import Path

import httpx

from ..config import get_settings
from . import themes as _themes

log = logging.getLogger("brainrotter.compilation")


def _ff() -> str:
    return shutil.which(get_settings().ffmpeg_bin) or "ffmpeg"


# --- structural -------------------------------------------------------------

def _structural_one(slice_path: Path) -> str | None:
    try:
        r = subprocess.run(
            [_ff(), "-hide_banner", "-i", str(slice_path),
             "-vf", "blackdetect=d=0.5:pic_th=0.98,freezedetect=n=-55dB:d=1.2",
             "-map", "0:v:0", "-f", "null", "-"],
            capture_output=True, text=True, timeout=90,
        ).stderr
    except Exception:
        return None
    dur = _probe(slice_path)
    black = sum(float(b) - float(a)
                for a, b in re.findall(r"black_start:([\d.]+).*?black_end:([\d.]+)", r))
    if dur > 0 and black / dur > 0.35:
        return f"{black/dur:.0%} black"
    freezes = re.findall(r"freeze_start:\s*([\d.]+).*?freeze_end:\s*([\d.]+)", r, re.S)
    frozen = sum(float(b) - float(a) for a, b in freezes)
    if dur > 0 and frozen / dur > 0.5:
        return f"frozen {frozen:.1f}s"
    return None


def structural_flags(slices: list[Path]) -> dict[int, str]:
    out: dict[int, str] = {}
    for i, p in enumerate(slices):
        why = _structural_one(p)
        if why:
            out[i] = why
    return out


def _probe(path: Path) -> float:
    from .sources import _probe_duration

    return _probe_duration(path)


# --- content (vision) ------------------------------------------------------

_VISION_CANDIDATES = ("llama3.2-vision:11b", "llama3.2-vision", "llava:13b",
                      "llava:latest", "llava", "bakllava", "moondream")


def _ollama_models() -> list[str]:
    try:
        url = get_settings().writer.ollama_url.rstrip("/")
        r = httpx.get(f"{url}/api/tags", timeout=3)
        return [m["name"] for m in r.json().get("models", [])]
    except Exception:
        return []


_GROQ_VISION = "qwen/qwen3.8-27b"       # Groq's qwen3 chat models accept images


def _groq_key() -> str | None:
    return get_settings().groq_api_key or None


def vision_model() -> str | None:
    """Which vision model to use: Groq's (free, no download, preferred if a key
    is set) else a local Ollama vision model if one is pulled."""
    if _groq_key():
        return f"groq:{_GROQ_VISION}"
    cfg = get_settings().compilation
    have = _ollama_models()
    if cfg.vision_model in have:
        return cfg.vision_model
    for m in _VISION_CANDIDATES:
        if m in have:
            return m
    for h in have:
        if any(h.startswith(c.split(":")[0]) for c in _VISION_CANDIDATES):
            return h
    return None


def vision_available() -> bool:
    return get_settings().compilation.review and vision_model() is not None


def _grid(slices: list[Path], work: Path) -> tuple[bytes, int] | None:
    """One frame per slice, numbered, tiled into a single JPEG. Returns
    (jpeg_bytes, cols)."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except Exception:
        return None
    ff = _ff()
    tiles = []
    for i, p in enumerate(slices):
        f = work / f"rev_{i:02d}.jpg"
        mid = max(0.1, _probe(p) / 2)
        try:
            subprocess.run(
                [ff, "-hide_banner", "-nostats", "-ss", f"{mid:.2f}", "-i", str(p),
                 "-frames:v", "1", "-vf", "scale=320:-2", "-y", str(f)],
                capture_output=True, timeout=30,
            )
            if f.is_file():
                tiles.append((i, Image.open(f).convert("RGB")))
        except Exception:
            continue
    if len(tiles) < 3:
        return None
    tw = 320
    th = max(im.height for _, im in tiles)
    cols = 3 if len(tiles) <= 9 else 4
    rows = (len(tiles) + cols - 1) // cols
    canvas = Image.new("RGB", (cols * tw, rows * th), (20, 20, 20))
    draw = ImageDraw.Draw(canvas)
    try:
        font = ImageFont.truetype("arial.ttf", 34)
    except Exception:
        font = ImageFont.load_default()
    for k, (idx, im) in enumerate(tiles):
        x, y = (k % cols) * tw, (k // cols) * th
        canvas.paste(im, (x, y))
        draw.rectangle([x + 4, y + 4, x + 52, y + 44], fill=(0, 0, 0))
        draw.text((x + 12, y + 6), str(idx + 1), fill=(255, 255, 0), font=font)
    buf = io.BytesIO()
    canvas.save(buf, format="JPEG", quality=80)
    return buf.getvalue(), cols


_SYS = (
    "You are a strict QA checker for a short-form video compilation. You see a "
    "grid of numbered still frames — one per clip in the cut. The compilation "
    "is meant to be: {theme}.\n"
    "FLAG a frame's number if ANY of these are true:\n"
    "  - it is video-game footage / a screen recording / gameplay HUD\n"
    "  - it is a screenshot, a text post, a meme image, chat/DMs, or a webpage\n"
    "  - it is an ad, a channel intro / outro / subscribe screen, or a title card\n"
    "  - it is a person sitting and talking to camera (reaction / commentary), "
    "not actual footage of the thing\n"
    "  - its content is unrelated to '{theme}'\n"
    "  - it shows the SAME clip / scene / moment as another numbered frame in "
    "the grid (near-duplicate) — flag the LATER number\n"
    "Real phone/camera footage that fits the theme is GOOD even if low quality "
    "or slightly blurry. When unsure, do NOT flag.\n"
    "Reply ONLY JSON: {\"drop\":[{\"n\":<number>,\"why\":\"<3-6 words>\"}]}. "
    "Empty list if every frame fits and none repeat."
)


def _ask_groq(sys_prompt: str, user_prompt: str, img_b64: str) -> str | None:
    key = _groq_key()
    if not key:
        return None
    body = {
        "model": _GROQ_VISION,
        "temperature": 0.0,
        "max_tokens": 500,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": [
                {"type": "text", "text": user_prompt},
                {"type": "image_url",
                 "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}},
            ]},
        ],
    }
    try:
        r = httpx.post("https://api.groq.com/openai/v1/chat/completions",
                       json=body, timeout=90,
                       headers={"Authorization": f"Bearer {key}"})
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]
    except Exception as exc:  # noqa: BLE001
        log.warning("compilation Groq vision failed: %s", exc)
        return None


def _ask_ollama(model: str, sys_prompt: str, user_prompt: str, img_b64: str) -> str | None:
    body = {
        "model": model, "stream": False, "format": "json",
        "messages": [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": user_prompt, "images": [img_b64]},
        ],
        "options": {"temperature": 0.0, "num_predict": 400},
    }
    try:
        url = get_settings().writer.ollama_url.rstrip("/") + "/api/chat"
        r = httpx.post(url, json=body, timeout=180)
        r.raise_for_status()
        return (r.json().get("message", {}) or {}).get("content", "")
    except Exception as exc:  # noqa: BLE001
        log.warning("compilation Ollama vision failed: %s", exc)
        return None


def content_flags(slices: list[Path], theme_key: str, work: Path) -> dict[int, str]:
    model = vision_model()
    if not model:
        return {}
    grid = _grid(slices, work)
    if not grid:
        return {}
    img_b64 = base64.b64encode(grid[0]).decode()
    theme = _themes.get(theme_key).label
    sys_prompt = _SYS.replace("{theme}", theme)
    user_prompt = (f"This compilation is: {theme}. Which numbered frames do not "
                   f"belong?")

    raw = (_ask_groq(sys_prompt, user_prompt, img_b64) if model.startswith("groq:")
           else _ask_ollama(model, sys_prompt, user_prompt, img_b64))
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except Exception:
        m = re.search(r"\{.*\}", raw, re.S)
        if not m:
            return {}
        try:
            data = json.loads(m.group(0))
        except Exception:
            return {}
    out: dict[int, str] = {}
    for item in (data.get("drop") or []):
        try:
            n = int(item.get("n")) - 1
        except (TypeError, ValueError):
            continue
        if 0 <= n < len(slices):
            out[n] = str(item.get("why", "off-theme"))[:60]
    return out


# --- combined ------------------------------------------------------------

def review(slices: list[Path], theme_key: str, work: Path) -> dict[int, str]:
    if not get_settings().compilation.review:
        return {}
    flags = structural_flags(slices)
    # don't waste a vision call if structural already nuked half the video
    if len(flags) < len(slices) * 0.5:
        for i, why in content_flags(slices, theme_key, work).items():
            flags.setdefault(i, why)
    if flags:
        log.info("compilation review dropped %d/%d slice(s): %s",
                 len(flags), len(slices),
                 ", ".join(f"{i+1}:{w}" for i, w in sorted(flags.items())))
    return flags
