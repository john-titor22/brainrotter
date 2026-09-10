"""Stable Diffusion text->image, run inside tools/imagegen/.venv.

Not imported by the rest of Brainrotter — it only exists to be executed by the
isolated venv's python:  python _infer.py <job.json>

job.json: {prompts, out_dir, model, steps, guidance, width, height, device,
           negative_prompt, seed}
Emits on stdout:  @@IMAGES@@ ["/abs/img_00.png", ...]
"""

from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

MARKER = "@@IMAGES@@"


def main() -> int:
    job = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    out_dir = Path(job["out_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        import torch
        from diffusers import AutoPipelineForText2Image
    except Exception:
        traceback.print_exc()
        return 1

    device = job.get("device", "cuda")
    if device == "cuda" and not torch.cuda.is_available():
        print("cuda requested but not available; falling back to cpu", file=sys.stderr)
        device = "cpu"
    dtype = torch.float16 if device == "cuda" else torch.float32

    try:
        pipe = AutoPipelineForText2Image.from_pretrained(
            job["model"], torch_dtype=dtype, variant="fp16" if dtype == torch.float16 else None,
        )
    except Exception:
        # some checkpoints have no fp16 variant
        pipe = AutoPipelineForText2Image.from_pretrained(job["model"], torch_dtype=dtype)

    offloaded = False
    resident = bool(job.get("gpu_resident"))
    if device == "cuda":
        # Default: cpu-offload — SDXL-Turbo fp16 does NOT reliably fit an 8 GB
        # card that's also driving a desktop / another render, and spilling to
        # shared memory is ~10x slower than offload. `visuals.gpu_resident=true`
        # opts into resident mode on a bigger card.
        if resident:
            try:
                pipe = pipe.to("cuda")
            except torch.cuda.OutOfMemoryError:
                torch.cuda.empty_cache()
                pipe.enable_model_cpu_offload()
                offloaded = True
        else:
            pipe.enable_model_cpu_offload()
            offloaded = True
        for fn in ("enable_vae_slicing", "enable_attention_slicing"):
            try:
                getattr(pipe, fn)()
            except Exception:
                pass
    else:
        pipe = pipe.to("cpu")

    try:
        pipe.set_progress_bar_config(disable=True)
    except Exception:
        pass

    seed = int(job.get("seed", 0))
    steps = int(job.get("steps", 4))
    guidance = float(job.get("guidance", 0.0))
    w, h = int(job.get("width", 832)), int(job.get("height", 1216))
    neg = job.get("negative_prompt") or None

    import gc

    written: list[str] = []
    n = len(job["prompts"])
    for i, prompt in enumerate(job["prompts"]):
        try:
            gen = torch.Generator(device="cpu").manual_seed(seed + i)
            image = pipe(
                prompt=prompt, negative_prompt=neg, num_inference_steps=steps,
                guidance_scale=guidance, width=w, height=h, generator=gen,
            ).images[0]
            dst = out_dir / f"img_{i:02d}.png"
            image.save(dst)
            written.append(str(dst))
            print(f"  image {i + 1}/{n} ok", file=sys.stderr, flush=True)
            del image
        except torch.cuda.OutOfMemoryError:
            traceback.print_exc()
            gc.collect()
            torch.cuda.empty_cache()
            if not offloaded:
                # ran out mid-batch — switch to offload and retry this image
                try:
                    pipe.enable_model_cpu_offload()
                    offloaded = True
                except Exception:
                    pass
        except Exception:
            traceback.print_exc()
        finally:
            gc.collect()
            # only churn the cache when we're memory-constrained
            if device == "cuda" and offloaded:
                try:
                    torch.cuda.empty_cache()
                except Exception:
                    pass

    print(MARKER + " " + json.dumps(written))
    return 0 if written else 1


if __name__ == "__main__":
    sys.exit(main())
