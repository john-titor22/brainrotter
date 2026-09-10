"""Runs INSIDE tools/darijatts/.venv — do not import from the main package.

stdin: JSON {"text": str, "out_wav": str, "model_dir": str, "temperature": float}
stdout: "@@OK@@ <seconds>" on success.

XTTS-v2 fine-tuned for Darija (medmac01/darija_xtt_2.0). Long text is split on
sentence punctuation and concatenated — XTTS degrades past ~40 words per call.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

OK = "@@OK@@"


def _chunks(text: str, max_chars: int = 200):
    parts = re.split(r"(?<=[.!?،؛\n])\s+", text.strip())
    buf = ""
    for p in parts:
        if not p:
            continue
        if len(buf) + len(p) + 1 <= max_chars:
            buf = (buf + " " + p).strip()
        else:
            if buf:
                yield buf
            buf = p
            while len(buf) > max_chars:          # a single very long sentence
                cut = buf.rfind(" ", 0, max_chars) or max_chars
                yield buf[:cut]
                buf = buf[cut:].strip()
    if buf:
        yield buf


def main() -> int:
    job = json.loads(sys.stdin.read())
    text = job["text"].strip()
    out_wav = Path(job["out_wav"])
    model_dir = Path(job["model_dir"])
    temperature = float(job.get("temperature", 0.72))

    import numpy as np
    import soundfile as sf
    import torch
    from TTS.tts.configs.xtts_config import XttsConfig
    from TTS.tts.models.xtts import Xtts

    device = "cuda" if torch.cuda.is_available() else "cpu"
    config = XttsConfig()
    config.load_json(str(model_dir / "config.json"))
    model = Xtts.init_from_config(config)
    model.load_checkpoint(
        config,
        checkpoint_path=str(model_dir / "model.pth"),
        vocab_path=str(model_dir / "vocab.json"),
        use_deepspeed=False, eval=True,
    )
    model.to(device)

    ref = str(model_dir / "speaker_reference.wav")
    gpt_cond_latent, speaker_embedding = model.get_conditioning_latents(audio_path=[ref])

    sr = 24000
    pieces = []
    for chunk in _chunks(text):
        out = model.inference(
            chunk, "ar", gpt_cond_latent, speaker_embedding,
            temperature=temperature, enable_text_splitting=False,
        )
        wav = np.asarray(out["wav"], dtype=np.float32)
        pieces.append(wav)
        pieces.append(np.zeros(int(sr * 0.18), dtype=np.float32))   # small gap

    if not pieces:
        print("no audio", file=sys.stderr)
        return 2
    full = np.concatenate(pieces)
    peak = float(np.max(np.abs(full))) or 1.0
    full = (full / peak) * 0.97
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(out_wav), full, sr)
    print(f"{OK} {len(full) / sr:.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
