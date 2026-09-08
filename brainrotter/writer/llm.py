"""LLM access for the Writer and Director.

Two providers:
  - "ollama"  — a local model, no API key, no network egress. The default.
                Needs Ollama running (https://ollama.com) with a model pulled:
                  ollama pull llama3.1:8b
  - "anthropic" — Claude API, if ANTHROPIC_API_KEY is set and you opt in.

`available()` tells callers whether the configured provider is usable right now
(so `scriptwriter` / `director` can fall back to their offline heuristics).
"""

from __future__ import annotations

import json
import re

import httpx

from ..config import get_settings

_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)


class LLMError(RuntimeError):
    pass


# --- capability check -------------------------------------------------------

def provider() -> str:
    return get_settings().writer.provider.lower()


def available() -> bool:
    p = provider()
    if p == "anthropic":
        return bool(get_settings().anthropic_api_key)
    if p == "ollama":
        return _ollama_up()
    return False


def status() -> str:
    p = provider()
    if p == "ollama":
        s = get_settings().writer
        if not _ollama_up():
            return f"ollama not reachable at {s.ollama_url} — start Ollama"
        if s.ollama_model not in _ollama_models():
            return f"ollama up, but model '{s.ollama_model}' not pulled (ollama pull {s.ollama_model})"
        return f"ollama ready ({s.ollama_model})"
    if p == "anthropic":
        return "anthropic key set" if get_settings().anthropic_api_key else "ANTHROPIC_API_KEY not set"
    return f"unknown provider '{p}'"


def _ollama_up() -> bool:
    try:
        return bool(_ollama_models())
    except Exception:
        return False


def _ollama_models() -> list[str]:
    url = get_settings().writer.ollama_url.rstrip("/")
    r = httpx.get(f"{url}/api/tags", timeout=3)
    r.raise_for_status()
    return [m["name"] for m in r.json().get("models", [])]


def _ollama_models_safe() -> list[str]:
    try:
        return _ollama_models()
    except Exception:
        return []


def multilingual_status() -> str:
    """For `doctor` — is the non-English writer model available?"""
    s = get_settings().writer
    if s.provider.lower() == "anthropic":
        return "Claude handles all languages"
    ml = s.multilingual_model
    if not ml:
        return f"none configured — non-English uses {s.ollama_model} (weak outside English)"
    if ml in _ollama_models_safe():
        return f"{ml} ready"
    return f"'{ml}' not pulled — `ollama pull {ml}` (falls back to {s.ollama_model})"


# --- completion ------------------------------------------------------------

def complete_text(system: str, user: str, *, fast: bool = False,
                  max_tokens: int = 3000, json_mode: bool = False,
                  language: str = "en", temperature: float | None = None) -> str:
    s = get_settings().writer
    p = provider()
    if p == "ollama":
        return _ollama_chat(system, user, fast=fast, max_tokens=max_tokens,
                            json_mode=json_mode, language=language,
                            temperature=temperature)
    if p == "anthropic":
        return _anthropic_chat(system, user, fast=fast, max_tokens=max_tokens)
    raise LLMError(f"unknown writer.provider '{s.provider}' (use 'ollama' or 'anthropic')")


def complete_json(system: str, user: str, *, fast: bool = False,
                  max_tokens: int = 3000, language: str = "en",
                  temperature: float | None = None) -> dict:
    raw = complete_text(
        system + "\n\nReturn a single JSON object and nothing else.",
        user, fast=fast, max_tokens=max_tokens, json_mode=True, language=language,
        temperature=temperature,
    )
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        m = _JSON_BLOCK.search(raw)
        if not m:
            raise LLMError(f"model did not return JSON:\n{raw[:600]}")
        return json.loads(m.group(0))


def _ollama_chat(system: str, user: str, *, fast: bool, max_tokens: int,
                 json_mode: bool, language: str = "en",
                 temperature: float | None = None) -> str:
    s = get_settings().writer
    model = s.ollama_fast_model if fast else s.ollama_model
    if language != "en" and s.multilingual_model:
        # Aya (or whatever's configured) writes French/Arabic/Darija far better
        # than llama3.1. Use it when it's actually pulled, else stick with the
        # default and let the prompt do its best.
        if s.multilingual_model in _ollama_models_safe():
            model = s.multilingual_model
    body = {
        "model": model,
        "stream": False,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "options": {
            "temperature": s.temperature if temperature is None else temperature,
            "num_predict": max_tokens,
        },
    }
    if json_mode:
        body["format"] = "json"
    try:
        r = httpx.post(f"{s.ollama_url.rstrip('/')}/api/chat", json=body, timeout=180)
        r.raise_for_status()
    except httpx.HTTPError as exc:
        raise LLMError(f"ollama request failed: {exc}") from exc
    return (r.json().get("message", {}) or {}).get("content", "").strip()


def _anthropic_chat(system: str, user: str, *, fast: bool, max_tokens: int) -> str:
    import anthropic

    s = get_settings()
    key = s.anthropic_api_key
    if not key:
        raise LLMError("writer.provider='anthropic' but ANTHROPIC_API_KEY is not set")
    model = s.writer.anthropic_fast_model if fast else s.writer.anthropic_model
    client = anthropic.Anthropic(api_key=key)
    resp = client.messages.create(
        model=model, max_tokens=max_tokens, system=system,
        messages=[{"role": "user", "content": user}],
    )
    return "".join(b.text for b in resp.content if b.type == "text").strip()
