"""
src/rag/retriever.py

Given a file's content, retrieves the most relevant existing chunks from
the repo's vector index (src/vectordb/chroma_store.py) — "similar
existing code elsewhere in the repo" context that src/agents/
llm_supplement.py can use to reason about consistency with the rest of
the codebase, not just the file in isolation.

Filters out chunks below MIN_SIMILARITY_SCORE and chunks from the same
file being reviewed (that's not "other context", that's the file
itself) — low-quality context measurably hurts LLM accuracy more than
it helps, the same principle the previous implementation's retriever
applied.
"""
from __future__ import annotations

from src.core.config import settings
from src.embeddings.embed import embed_text
from src.vectordb.chroma_store import ChromaStore, RetrievedChunk

_QUERY_TEXT_CHARS = 2000  # enough signal for a similarity query without
# embedding an entire (possibly huge) file for every retrieval call.


def retrieve_context(
    code: str,
    filename: str,
    *,
    store: ChromaStore | None = None,
    top_k: int | None = None,
) -> list[RetrievedChunk]:
    store = store or ChromaStore()
    top_k = top_k or settings.top_k_retrieval

    query_vector = embed_text(code[:_QUERY_TEXT_CHARS])
    # Over-fetch since same-file and below-threshold results get
    # filtered out below and shouldn't shrink the final result count.
    results = store.query(query_vector, top_k=top_k + 5)

    filtered = [
        r
        for r in results
        if r.chunk.filename != filename and r.score >= settings.min_similarity_score
    ]
    return filtered[:top_k]


def format_context_for_prompt(chunks: list[RetrievedChunk]) -> str:
    """Renders retrieved chunks into the text block
    src/agents/llm_supplement.py's prompt embeds as extra context."""
    if not chunks:
        return ""

    sections = []
    for i, rc in enumerate(chunks, start=1):
        c = rc.chunk
        header = (
            f"--- Context {i} | {c.filename} | {c.chunk_type} {c.name} "
            f"| score={rc.score:.2f} ---"
        )
        sections.append(f"{header}\n{c.content}")
    return "\n\n".join(sections)
