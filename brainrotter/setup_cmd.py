"""First-run setup: vendor the engine, make config, create the app shortcut."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from .config import PROJECT_ROOT

MPT_REPO = "https://github.com/harry0703/MoneyPrinterTurbo.git"
ENGINE_DIR = PROJECT_ROOT / "vendor" / "MoneyPrinterTurbo"


def _step(msg: str) -> None:
    print(f"  {msg}")


def vendor_engine() -> bool:
    if (ENGINE_DIR / "app" / "services" / "task.py").is_file():
        _step("engine already vendored.")
        return True
    if not shutil.which("git"):
        _step("git not found - can't fetch the engine. Install git and rerun.")
        return False
    ENGINE_DIR.parent.mkdir(parents=True, exist_ok=True)
    _step(f"cloning MoneyPrinterTurbo into {ENGINE_DIR.relative_to(PROJECT_ROOT)} ...")
    r = subprocess.run(["git", "clone", "--depth", "1", MPT_REPO, str(ENGINE_DIR)])
    if r.returncode != 0:
        return False
    shutil.rmtree(ENGINE_DIR / ".git", ignore_errors=True)
    return True


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
