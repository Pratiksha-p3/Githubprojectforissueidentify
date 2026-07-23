"""
cache/redis_cache.py

Optional Redis cache for LLM completions. Every agent in this project now
calls agents.llm_client.chat_completion() for its LLM calls (see that
module) — this cache sits in front of it, so setting REDIS_URL speeds up
and reduces the cost of re-reviewing unchanged code (same file content ->
same prompt -> cache hit) without touching any individual agent.

Inert without REDIS_URL: get() always returns None and set() is a no-op,
so behavior is identical to today (every call hits the LLM) until this is
configured.

Install: pip install redis
"""
from __future__ import annotations

import hashlib

from config import cfg

_client = None
_unavailable = False


def is_configured() -> bool:
    return bool(cfg.redis_url)


def make_key(*parts: str) -> str:
    """Stable cache key from arbitrary string parts (e.g. model + prompt)."""
    h = hashlib.sha256()
    for p in parts:
        h.update(p.encode("utf-8", errors="ignore"))
        h.update(b"\x00")
    return f"llm:{h.hexdigest()}"


def get(key: str) -> str | None:
    client = _get_client()
    if client is None:
        return None
    try:
        value = client.get(key)
        return value.decode("utf-8") if value is not None else None
    except Exception as e:
        print(f"[redis-cache] get failed: {e}")
        return None


def set(key: str, value: str, ttl: int = None) -> None:
    client = _get_client()
    if client is None:
        return
    try:
        client.set(key, value, ex=ttl or cfg.cache_ttl_seconds)
    except Exception as e:
        print(f"[redis-cache] set failed: {e}")


# ── Internal ──────────────────────────────────────────────────────────────

def _get_client():
    global _client, _unavailable
    if _client is not None:
        return _client
    if _unavailable or not is_configured():
        return None
    try:
        import redis
        _client = redis.from_url(cfg.redis_url, socket_connect_timeout=3)
        _client.ping()
        print("[redis-cache] Connected")
        return _client
    except Exception as e:
        print(f"[redis-cache] Unavailable, falling back to no-op: {e}")
        _unavailable = True
        return None
