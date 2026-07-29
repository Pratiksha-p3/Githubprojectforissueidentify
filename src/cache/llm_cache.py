"""
src/cache/llm_cache.py

Cache for deterministic (temperature=0) LLM calls, keyed on the full
prompt so identical file content produces a cache hit instead of a fresh
API call — the concrete mechanism behind the "embedding/LLM cache"
nonfunctional requirement (token budget / cost control).

Disk-backed for now (one JSON file per key, under .cache/llm/) since
Redis isn't wired in until Stage 4 — same get/set/make_key interface a
Redis-backed implementation will expose later, so callers don't change
when the backend does.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

_CACHE_DIR = Path(".cache/llm")


def make_key(*parts: str) -> str:
    joined = "\x1f".join(parts)  # unit separator — won't collide with real content
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def get(key: str, *, cache_dir: Path | None = None) -> str | None:
    # Resolved at call time (not bound as a default-arg value) so tests
    # can monkeypatch the module-level _CACHE_DIR and have it take effect.
    target_dir = cache_dir if cache_dir is not None else _CACHE_DIR
    path = target_dir / f"{key}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("value")
    except (json.JSONDecodeError, OSError):
        return None


def set(key: str, value: str, *, cache_dir: Path | None = None) -> None:  # noqa: A001 - mirrors dict-like get/set pairing
    target_dir = cache_dir if cache_dir is not None else _CACHE_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"{key}.json"
    path.write_text(json.dumps({"value": value}), encoding="utf-8")
