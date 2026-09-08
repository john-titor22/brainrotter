"""Install SadTalker into its own isolated virtualenv + fetch its checkpoints.

Run via `brainrotter avatar-setup`. Big one-time download (~2 GB checkpoints +
a CUDA PyTorch, so 4-5 GB total). Resumable — re-run to pick up where it left
off. SadTalker's dependency set is pinned to its last known-good combo.

SadTalker's pinned stack (torch 2.0.1, numpy 1.23, numba 0.56 …) only has
wheels for Python 3.8-3.11, so the isolated venv is built from a Python 3.10
interpreter — found on the machine if present, otherwise winget-installed. The
rest of Brainrotter is unaffected; this venv is walled off under tools/.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import urllib.request
from pathlib import Path

from .sadtalker import SADTALKER_DIR, SADTALKER_PY

REPO = "https://github.com/OpenTalker/SadTalker.git"

# Interpreters SadTalker's pinned wheels exist for, best first.
WANTED_PY = ("3.10", "3.11")
WINGET_PY = ("Python.Python.3.10", "3.10")

# SadTalker's tested torch (CUDA 11.8) — matches its own requirements notes.
TORCH = [
    "torch==2.0.1+cu118", "torchvision==0.15.2+cu118", "torchaudio==2.0.2+cu118",
]
TORCH_INDEX = "https://download.pytorch.org/whl/cu118"

# a slimmer, de-conflicted version of SadTalker/requirements.txt.
# gradio is dropped on purpose — it's only for SadTalker's web UI (we drive
# inference.py directly), and its version pins fight everything else on 3.10.
REQS = [
    "numpy==1.23.4", "face_alignment==1.3.5", "imageio==2.19.3",
    "imageio-ffmpeg==0.4.7", "librosa==0.9.2", "numba==0.56.4", "resampy==0.3.1",
    "pydub==0.25.1", "scipy==1.10.1", "kornia==0.6.8", "tqdm", "yacs==0.1.8",
    "pyyaml", "joblib==1.1.0", "scikit-image==0.19.3", "basicsr==1.4.2",
    "facexlib==0.3.0", "gfpgan==1.3.8", "av", "safetensors==0.4.5",
]

CHECKPOINTS = {
    "checkpoints/mapping_00109-model.pth.tar":
        "https://github.com/OpenTalker/SadTalker/releases/download/v0.0.2-rc/mapping_00109-model.pth.tar",
    "checkpoints/mapping_00229-model.pth.tar":
        "https://github.com/OpenTalker/SadTalker/releases/download/v0.0.2-rc/mapping_00229-model.pth.tar",
    "checkpoints/SadTalker_V0.0.2_256.safetensors":
        "https://github.com/OpenTalker/SadTalker/releases/download/v0.0.2-rc/SadTalker_V0.0.2_256.safetensors",
    "checkpoints/SadTalker_V0.0.2_512.safetensors":
        "https://github.com/OpenTalker/SadTalker/releases/download/v0.0.2-rc/SadTalker_V0.0.2_512.safetensors",
    "gfpgan/weights/alignment_WFLW_4HG.pth":
        "https://github.com/xinntao/facexlib/releases/download/v0.1.0/alignment_WFLW_4HG.pth",
    "gfpgan/weights/detection_Resnet50_Final.pth":
        "https://github.com/xinntao/facexlib/releases/download/v0.1.0/detection_Resnet50_Final.pth",
    "gfpgan/weights/GFPGANv1.4.pth":
        "https://github.com/TencentARC/GFPGAN/releases/download/v1.3.0/GFPGANv1.4.pth",
    "gfpgan/weights/parsing_parsenet.pth":
        "https://github.com/xinntao/facexlib/releases/download/v0.2.2/parsing_parsenet.pth",
}


def _run(cmd, **kw):
    print("   $", " ".join(str(c) for c in cmd))
    return subprocess.run(cmd, check=True, **kw)


def _pip(vpy: str, args: list[str], *, tries: int = 4) -> None:
    """pip install with generous network timeouts and a few retries — the
    downloads are large and the connection here is flaky."""
    cmd = [vpy, "-m", "pip", "install", "--retries", "10",
           "--timeout", "120", *args]
    for attempt in range(1, tries + 1):
        try:
            _run(cmd)
            return
        except subprocess.CalledProcessError:
            if attempt == tries:
                raise
            print(f"   pip failed (attempt {attempt}/{tries}) — retrying, "
                  "cached downloads are kept")


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
    """Yield plausible python.exe paths, best (3.10) first."""
    local = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Python"
    for ver in WANTED_PY:
        tag = ver.replace(".", "")
        yield local / f"Python{tag}" / "python.exe"
        yield Path(f"C:/Python{tag}/python.exe")
        w = shutil.which(f"python{ver}")
        if w:
            yield Path(w)
    # the py launcher can resolve a specific minor version
    if shutil.which("py"):
        for ver in WANTED_PY:
            try:
                out = subprocess.run(["py", f"-{ver}", "-c",
                                      "import sys;print(sys.executable)"],
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


def _winget_install_python() -> str | None:
    if not shutil.which("winget"):
        return None
    pkg_id, ver = WINGET_PY
    print(f"   installing Python {ver} via winget (one time)…")
    try:
        _run(["winget", "install", "-e", "--id", pkg_id, "--silent",
              "--accept-package-agreements", "--accept-source-agreements"])
    except subprocess.CalledProcessError as exc:
        # winget returns non-zero when the package is already installed too
        print(f"   winget exit {exc.returncode} — checking anyway")
    return _find_base_python()


def _base_python() -> str:
    exe = _find_base_python() or _winget_install_python()
    if not exe:
        raise SystemExit(
            "Need Python 3.10 or 3.11 for SadTalker's pinned dependencies.\n"
            "Install one from https://www.python.org/downloads/release/python-31011/\n"
            "(or `winget install Python.Python.3.10`) and re-run "
            "`brainrotter avatar-setup`."
        )
    print(f"   base interpreter: {exe}  (Python {_py_version(exe)})")
    return exe


def _download(url: str, dst: Path) -> None:
    if dst.is_file() and dst.stat().st_size > 10_000:
        print(f"   have {dst.name}")
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    print(f"   downloading {dst.name} ...")
    tmp = dst.with_suffix(dst.suffix + ".part")
    with urllib.request.urlopen(url) as r, tmp.open("wb") as f:  # noqa: S310
        while chunk := r.read(1 << 20):
            f.write(chunk)
    tmp.rename(dst)


def run() -> int:
    if not shutil.which("git"):
        print("git is required.")
        return 1

    print("1. SadTalker source")
    if not (SADTALKER_DIR / "inference.py").is_file():
        SADTALKER_DIR.parent.mkdir(parents=True, exist_ok=True)
        _run(["git", "clone", "--depth", "1", REPO, str(SADTALKER_DIR)])
    else:
        print("   already cloned")

    print("\n2. isolated virtualenv (Python 3.10/3.11)")
    venv_dir = SADTALKER_DIR / ".venv"
    venv_ok = SADTALKER_PY.is_file() and _py_version(SADTALKER_PY) in WANTED_PY
    if not venv_ok:
        if venv_dir.exists():
            print("   existing venv is the wrong Python — rebuilding")
            shutil.rmtree(venv_dir, ignore_errors=True)
        _run([_base_python(), "-m", "venv", str(venv_dir)])
    vpy = str(SADTALKER_PY)
    _pip(vpy, ["--upgrade", "pip", "wheel", "--quiet"])

    print("\n3. PyTorch (CUDA 11.8) - large download")
    _pip(vpy, [*TORCH, "--index-url", TORCH_INDEX])

    print("\n4. SadTalker dependencies")
    _pip(vpy, REQS)

    print("\n5. checkpoints (~2 GB, resumable)")
    for rel, url in CHECKPOINTS.items():
        _download(url, SADTALKER_DIR / rel)

    print("\n6. smoke test")
    try:
        _run([vpy, "-c", "import torch; print('   cuda:', torch.cuda.is_available())"])
    except subprocess.CalledProcessError:
        print("   torch import failed - see errors above")
        return 1

    print("\nSadTalker installed. anime_figure will now generate talking heads.")
    print("Set  [avatar] enabled = false  in config.toml to turn it off.")
    return 0
