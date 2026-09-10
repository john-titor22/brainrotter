"""Run a render in a child process, retrying transient MoviePy / ffmpeg deaths."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

from ..models import RenderPlan
from .mpt_engine import EngineError

# The stdout contract with brainrotter.engine._subprocess. Defined here (not
# imported from _subprocess) so that running `python -m brainrotter.engine.
# _subprocess` doesn't pull _subprocess into sys.modules during package import
# — that triggers a runpy RuntimeWarning and can double-run the module body.
RESULT_MARKER = "@@RESULT@@"

# ffmpeg / MoviePy progress spam — useless in an error tail and it hides the
# real exception line.
_PROGRESS = re.compile(
    r"^\s*(frame=|size=|\[.*\]\s|video:|audio:|Lsize|Output #|Input #|Stream #|"
    r"Press \[q\]|Side data:|CPB properties:|encoder\s|Metadata:|\s+major_brand|"
    r"\s+minor_version|\s+compatible_brands|\s+encoder|\s+handler_name|"
    r"\s+vendor_id|configuration:|libav|built with|ffmpeg version)"
    r"|bitrate=\s*\S+kbits/s|speed=\s*\S+x\b"
)

# Errors that will fail again no matter how many times we retry.
_PERMANENT = (
    "no background clips found",
    "failed to import vendored engine",
    "output is missing",
    "set background_source",
)


def _error_tail(proc: subprocess.CompletedProcess, keep: int = 8) -> str:
    raw = (proc.stderr or proc.stdout or "").strip().splitlines()
    signal = [ln for ln in raw if ln.strip() and not _PROGRESS.search(ln)]
    lines = (signal or raw)[-keep:]
    return " | ".join(l.strip() for l in lines)


def render_isolated(plan: RenderPlan, *, job_id: str, out_dir: Path | None = None,
                    attempts: int = 2, timeout: int = 1800) -> dict:
    payload = json.dumps({
        "plan": plan.model_dump(),
        "job_id": job_id,
        "out_dir": str(out_dir) if out_dir else None,
    })
    last = ""
    for attempt in range(1, attempts + 1):
        proc = subprocess.run(
            [sys.executable, "-m", "brainrotter.engine._subprocess"],
            input=payload, capture_output=True, text=True, timeout=timeout,
        )
        for line in proc.stdout.splitlines():
            if line.startswith(RESULT_MARKER):
                return json.loads(line[len(RESULT_MARKER):].strip())

        last = _error_tail(proc)
        if any(s in last for s in _PERMANENT):
            raise EngineError(last or "engine failed")
        # Everything else — a clean EngineError (exit 2), a segfault, a MoviePy /
        # ffmpeg pipe break — is usually transient on Windows. Try again.
    raise EngineError(f"engine failed after {attempts} attempt(s): {last}")
