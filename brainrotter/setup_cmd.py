"""First-run setup: vendor the engine, make config, create the app shortcut."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from .config import PROJECT_ROOT

MPT_REPO = "https://github.com/harry0703/MoneyPrinterTurbo.git"
ENGINE_DIR = PROJECT_ROOT / "vendor" / "MoneyPrinterTurbo"


def _step(msg: str) -> None:
    print(f"  {msg}")


CAPTION_FONT = ENGINE_DIR / "resource" / "fonts" / "BeVietnamPro-Bold.ttf"


def vendor_engine() -> bool:
    have_code = (ENGINE_DIR / "app" / "services" / "task.py").is_file()
    # the caption font is the only runtime resource we can't render without;
    # it's ~139 KB and bundled in the repo. Background music is optional.
    have_font = CAPTION_FONT.is_file()

    if have_code and have_font:
        _step("engine already vendored.")
        _maybe_fetch_music()
        return True

    if not shutil.which("git"):
        _step("git not found - can't fetch the engine. Install git and rerun.")
        return False

    if have_code and not have_font:
        _step("fetching the caption font ...")
        _sparse_fetch(["resource/fonts"])
        return CAPTION_FONT.is_file()

    ENGINE_DIR.parent.mkdir(parents=True, exist_ok=True)
    _step(f"cloning MoneyPrinterTurbo into {ENGINE_DIR.relative_to(PROJECT_ROOT)} ...")
    r = subprocess.run(["git", "clone", "--depth", "1", MPT_REPO, str(ENGINE_DIR)])
    if r.returncode != 0:
        return False
    shutil.rmtree(ENGINE_DIR / ".git", ignore_errors=True)
    return True


def _maybe_fetch_music() -> None:
    """Background music is optional. Grab it once if the user has nothing else."""
    songs = ENGINE_DIR / "resource" / "songs"
    have_music = (songs.is_dir() and any(songs.glob("*.mp3"))) or any(
        (PROJECT_ROOT / "assets" / "music").glob("*")
    )
    if have_music or not shutil.which("git"):
        return
    _step("fetching background music (~56 MB, optional - Ctrl+C to skip) ...")
    try:
        _sparse_fetch(["resource/songs"])
    except KeyboardInterrupt:
        _step("skipped music. Drop your own MP3s into assets/music/ any time.")


def _sparse_fetch(paths: list[str]) -> None:
    with tempfile.TemporaryDirectory() as td:
        clone = ["git", "clone", "--depth", "1", "--filter=blob:none", "--sparse",
                 MPT_REPO, td]
        if subprocess.run(clone).returncode != 0:
            if subprocess.run(["git", "clone", "--depth", "1", MPT_REPO, td]).returncode != 0:
                return
        subprocess.run(["git", "-C", td, "sparse-checkout", "set", *paths],
                       capture_output=True)
        for p in paths:
            src = Path(td) / p
            if src.is_dir():
                shutil.copytree(src, ENGINE_DIR / p, dirs_exist_ok=True)


def make_configs() -> None:
    for name, example in ((".env", ".env.example"),
                          ("config.toml", "config.example.toml")):
        dst, src = PROJECT_ROOT / name, PROJECT_ROOT / example
        if dst.exists():
            _step(f"{name} already exists.")
        elif src.exists():
            shutil.copyfile(src, dst)
            _step(f"created {name}")


def install_shortcut() -> None:
    if sys.platform != "win32":
        _step("shortcuts are Windows-only - run `brainrotter app` to open it.")
        return
    bat = PROJECT_ROOT / "Brainrotter.bat"
    icon = PROJECT_ROOT / "extension" / "brainrotter.ico"
    ps = f"""
$W = New-Object -ComObject WScript.Shell
foreach ($d in @("$env:USERPROFILE\\Desktop\\Brainrotter.lnk",
                 "$env:APPDATA\\Microsoft\\Windows\\Start Menu\\Programs\\Brainrotter.lnk")) {{
  $s = $W.CreateShortcut($d)
  $s.TargetPath = "{bat}"
  $s.WorkingDirectory = "{PROJECT_ROOT}"
  $s.IconLocation = "{icon}"
  $s.Description = "Brainrotter - the brainrot video factory"
  $s.WindowStyle = 7
  $s.Save()
  Write-Output "  shortcut: $d"
}}
"""
    subprocess.run(["powershell", "-NoProfile", "-Command", ps])


def run() -> int:
    print("Brainrotter setup\n")

    print("1. render engine")
    if not vendor_engine():
        print("\n  setup stopped - fix the error above and rerun `brainrotter setup`.")
        return 1

    print("\n2. dependencies")
    _step("run:  pip install -r requirements.txt   (in your venv)")

    print("\n3. config files")
    make_configs()

    print("\n4. app shortcut")
    install_shortcut()

    print(
        "\nAlmost there. To finish:\n"
        "  - install Ollama (https://ollama.com) and:  ollama pull llama3.1:8b\n"
        "  - grab background footage:  brainrotter footage sync\n"
        "     (needs assets/cookies.txt - see README)\n"
        "  - check everything:  brainrotter doctor\n"
        "  - then open the Brainrotter shortcut, or:  brainrotter app\n"
    )
    return 0
