"""
src/eval/rag_eval.py

Precision/recall evaluation for the RAG retriever (src/rag/retriever.py)
against a labeled corpus: for each (query, expected_relevant_ids) pair,
retrieve top-k results and check how many retrieved chunks are in the
expected-relevant set. Wired as a real pytest assertion
(tests/test_rag_eval.py) rather than a separate CI script — a pytest
failure already IS the CI gate this project's workflow enforces, so
"RAG eval must pass a threshold before deploy" doesn't need a second
mechanism bolted on alongside the test suite that already blocks merges.
"""
from __future__ import annotations

from dataclasses import dataclass

from src.vectordb.chroma_store import ChromaStore, RetrievedChunk


@dataclass
class LabeledQuery:
    query_vector: list[float]
    relevant_chunk_ids: set[str]


@dataclass
class EvalResult:
    precision: float
    recall: float
    queries_evaluated: int


def chunk_id(retrieved: RetrievedChunk) -> str:
    return f"{retrieved.chunk.filename}:{retrieved.chunk.start_line}"


def evaluate_retrieval(
    labeled_queries: list[LabeledQuery], store: ChromaStore, *, top_k: int = 5
) -> EvalResult:
    if not labeled_queries:
        return EvalResult(precision=1.0, recall=1.0, queries_evaluated=0)

    precisions = []
    recalls = []
    for lq in labeled_queries:
        results = store.query(lq.query_vector, top_k=top_k)
        retrieved_ids = {chunk_id(r) for r in results}

        if not retrieved_ids:
            perfect = not lq.relevant_chunk_ids
            precisions.append(1.0 if perfect else 0.0)
            recalls.append(1.0 if perfect else 0.0)
            continue

        true_positives = retrieved_ids & lq.relevant_chunk_ids
        precisions.append(len(true_positives) / len(retrieved_ids))
        recalls.append(
            len(true_positives) / len(lq.relevant_chunk_ids) if lq.relevant_chunk_ids else 1.0
        )

    return EvalResult(
        precision=sum(precisions) / len(precisions),
        recall=sum(recalls) / len(recalls),
        queries_evaluated=len(labeled_queries),
    )
