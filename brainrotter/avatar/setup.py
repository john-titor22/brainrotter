"""Install SadTalker into its own isolated virtualenv + fetch its checkpoints.

Run via `brainrotter avatar-setup`. Big one-time download (~2 GB checkpoints +
a CUDA PyTorch, so 4-5 GB total). Resumable — re-run to pick up where it left
off. SadTalker's dependency set is pinned to its last known-good combo.
"""

from __future__ import annotations

import subprocess
import sys
import urllib.request
from pathlib import Path

from .sadtalker import SADTALKER_DIR, SADTALKER_PY

REPO = "https://github.com/OpenTalker/SadTalker.git"

# SadTalker's tested torch (CUDA 11.8) — matches its own requirements notes.
TORCH = [
    "torch==2.0.1+cu118", "torchvision==0.15.2+cu118", "torchaudio==2.0.2+cu118",
]
TORCH_INDEX = "https://download.pytorch.org/whl/cu118"

# a slimmer, de-conflicted version of SadTalker/requirements.txt
REQS = [
    "numpy==1.23.4", "face_alignment==1.3.5", "imageio==2.19.3",
    "imageio-ffmpeg==0.4.7", "librosa==0.9.2", "numba==0.56.4", "resampy==0.3.1",
    "pydub==0.25.1", "scipy==1.10.1", "kornia==0.6.8", "tqdm", "yacs==0.1.8",
    "pyyaml", "joblib==1.1.0", "scikit-image==0.19.3", "basicsr==1.4.2",
    "facexlib==0.3.0", "gfpgan==1.3.8", "av", "safetensors", "gradio",
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
    if not __import__("shutil").which("git"):
        print("git is required.")
        return 1

    print("1. SadTalker source")
    if not (SADTALKER_DIR / "inference.py").is_file():
        SADTALKER_DIR.parent.mkdir(parents=True, exist_ok=True)
        _run(["git", "clone", "--depth", "1", REPO, str(SADTALKER_DIR)])
    else:
        print("   already cloned")

    print("\n2. isolated virtualenv")
    if not SADTALKER_PY.is_file():
        _run([sys.executable, "-m", "venv", str(SADTALKER_DIR / ".venv")])
    vpy = str(SADTALKER_PY)
    _run([vpy, "-m", "pip", "install", "--upgrade", "pip", "wheel", "--quiet"])

    print("\n3. PyTorch (CUDA 11.8) - large download")
    _run([vpy, "-m", "pip", "install", *TORCH, "--index-url", TORCH_INDEX])

    print("\n4. SadTalker dependencies")
    _run([vpy, "-m", "pip", "install", *REQS])

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
