"""Render stills from prompts — free keyless FLUX (Pollinations) by default, or
the isolated local SDXL venv."""

from __future__ import annotations

import json
import logging
import subprocess
import time
import urllib.parse
from pathlib import Path

import httpx

from ..config import PROJECT_ROOT, get_settings

log = logging.getLogger("brainrotter.visuals")

GEN_DIR = PROJECT_ROOT / "tools" / "imagegen"
GEN_PY = GEN_DIR / ".venv" / "Scripts" / "python.exe"
_INFER = Path(__file__).parent / "_infer.py"
_MARKER = "@@IMAGES@@"
_READY = GEN_DIR / ".ready"          # written by visual-setup after a smoke render


class VisualError(RuntimeError):
    pass


def _provider() -> str:
    return (get_settings().visuals.provider or "pollinations").lower()


def is_installed() -> bool:
    """Local SDXL venv present (only relevant for provider='local')."""
    return GEN_PY.is_file() and _INFER.is_file() and _READY.is_file()


def available() -> bool:
    if not get_settings().visuals.enabled:
        return False
    if _provider() == "pollinations":
        return True                      # keyless HTTP — assume reachable
    return is_installed()


def status() -> str:
    s = get_settings().visuals
    if _provider() == "pollinations":
        return f"ready (Pollinations {s.pollinations_model}, free/keyless)"
    if not GEN_PY.is_file():
        return "local: not installed — `brainrotter visual-setup` (or set visuals.provider=pollinations)"
    if not _READY.is_file():
        return "local: venv present but no model — re-run `brainrotter visual-setup`"
    return f"local ready ({s.model}, {s.width}x{s.height})"


# --- Pollinations (free keyless FLUX) ------------------------------------

def _pollinations(prompts: list[str], out_dir: Path, *, seed: int | None,
                  negative_extra: str = "") -> dict[int, Path]:
    from PIL import Image

    cfg = get_settings().visuals
    seed0 = seed if seed is not None else int(time.time()) & 0x7FFFFFFF
    neg = (cfg.negative_prompt + (negative_extra or "")).strip(", ")
    out: dict[int, Path] = {}
    headers = {"User-Agent": "brainrotter/0.1"}
    if cfg.pollinations_token:
        headers["Authorization"] = f"Bearer {cfg.pollinations_token}"
    with httpx.Client(timeout=120, headers=headers, follow_redirects=True) as c:
        for i, prompt in enumerate(prompts):
            full = f"{prompt}. negative: {neg}" if neg else prompt
            url = ("https://image.pollinations.ai/prompt/"
                   + urllib.parse.quote(full[:1800], safe=""))
            params = {"width": cfg.width, "height": cfg.height,
                      "model": cfg.pollinations_model, "nologo": "true",
                      "safe": "false", "seed": seed0 + i * 7919}
            dst = out_dir / f"img_{i:02d}.png"
            for attempt in range(3):
                try:
                    r = c.get(url, params=params)
                    if r.status_code in (429, 500, 502, 503, 504):
                        time.sleep(4.0 * (attempt + 1))
                        continue
                    r.raise_for_status()
                    data = r.content
                    if len(data) < 3000:
                        time.sleep(3.0)
                        continue
                    dst.write_bytes(data)
                    with Image.open(dst) as im:      # validate + normalise to PNG
                        im = im.convert("RGB")
                        if min(im.size) < 256:
                            raise ValueError("too small")
                        im.save(dst, format="PNG")
                    out[i] = dst
                    break
                except Exception as exc:  # noqa: BLE001
                    log.warning("pollinations image %d attempt %d: %s", i, attempt, exc)
                    time.sleep(3.0)
    return out


def generate(prompts: list[str], out_dir: Path, *, seed: int | None = None,
             negative_extra: str = "") -> dict[int, Path]:
    """Render one PNG per prompt into ``out_dir``. Returns {prompt_index: path}
    for the images that decoded. Empty dict on total failure (caller falls back
    to footage)."""
    if not prompts:
        return {}
    out_dir.mkdir(parents=True, exist_ok=True)

    if _provider() == "pollinations":
        got = _pollinations(prompts, out_dir, seed=seed, negative_extra=negative_extra)
        if got or not is_installed():
            return got
        log.warning("pollinations produced nothing — falling back to local SDXL")

    if not is_installed():
        raise VisualError("no image provider available (set visuals.provider=pollinations "
                          "or run `brainrotter visual-setup`)")

    cfg = get_settings().visuals
    out_dir.mkdir(parents=True, exist_ok=True)
    job = {
        "prompts": prompts,
        "out_dir": str(out_dir.resolve()),
        "model": cfg.model,
        "steps": cfg.steps,
        "guidance": cfg.guidance,
        "width": cfg.width,
        "height": cfg.height,
        "device": cfg.device,
        "gpu_resident": bool(getattr(cfg, "gpu_resident", False)),
        "negative_prompt": (cfg.negative_prompt + (negative_extra or "")).strip(", "),
        "seed": seed if seed is not None else int(time.time()) & 0x7FFFFFFF,
    }
    job_file = (out_dir / "job.json").resolve()
    job_file.write_text(json.dumps(job), encoding="utf-8")

    try:
        subprocess.run(
            [str(GEN_PY), str(_INFER.resolve()), str(job_file)],
            capture_output=True, text=True, timeout=cfg.timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        pass
    # _infer.py names every output img_<promptindex>.png, so we can just scan
    # the dir — no need to parse stdout, and a timeout still leaves partials.
    return _scan(out_dir)


def _scan(out_dir: Path) -> dict[int, Path]:
    """{prompt index: path} for every img_NN.png that fully decodes. A render
    killed mid-save (VRAM pressure, timeout) can leave a truncated PNG that
    breaks the engine downstream — those are dropped here."""
    from PIL import Image

    out: dict[int, Path] = {}
    for p in sorted(out_dir.glob("img_*.png")):
        try:
            idx = int(p.stem.split("_", 1)[1])
        except (ValueError, IndexError):
            continue
        if not (p.is_file() and p.stat().st_size > 5_000):
            continue
        try:
            with Image.open(p) as im:
                im.load()
                if min(im.size) >= 256:
                    out[idx] = p
        except Exception:
            continue
    return out
