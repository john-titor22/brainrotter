"""Drive the isolated Darija-XTTS venv to synthesize speech from Darija text."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import time
from pathlib import Path

from ..config import PROJECT_ROOT, get_settings

log = logging.getLogger("brainrotter.darija_tts")

TTS_DIR = PROJECT_ROOT / "tools" / "darijatts"
TTS_PY = TTS_DIR / ".venv" / "Scripts" / "python.exe"
MODEL_DIR = TTS_DIR / "model"
_INFER = Path(__file__).parent / "_infer.py"
_READY = TTS_DIR / ".ready"
_OK = "@@OK@@"

_MODEL_FILES = {
    "config.json": "https://huggingface.co/medmac01/darija_xtt_2.0/resolve/main/config.json",
    "vocab.json": "https://huggingface.co/medmac01/darija_xtt_2.0/resolve/main/vocab.json",
    "model.pth": "https://huggingface.co/medmac01/darija_xtt_2.0/resolve/main/model_2.1.pth",
    "speaker_reference.wav": "https://huggingface.co/medmac01/darija_xtt_2.0/resolve/main/speaker_ref.wav",
}


def is_installed() -> bool:
    return (TTS_PY.is_file() and _READY.is_file()
            and all((MODEL_DIR / f).is_file() for f in _MODEL_FILES))


def available() -> bool:
    return get_settings().language.darija_tts and is_installed()


def status() -> str:
    if not TTS_PY.is_file():
        return "not installed — `brainrotter darija-tts-setup` (~2 GB; else edge-tts ar-MA)"
    missing = [f for f in _MODEL_FILES if not (MODEL_DIR / f).is_file()]
    if missing or not _READY.is_file():
        return "venv present, model incomplete — re-run `brainrotter darija-tts-setup`"
    return "ready (XTTS-v2 Darija, local)"


def synthesize(text: str, out_wav: Path, *, temperature: float = 0.72) -> Path | None:
    """Darija speech → wav (24 kHz). None on any failure."""
    if not (text and text.strip()) or not is_installed():
        return None
    out_wav = Path(out_wav).resolve()
    payload = json.dumps({
        "text": text, "out_wav": str(out_wav),
        "model_dir": str(MODEL_DIR.resolve()), "temperature": temperature,
    })
    try:
        proc = subprocess.run(
            [str(TTS_PY), str(_INFER.resolve())],
            input=payload, capture_output=True, text=True,
            timeout=get_settings().language.darija_tts_timeout,
        )
    except subprocess.TimeoutExpired:
        log.warning("darija tts timed out")
        return None
    for line in proc.stdout.splitlines():
        if line.startswith(_OK):
            if out_wav.is_file() and out_wav.stat().st_size > 4000:
                return out_wav
    log.warning("darija tts failed: %s", (proc.stderr or "")[-300:])
    return None


# --- setup ---------------------------------------------------------------

def _download(url: str, dst: Path) -> bool:
    import urllib.request

    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(dst.suffix + ".part")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "brainrotter/0.1"})
        with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
            shutil.copyfileobj(r, f, length=1 << 20)
        tmp.replace(dst)
        return dst.stat().st_size > 1000
    except Exception as exc:  # noqa: BLE001
        log.warning("download failed %s: %s", url, exc)
        tmp.unlink(missing_ok=True)
        return False


def run_setup(*, force: bool = False) -> str:
    if is_installed() and not force:
        return "already installed"
    if not TTS_PY.is_file():
        return ("venv missing — create it first:\n"
                f"  py -3.10 -m venv {TTS_DIR / '.venv'}\n"
                f"  {TTS_PY} -m pip install coqui-tts soundfile")
    for name, url in _MODEL_FILES.items():
        dst = MODEL_DIR / name
        if dst.is_file() and dst.stat().st_size > 1000 and not force:
            continue
        log.info("downloading %s …", name)
        if not _download(url, dst):
            return f"failed to download {name}"
    # smoke test
    probe = MODEL_DIR / "_probe.wav"
    got = synthesize("سلام، هادشي كيخدم مزيان.", probe, temperature=0.7)
    probe.unlink(missing_ok=True)
    if not got:
        return "model downloaded but the smoke test failed — check the venv deps"
    _READY.write_text("ok", encoding="utf-8")
    return "ready"
