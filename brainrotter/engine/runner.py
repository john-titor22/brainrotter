"""Run a render in a child process, with one retry on a hard crash."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from ..models import RenderPlan
from ._subprocess import RESULT_MARKER
from .mpt_engine import EngineError


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

        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-6:]
        last = " | ".join(tail)
        # retry on a hard crash (segfault) OR a flaky MoviePy / ffmpeg / OS error
        flaky = any(s in last for s in (
            "Errno 22", "MoviePy error", "FFMPEG", "ffmpeg", "Broken pipe",
            "Invalid argument", "Auto-inserting", "bitstream filter",
            "moov atom not found", "Invalid data found", "Conversion failed",
            "h264_mp4toannexb",
        ))
        crashed = proc.returncode not in (0, 2)
        if not crashed and not flaky:
            raise EngineError(last or "engine failed")
        # else: fall through and try again
    raise EngineError(f"engine failed after {attempts} attempts: {last}")
