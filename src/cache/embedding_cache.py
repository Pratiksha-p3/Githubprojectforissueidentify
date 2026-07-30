"""
src/cache/embedding_cache.py

Cache for embedding vectors, keyed on (provider, model, text) — embedding
a given text is deterministic, so re-indexing a repo where most files are
unchanged since the last run is almost entirely cache hits instead of
fresh embedding calls. Same disk-backed-for-now pattern as
src/cache/llm_cache.py; a Redis-backed version is a drop-in swap once
embeddings need to be shared across worker processes rather than kept
per-machine.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

_CACHE_DIR = Path(".cache/embeddings")


def make_key(*parts: str) -> str:
    joined = "\x1f".join(parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def get(key: str, *, cache_dir: Path | None = None) -> list[float] | None:
    target_dir = cache_dir if cache_dir is not None else _CACHE_DIR
    path = target_dir / f"{key}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("vector")
    except (json.JSONDecodeError, OSError):
        return None


def set(key: str, vector: list[float], *, cache_dir: Path | None = None) -> None:  # noqa: A001
    target_dir = cache_dir if cache_dir is not None else _CACHE_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"{key}.json"
    path.write_text(json.dumps({"vector": vector}), encoding="utf-8")
