"""Install the isolated Stable Diffusion venv for generated visuals.

Run via `brainrotter visual-setup`. One-time: a CUDA PyTorch + `diffusers` +
an SDXL-Turbo checkpoint (~7 GB total, downloaded on the first render and
cached by huggingface). Resumable — re-run to continue.

Walled off under tools/imagegen/.venv so it can't disturb the main environment.
torch cu121 has wheels for Python 3.10-3.12, so any of those works as the base.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
from pathlib import Path

from ..config import get_settings
from .generator import GEN_DIR, GEN_PY, _INFER, _READY, is_installed

_SETUP_LOCK_PORT = 47658

WANTED_PY = ("3.11", "3.12", "3.10")
WINGET_PY = ("Python.Python.3.11", "3.11")

TORCH = ["torch", "torchvision"]
TORCH_INDEX = "https://download.pytorch.org/whl/cu121"

REQS = [
    "diffusers==0.31.0", "transformers==4.46.3", "accelerate==1.1.1",
    "safetensors==0.4.5", "pillow", "sentencepiece", "protobuf",
]


def _run(cmd, **kw):
    print("   $", " ".join(str(c) for c in cmd))
    return subprocess.run(cmd, check=True, **kw)


def _pip(vpy: str, args: list[str], *, tries: int = 4) -> None:
    cmd = [vpy, "-m", "pip", "install", "--retries", "10", "--timeout", "120", *args]
    for attempt in range(1, tries + 1):
        try:
            _run(cmd)
            return
        except subprocess.CalledProcessError:
            if attempt == tries:
                raise
            print(f"   pip failed (attempt {attempt}/{tries}) — retrying, cache kept")


def _py_version(exe: Path | str) -> str | None:
    try:
        out = subprocess.run(
            [str(exe), "-c", "import sys;print('%d.%d' % sys.version_info[:2])"],
            check=True, capture_output=True, text=True, timeout=30,
        )
        return out.stdout.strip()
    except (subprocess.SubprocessError, OSError):
        return None


def _candidate_pythons():
    local = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Python"
    for ver in WANTED_PY:
        tag = ver.replace(".", "")
        yield local / f"Python{tag}" / "python.exe"
        yield Path(f"C:/Python{tag}/python.exe")
        w = shutil.which(f"python{ver}")
        if w:
            yield Path(w)
    if shutil.which("py"):
        for ver in WANTED_PY:
            try:
                out = subprocess.run(["py", f"-{ver}", "-c", "import sys;print(sys.executable)"],
                                     check=True, capture_output=True, text=True, timeout=30)
                yield Path(out.stdout.strip())
            except (subprocess.SubprocessError, OSError):
                pass


def _find_base_python() -> str | None:
    seen: set[str] = set()
    for p in _candidate_pythons():
        key = str(p).lower()
        if key in seen:
            continue
        seen.add(key)
        if p.is_file() and (_py_version(p) or "") in WANTED_PY:
            return str(p)
    return None


def _winget_python() -> str | None:
    if not shutil.which("winget"):
        return None
    pkg_id, ver = WINGET_PY
    print(f"   installing Python {ver} via winget (one time)…")
    try:
        _run(["winget", "install", "-e", "--id", pkg_id, "--silent",
              "--accept-package-agreements", "--accept-source-agreements"])
    except subprocess.CalledProcessError as exc:
        print(f"   winget exit {exc.returncode} — checking anyway")
    return _find_base_python()


def _base_python() -> str:
    exe = _find_base_python() or _winget_python()
    if not exe:
        raise SystemExit(
            "Need Python 3.10, 3.11 or 3.12 for the image-gen venv.\n"
            "Install one (`winget install Python.Python.3.11`) and re-run "
            "`brainrotter visual-setup`."
        )
    print(f"   base interpreter: {exe}  (Python {_py_version(exe)})")
    return exe


def run(force: bool = False) -> int:
    cfg = get_settings().visuals
    GEN_DIR.mkdir(parents=True, exist_ok=True)

    if is_installed() and not force:
        print("Image generation is already installed and ready "
              f"({cfg.model}). Pass --force to reinstall.")
        return 0

    # Only one visual-setup at a time — a second run racing on the smoke-render
    # dir is what used to make this fail confusingly.
    lock = socket.socket()
    try:
        lock.bind(("127.0.0.1", _SETUP_LOCK_PORT))
        lock.listen(1)
    except OSError:
        print("Another `brainrotter visual-setup` is already running — "
              "watch that one instead.")
        return 1

    try:
        return _install(cfg)
    finally:
        lock.close()


def _install(cfg) -> int:
    print("1. isolated virtualenv (Python 3.10-3.12)")
    venv_dir = GEN_DIR / ".venv"
    ok = GEN_PY.is_file() and (_py_version(GEN_PY) or "") in WANTED_PY
    if not ok:
        if venv_dir.exists():
            print("   existing venv is unusable — rebuilding")
            shutil.rmtree(venv_dir, ignore_errors=True)
        _run([_base_python(), "-m", "venv", str(venv_dir)])
    vpy = str(GEN_PY)
    _pip(vpy, ["--upgrade", "pip", "wheel", "--quiet"])

    print("\n2. PyTorch (CUDA 12.1) — large download")
    _pip(vpy, [*TORCH, "--index-url", TORCH_INDEX])

    print("\n3. diffusers + friends")
    _pip(vpy, REQS)

    print(f"\n4. smoke render — pulls {cfg.model} (~7 GB, cached by huggingface)")
    probe = GEN_DIR / f"_probe_{os.getpid()}"
    probe.mkdir(parents=True, exist_ok=True)
    job = (probe / "job.json").resolve()
    job.write_text(json.dumps({
        "prompts": ["a red apple on a wooden table, product photo"],
        "out_dir": str(probe.resolve()), "model": cfg.model, "steps": cfg.steps,
        "guidance": cfg.guidance, "width": 512, "height": 512, "device": cfg.device,
        "negative_prompt": cfg.negative_prompt, "seed": 1,
    }), encoding="utf-8")
    try:
        _run([vpy, str(_INFER.resolve()), str(job)])
        made = any(probe.glob("img_*.png"))
    except subprocess.CalledProcessError:
        made = False
    finally:
        shutil.rmtree(probe, ignore_errors=True)
    if not made:
        print("   smoke render failed — see errors above")
        return 1

    _READY.write_text("ok", encoding="utf-8")
    print("\nImage generation installed. The Director can now pick 'generated' "
          "visuals (object_story first).")
    print("Turn it off with  [visuals] enabled = false  in config.toml.")
    return 0
