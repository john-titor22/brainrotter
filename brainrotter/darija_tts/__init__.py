"""Real Moroccan Darija text-to-speech.

edge-tts's ``ar-MA`` voices are Modern-Standard-Arabic models with a Moroccan
accent — they read Darija text but sound stiff and "Arabic", not Darija. This
module runs a Darija-fine-tuned XTTS-v2 (``medmac01/darija_xtt_2.0``, voice-
cloned from a real Darija speaker) in an isolated venv, the same pattern as the
SadTalker / SDXL setups.

``synthesize(text, out_wav)`` → the wav path, or None (caller falls back to
edge-tts). ``available()`` is False until ``brainrotter darija-tts-setup`` has
run.
"""

from __future__ import annotations

from .generator import available, is_installed, status, synthesize

__all__ = ["synthesize", "available", "is_installed", "status"]
