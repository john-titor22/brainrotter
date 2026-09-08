# Vendored dependencies

## MoneyPrinterTurbo

The rendering engine (TTS + caption alignment + ffmpeg compositing). MIT licensed
— see `MoneyPrinterTurbo/LICENSE`. Brainrotter drives it in-process through
`brainrotter/engine/`; we never use its LLM, stock-footage, or upload paths.

### How it got here

```bash
git clone --depth 1 https://github.com/harry0703/MoneyPrinterTurbo.git vendor/MoneyPrinterTurbo
rm -rf vendor/MoneyPrinterTurbo/.git
pip install -r vendor/MoneyPrinterTurbo/requirements.txt
```

### What our repo does and doesn't track

`.gitignore` excludes `resource/` (≈198 MB of CJK fonts + bundled BGM), `test/`,
`docs/`, `config.toml` (generated per-run by `brainrotter/engine/mpt_config.py`),
`storage/` (per-task scratch), and `models/` (downloaded Whisper weights).

**`resource/` is required at runtime** — the engine loads caption fonts from
`resource/fonts/` and background music from `resource/songs/`. If you clone
Brainrotter fresh, re-run the `git clone` above to restore it (or copy
`resource/` from any MoneyPrinterTurbo checkout).

### Updating

Re-clone into a temp dir, diff, and copy over — then re-run the smoke test:
`python -c "from brainrotter.engine import render_plan; ..."` (see
`ARCHITECTURE.md`). Pin the upstream commit here when you do:

- Vendored from `harry0703/MoneyPrinterTurbo` — v1.3.6, cloned 2026-09-08.
