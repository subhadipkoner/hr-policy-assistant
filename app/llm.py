"""Language model access, with the provider chosen at runtime.

Kept deliberately small: one blocking request, no streaming and no tools, so
the Gemini call is a plain HTTPS POST from the standard library rather than
another dependency to install and keep current.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

GEMINI_ENDPOINT = ("https://generativelanguage.googleapis.com/v1beta/"
                   "models/{model}:generateContent")

# "gemini-flash-latest" is an alias that tracks the current Flash model.
# Pinning a version is what breaks: gemini-2.5-flash already returns 404
# "no longer available to new users" on a freshly issued key.
DEFAULT_GEMINI_MODEL = "gemini-flash-latest"
DEFAULT_ANTHROPIC_MODEL = "claude-haiku-5-5"

# Measured: a 4KB grounded prompt takes ~38s with reasoning off and ~105s with
# it on, so the old 60s ceiling silently dropped every answer into the
# extractive fallback.
TIMEOUT_SECONDS = int(os.getenv("LLM_TIMEOUT_SECONDS", "90"))

# This task is extraction from five short passages that are already in the
# prompt. Reasoning tokens nearly tripled latency without improving the answer,
# so they are off unless a budget is set explicitly.
GEMINI_THINKING_BUDGET = int(os.getenv("GEMINI_THINKING_BUDGET", "0"))

# The API returns 503 "high demand" intermittently; one quick retry clears it.
RETRY_STATUSES = {429, 500, 503}


class LLMUnavailable(RuntimeError):
    """No provider is configured, or the call failed. Caller falls back."""


def active_provider() -> str:
    """gemini | anthropic | none, honouring an explicit LLM_PROVIDER."""
    choice = os.getenv("LLM_PROVIDER", "").strip().lower()
    if choice in ("gemini", "anthropic", "none"):
        return choice
    if os.getenv("GEMINI_API_KEY", "").strip():
        return "gemini"
    if os.getenv("ANTHROPIC_API_KEY", "").strip():
        return "anthropic"
    return "none"


def model_name(provider: str | None = None) -> str:
    provider = provider or active_provider()
    if provider == "gemini":
        return os.getenv("GEMINI_MODEL", DEFAULT_GEMINI_MODEL)
    if provider == "anthropic":
        return os.getenv("ANTHROPIC_MODEL", DEFAULT_ANTHROPIC_MODEL)
    return "extractive"


def _gemini(system: str, user: str, max_tokens: int) -> str:
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if not key:
        raise LLMUnavailable("GEMINI_API_KEY is not set")

    payload = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": {
            # Reasoning tokens are drawn from this same budget, so leave room
            # above the ~250 tokens an answer actually needs.
            "maxOutputTokens": max_tokens,
            "temperature": 0.2,
            "thinkingConfig": {"thinkingBudget": GEMINI_THINKING_BUDGET},
        },
    }
    data = json.dumps(payload).encode("utf-8")
    url = GEMINI_ENDPOINT.format(model=model_name("gemini"))
    headers = {"x-goog-api-key": key, "Content-Type": "application/json"}

    body = None
    for attempt in range(3):
        request = urllib.request.Request(url, data=data, headers=headers)
        try:
            with urllib.request.urlopen(request,
                                        timeout=TIMEOUT_SECONDS) as resp:
                body = json.load(resp)
            break
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            # The Lite models reject thinkingConfig outright. Rather than keep
            # a list of which models accept it, drop it and try once more.
            if (exc.code == 400
                    and "thinkingConfig" in payload["generationConfig"]):
                payload["generationConfig"].pop("thinkingConfig")
                data = json.dumps(payload).encode("utf-8")
                continue
            if exc.code in RETRY_STATUSES and attempt < 2:
                time.sleep(1.5)
                continue
            raise LLMUnavailable(
                f"Gemini HTTP {exc.code}: {detail[:160]}") from exc
        except Exception as exc:
            raise LLMUnavailable(f"Gemini request failed: {exc}") from exc

    if body is None:
        raise LLMUnavailable("Gemini did not return a response")

    candidates = body.get("candidates") or []
    if not candidates:
        blocked = body.get("promptFeedback", {}).get("blockReason")
        raise LLMUnavailable(f"Gemini returned no candidate ({blocked})")

    candidate = candidates[0]
    parts = candidate.get("content", {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts).strip()
    if not text:
        # Usually the whole budget went to reasoning tokens.
        raise LLMUnavailable(
            f"Gemini returned no text (finish: {candidate.get('finishReason')})")
    return text


def _anthropic(system: str, user: str, max_tokens: int) -> str:
    key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not key:
        raise LLMUnavailable("ANTHROPIC_API_KEY is not set")
    try:
        import anthropic
    except ImportError as exc:
        raise LLMUnavailable("the anthropic package is not installed") from exc

    try:
        client = anthropic.Anthropic(api_key=key)
        message = client.messages.create(
            model=model_name("anthropic"),
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
    except Exception as exc:
        raise LLMUnavailable(f"{type(exc).__name__}: {exc}") from exc

    text = "".join(b.text for b in message.content
                   if getattr(b, "type", "") == "text").strip()
    if not text:
        raise LLMUnavailable("Claude returned no text")
    return text


def complete(system: str, user: str, max_tokens: int = 2048) -> str:
    """Return the model's answer, or raise LLMUnavailable."""
    provider = active_provider()
    if provider == "gemini":
        return _gemini(system, user, max_tokens)
    if provider == "anthropic":
        return _anthropic(system, user, max_tokens)
    raise LLMUnavailable("no language model is configured")
