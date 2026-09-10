"""LLM access for the Writer and Director.

Providers (config: ``writer.provider``):
  - "ollama"    — a local model, no key, no egress. The default.
                  `ollama pull llama3.1:8b`
  - "groq"      — Groq's free API tier: Qwen3 27B, very fast, generous
                  limits, doesn't train on your data. Much stronger than a
                  local 8B and frees the GPU. Needs GROQ_API_KEY
                  (https://console.groq.com/keys).
  - "anthropic" — Claude API, if ANTHROPIC_API_KEY is set.

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
    if p == "groq":
        return bool(get_settings().groq_api_key)
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
    if p == "groq":
        s = get_settings().writer
        return (f"groq ready ({s.groq_model})" if get_settings().groq_api_key
                else "GROQ_API_KEY not set — https://console.groq.com/keys")
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
    r = httpx.get(f"{url}/api/tags", timeout=6)
    r.raise_for_status()
    return [m["name"] for m in r.json().get("models", [])]


_models_cache: tuple[float, list[str]] = (0.0, [])


def _ollama_models_safe() -> list[str]:
    """Cached ~60s — a slow /api/tags (ollama busy loading a model) must not make
    us silently skip the Darija model and fall back to MSA."""
    import time as _t

    global _models_cache
    if _t.time() - _models_cache[0] < 60 and _models_cache[1]:
        return _models_cache[1]
    try:
        models = _ollama_models()
        if models:
            _models_cache = (_t.time(), models)
        return models
    except Exception:
        return _models_cache[1]


def multilingual_status() -> str:
    """For `doctor` — is the Darija writer model available?"""
    s = get_settings().writer
    dm = s.darija_model
    if dm:
        if dm in _ollama_models_safe():
            return f"Darija → {dm.split('/')[-1]} (Atlas-Chat, local) ✓"
        short = dm.split("/")[-1]
        return (f"Darija model not pulled — `ollama pull {dm}` "
                f"(~6 GB; until then Darija uses the main writer and drifts to MSA)")
    if s.provider.lower() in ("groq", "anthropic"):
        return f"Darija uses {s.provider} (drifts to MSA — set writer.darija_model)"
    return f"Darija uses {s.ollama_model} (weak — set writer.darija_model)"


# --- completion ------------------------------------------------------------

def complete_text(system: str, user: str, *, fast: bool = False,
                  max_tokens: int = 3000, json_mode: bool = False,
                  language: str = "en", temperature: float | None = None) -> str:
    s = get_settings().writer
    p = provider()
    # Darija always routes to the dedicated local model (Atlas-Chat) when it's
    # pulled — general/cloud models drift to MSA no matter the provider.
    if language in ("ar", "ary") and s.darija_model:
        if s.darija_model in _ollama_models_safe():
            return _ollama_chat(system, user, fast=fast, max_tokens=max_tokens,
                                json_mode=json_mode, language="en",  # model is Darija-native
                                temperature=temperature, model_override=s.darija_model)
    if p == "ollama":
        return _ollama_chat(system, user, fast=fast, max_tokens=max_tokens,
                            json_mode=json_mode, language=language,
                            temperature=temperature)
    if p == "groq":
        return _groq_chat(system, user, fast=fast, max_tokens=max_tokens,
                          json_mode=json_mode, temperature=temperature)
    if p == "anthropic":
        return _anthropic_chat(system, user, fast=fast, max_tokens=max_tokens)
    raise LLMError(f"unknown writer.provider '{s.provider}' (use 'ollama', 'groq' or 'anthropic')")


def complete_json(system: str, user: str, *, fast: bool = False,
                  max_tokens: int = 3000, language: str = "en",
                  temperature: float | None = None) -> dict:
    raw = complete_text(
        system + "\n\nReturn a single json object and nothing else.",
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
                 temperature: float | None = None,
                 model_override: str | None = None) -> str:
    s = get_settings().writer
    model = model_override or (s.ollama_fast_model if fast else s.ollama_model)
    if not model_override and language != "en" and s.multilingual_model:
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
    url = f"{s.ollama_url.rstrip('/')}/api/chat"
    timeout = getattr(s, "request_timeout", 300)
    transient = (httpx.TimeoutException, httpx.ConnectError, httpx.RemoteProtocolError)
    last_exc: Exception | None = None
    for attempt in range(2):
        try:
            r = httpx.post(url, json=body, timeout=timeout)
            r.raise_for_status()
            return (r.json().get("message", {}) or {}).get("content", "").strip()
        except httpx.HTTPError as exc:
            last_exc = exc
            if attempt == 0 and isinstance(exc, transient):
                continue        # one retry on a timeout / dropped connection
            raise LLMError(f"ollama request failed: {exc}") from exc
    raise LLMError(f"ollama request failed: {last_exc}")


def _groq_chat(system: str, user: str, *, fast: bool, max_tokens: int,
               json_mode: bool, temperature: float | None = None) -> str:
    s = get_settings()
    key = s.groq_api_key
    if not key:
        raise LLMError("writer.provider='groq' but GROQ_API_KEY is not set "
                       "(https://console.groq.com/keys)")
    model = s.writer.groq_fast_model if fast else s.writer.groq_model
    body: dict = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": s.writer.temperature if temperature is None else temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    url = "https://api.groq.com/openai/v1/chat/completions"
    transient = (httpx.TimeoutException, httpx.ConnectError, httpx.RemoteProtocolError)
    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            r = httpx.post(url, json=body, headers=headers, timeout=90)
            if r.status_code == 429:            # rate limited — brief backoff
                last_exc = LLMError("groq rate limit")
                _sleep(2.0 * (attempt + 1))
                continue
            r.raise_for_status()
            data = r.json()
            return (data["choices"][0]["message"]["content"] or "").strip()
        except httpx.HTTPError as exc:
            last_exc = exc
            if attempt < 2 and isinstance(exc, transient):
                continue
            raise LLMError(f"groq request failed: {exc}") from exc
    raise LLMError(f"groq request failed: {last_exc}")


def _sleep(secs: float) -> None:
    import time

    time.sleep(secs)


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
