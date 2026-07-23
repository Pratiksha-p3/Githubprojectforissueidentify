"""
agents/llm_client.py

Single dispatch point for every LLM call in this project. Every agent that
needs a completion should call chat_completion() instead of instantiating
its own Groq/OpenAI/Anthropic client directly — that's what makes
LLM_PROVIDER=groq|openai|anthropic an actual switch instead of a config
field nobody reads.

Defaults to Groq (free, no signup beyond the existing GROQ_API_KEY).
Set LLM_PROVIDER=openai (with OPENAI_API_KEY + OPENAI_MODEL, default
gpt-4o) or LLM_PROVIDER=anthropic (with ANTHROPIC_API_KEY +
ANTHROPIC_MODEL) to switch the whole pipeline over.
"""
from __future__ import annotations

from config import cfg

_clients: dict[str, object] = {}


def chat_completion(
    system: str,
    user: str,
    temperature: float = 0,
    max_tokens: int = 2048,
) -> str:
    """Returns raw completion text from whichever provider LLM_PROVIDER selects.

    Deterministic calls (temperature=0, the vast majority in this project)
    are cached in Redis when REDIS_URL is set — same file content produces
    the same prompt, so re-reviewing unchanged code is a cache hit instead
    of a fresh LLM call. Non-zero temperature calls (e.g. test generation,
    which wants varied output) skip the cache.
    """
    provider = (cfg.llm_provider or "groq").lower()
    cache_key = None
    if temperature == 0:
        from cache.redis_cache import get as cache_get, make_key
        cache_key = make_key(provider, cfg.review_model, system, user, str(max_tokens))
        cached = cache_get(cache_key)
        if cached is not None:
            return cached

    if provider == "openai":
        result = _call_openai(system, user, temperature, max_tokens)
    elif provider == "anthropic":
        result = _call_anthropic(system, user, temperature, max_tokens)
    else:
        result = _call_groq(system, user, temperature, max_tokens)

    if cache_key is not None:
        from cache.redis_cache import set as cache_set
        cache_set(cache_key, result)

    return result


def _call_groq(system: str, user: str, temperature: float, max_tokens: int) -> str:
    if "groq" not in _clients:
        from groq import Groq
        _clients["groq"] = Groq(api_key=cfg.groq_api_key)
    resp = _clients["groq"].chat.completions.create(
        model=cfg.review_model,
        temperature=temperature,
        max_tokens=max_tokens,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    return resp.choices[0].message.content or ""


def _call_openai(system: str, user: str, temperature: float, max_tokens: int) -> str:
    if not cfg.openai_api_key:
        raise RuntimeError("LLM_PROVIDER=openai but OPENAI_API_KEY is not set")
    if "openai" not in _clients:
        from openai import OpenAI
        _clients["openai"] = OpenAI(api_key=cfg.openai_api_key)
    resp = _clients["openai"].chat.completions.create(
        model=cfg.openai_model,
        temperature=temperature,
        max_tokens=max_tokens,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    return resp.choices[0].message.content or ""


def _call_anthropic(system: str, user: str, temperature: float, max_tokens: int) -> str:
    if not cfg.anthropic_api_key:
        raise RuntimeError("LLM_PROVIDER=anthropic but ANTHROPIC_API_KEY is not set")
    if "anthropic" not in _clients:
        from anthropic import Anthropic
        _clients["anthropic"] = Anthropic(api_key=cfg.anthropic_api_key)
    resp = _clients["anthropic"].messages.create(
        model=cfg.anthropic_model,
        temperature=temperature,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return "".join(block.text for block in resp.content if hasattr(block, "text"))
