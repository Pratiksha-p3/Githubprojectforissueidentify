"""
src/embeddings/embed.py

Single dispatch point for embedding text into vectors, mirroring
src/agents/llm_client.py's "one function every caller goes through"
pattern. Defaults to a local sentence-transformers model — free, no API
key, no network call after the model is downloaded once — so indexing a
repo has no hard external dependency. OpenAI's embedding API is an
explicit opt-in via EMBEDDING_PROVIDER, not a default.

Embedding is deterministic (same text always produces the same vector),
so every call is cached (src/cache/embedding_cache.py) keyed on
(provider, model, text) — re-indexing a repo where most files are
unchanged is almost entirely cache hits.
"""
from __future__ import annotations

from src.cache import embedding_cache
from src.core.config import settings

_model = None  # lazily loaded sentence-transformers model, one process-wide instance


def embed_text(text: str) -> list[float]:
    provider = (settings.embedding_provider or "local").lower()
    cache_key = embedding_cache.make_key(provider, settings.embedding_model, text)
    cached = embedding_cache.get(cache_key)
    if cached is not None:
        return cached

    vector = _embed_openai(text) if provider == "openai" else _embed_local(text)

    embedding_cache.set(cache_key, vector)
    return vector


def embed_texts(texts: list[str]) -> list[list[float]]:
    return [embed_text(t) for t in texts]


def _embed_local(text: str) -> list[float]:
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer

        _model = SentenceTransformer(settings.embedding_model)
    return _model.encode(text, normalize_embeddings=True).tolist()


def _embed_openai(text: str) -> list[float]:
    if not settings.openai_api_key:
        raise RuntimeError("EMBEDDING_PROVIDER=openai but OPENAI_API_KEY is not set")
    from openai import OpenAI

    client = OpenAI(api_key=settings.openai_api_key)
    resp = client.embeddings.create(model=settings.embedding_model, input=text)
    return list(resp.data[0].embedding)
