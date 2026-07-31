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
Cache hits are served before the circuit breaker is even checked — an
open circuit means "don't make new calls," not "stop serving results
already known to be good."

Stage 14 adds two more concerns at this same choke point:

- Circuit breaker (src/core/circuit_breaker.py): every real provider
  call is wrapped so a burst of failures opens the circuit and further
  calls fail fast (CircuitOpenError) instead of each queuing up its own
  three-attempt backoff against a provider that's already down.
- Canary routing (src/core/canary.py): an optional `canary_key` lets a
  caller opt a specific (repo, commit_sha) into a candidate model
  version deterministically, without touching every call site — omitting
  it (the default) always resolves to the stable model, so existing
  callers are unaffected.
"""
from __future__ import annotations

from typing import Any

from src.cache import llm_cache
from src.core.backoff import call_with_backoff
from src.core.canary import variant_for
from src.core.circuit_breaker import CircuitOpenError, breaker
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
    canary_key: str | None = None,
) -> str:
    provider = (settings.llm_provider or "groq").lower()
    variant = variant_for(canary_key, settings.canary_rollout_percent) if canary_key else "stable"
    model = _model_for(provider, variant)
    budget = (
        min(max_tokens, settings.max_review_tokens)
        if max_tokens
        else settings.max_review_tokens
    )

    cache_key = None
    if temperature == 0:
        cache_key = llm_cache.make_key(provider, model, system, user, str(budget))
        cached = llm_cache.get(cache_key)
        if cached is not None:
            return cached

    if not breaker.allow_request():
        raise CircuitOpenError(
            f"LLM circuit breaker is open for provider={provider!r} "
            f"(too many recent failures) — call skipped"
        )

    try:
        if provider == "openai":
            result = _call_openai(system, user, temperature, budget, model)
        elif provider == "anthropic":
            result = _call_anthropic(system, user, temperature, budget, model)
        else:
            result = _call_groq(system, user, temperature, budget, model)
    except Exception:
        breaker.record_failure()
        raise
    breaker.record_success()

    if cache_key is not None:
        llm_cache.set(cache_key, result)

    return result


def _model_for(provider: str, variant: str) -> str:
    is_canary = variant == "canary"
    if provider == "openai":
        return (settings.canary_openai_model if is_canary else "") or settings.openai_model
    if provider == "anthropic":
        return (settings.canary_anthropic_model if is_canary else "") or settings.anthropic_model
    return (settings.canary_review_model if is_canary else "") or settings.review_model


def _is_retryable(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in ("rate_limit", "429", "timeout", "connection"))


def _call_groq(system: str, user: str, temperature: float, max_tokens: int, model: str) -> str:
    if "groq" not in _clients:
        from groq import Groq

        _clients["groq"] = Groq(api_key=settings.groq_api_key)
    client = _clients["groq"]

    def _do_call() -> str:
        resp = client.chat.completions.create(
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return resp.choices[0].message.content or ""

    return call_with_backoff(_do_call, should_retry=_is_retryable)


def _call_openai(system: str, user: str, temperature: float, max_tokens: int, model: str) -> str:
    if not settings.openai_api_key:
        raise RuntimeError("LLM_PROVIDER=openai but OPENAI_API_KEY is not set")
    if "openai" not in _clients:
        from openai import OpenAI

        _clients["openai"] = OpenAI(api_key=settings.openai_api_key)
    client = _clients["openai"]

    def _do_call() -> str:
        resp = client.chat.completions.create(
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return resp.choices[0].message.content or ""

    return call_with_backoff(_do_call, should_retry=_is_retryable)


def _call_anthropic(
    system: str, user: str, temperature: float, max_tokens: int, model: str
) -> str:
    if not settings.anthropic_api_key:
        raise RuntimeError("LLM_PROVIDER=anthropic but ANTHROPIC_API_KEY is not set")
    if "anthropic" not in _clients:
        from anthropic import Anthropic

        _clients["anthropic"] = Anthropic(api_key=settings.anthropic_api_key)
    client = _clients["anthropic"]

    def _do_call() -> str:
        resp = client.messages.create(
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return "".join(block.text for block in resp.content if hasattr(block, "text"))

    return call_with_backoff(_do_call, should_retry=_is_retryable)
