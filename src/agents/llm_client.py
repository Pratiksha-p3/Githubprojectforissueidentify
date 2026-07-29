"""
src/agents/llm_client.py

Single dispatch point for every LLM call in this project. Every caller
goes through call_llm() instead of instantiating its own Groq/OpenAI/
Anthropic client directly — that's what makes LLM_PROVIDER an actual
runtime switch instead of a config field nobody reads.

Enforces two of the non-functional requirements at this one choke point
rather than per call site: a token budget cap (never exceeds
settings.max_review_tokens regardless of what a caller asks for) and
retry-with-backoff on transient failures (rate limits, timeouts,
connection errors) via src/core/backoff.py — a non-retryable failure
(e.g. an invalid API key) is raised immediately instead of being retried
uselessly three times.

Deterministic calls (temperature=0) are cached (src/cache/llm_cache.py)
so re-analyzing unchanged content is a cache hit, not a fresh API call.
"""
from __future__ import annotations

from typing import Any

from src.cache import llm_cache
from src.core.backoff import call_with_backoff
from src.core.config import settings

# Opaque third-party SDK client instances (Groq/OpenAI/Anthropic) — typed
# as Any deliberately rather than importing all three SDKs' client types
# just for a cast.
_clients: dict[str, Any] = {}


def call_llm(
    system: str,
    user: str,
    *,
    temperature: float = 0,
    max_tokens: int | None = None,
) -> str:
    provider = (settings.llm_provider or "groq").lower()
    budget = (
        min(max_tokens, settings.max_review_tokens)
        if max_tokens
        else settings.max_review_tokens
    )

    cache_key = None
    if temperature == 0:
        model = _model_for(provider)
        cache_key = llm_cache.make_key(provider, model, system, user, str(budget))
        cached = llm_cache.get(cache_key)
        if cached is not None:
            return cached

    if provider == "openai":
        result = _call_openai(system, user, temperature, budget)
    elif provider == "anthropic":
        result = _call_anthropic(system, user, temperature, budget)
    else:
        result = _call_groq(system, user, temperature, budget)

    if cache_key is not None:
        llm_cache.set(cache_key, result)

    return result


def _model_for(provider: str) -> str:
    if provider == "openai":
        return settings.openai_model
    if provider == "anthropic":
        return settings.anthropic_model
    return settings.review_model


def _is_retryable(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in ("rate_limit", "429", "timeout", "connection"))


def _call_groq(system: str, user: str, temperature: float, max_tokens: int) -> str:
    if "groq" not in _clients:
        from groq import Groq

        _clients["groq"] = Groq(api_key=settings.groq_api_key)
    client = _clients["groq"]

    def _do_call() -> str:
        resp = client.chat.completions.create(
            model=settings.review_model,
            temperature=temperature,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return resp.choices[0].message.content or ""

    return call_with_backoff(_do_call, should_retry=_is_retryable)


def _call_openai(system: str, user: str, temperature: float, max_tokens: int) -> str:
    if not settings.openai_api_key:
        raise RuntimeError("LLM_PROVIDER=openai but OPENAI_API_KEY is not set")
    if "openai" not in _clients:
        from openai import OpenAI

        _clients["openai"] = OpenAI(api_key=settings.openai_api_key)
    client = _clients["openai"]

    def _do_call() -> str:
        resp = client.chat.completions.create(
            model=settings.openai_model,
            temperature=temperature,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return resp.choices[0].message.content or ""

    return call_with_backoff(_do_call, should_retry=_is_retryable)


def _call_anthropic(system: str, user: str, temperature: float, max_tokens: int) -> str:
    if not settings.anthropic_api_key:
        raise RuntimeError("LLM_PROVIDER=anthropic but ANTHROPIC_API_KEY is not set")
    if "anthropic" not in _clients:
        from anthropic import Anthropic

        _clients["anthropic"] = Anthropic(api_key=settings.anthropic_api_key)
    client = _clients["anthropic"]

    def _do_call() -> str:
        resp = client.messages.create(
            model=settings.anthropic_model,
            temperature=temperature,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return "".join(block.text for block in resp.content if hasattr(block, "text"))

    return call_with_backoff(_do_call, should_retry=_is_retryable)
