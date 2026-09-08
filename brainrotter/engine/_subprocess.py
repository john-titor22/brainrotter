"""Run one render in a child process.

The vendored engine leans on MoviePy / native codecs that can hard-crash
(segfault, `[Errno 22]`) on a bad frame. Running the render here means a crash
fails *the job*, not the dashboard server.

Usage (internal): ``python -m brainrotter.engine._subprocess`` with a JSON
payload {"plan": <RenderPlan>, "job_id": str, "out_dir": str|null} on stdin.
Emits ``@@RESULT@@ <json>`` on stdout on success.
"""

from __future__ import annotations

import json
import sys

RESULT_MARKER = "@@RESULT@@"


def main() -> int:
    payload = json.loads(sys.stdin.read())
    from ..models import RenderPlan
    from .mpt_engine import EngineError, render_plan

    plan = RenderPlan.model_validate(payload["plan"])
    out_dir = payload.get("out_dir")
    try:
        from pathlib import Path

        res = render_plan(
            plan,
            job_id=payload["job_id"],
            out_dir=Path(out_dir) if out_dir else None,
        )
    except EngineError as exc:
        print(f"ENGINE_ERROR: {exc}", file=sys.stderr)
        return 2
    print(RESULT_MARKER + " " + json.dumps(res))
    return 0


if __name__ == "__main__":
    sys.exit(main())
