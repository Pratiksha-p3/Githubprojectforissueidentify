"""
tests/test_rag_eval.py

RAG eval as a real CI gate: a labeled corpus of chunks + queries with
known relevant chunks per query, retrieved through the real ChromaStore
(hand-crafted embedding vectors, not the actual sentence-transformers
model, so this test is fast and deterministic) — if precision/recall
drops below the threshold, this test fails the same way any other test
failure blocks a merge.
"""
from __future__ import annotations

from src.eval.rag_eval import EvalResult, LabeledQuery, evaluate_retrieval
from src.ingestion.chunker import CodeChunk
from src.vectordb.chroma_store import ChromaStore

_PRECISION_THRESHOLD = 0.7
_RECALL_THRESHOLD = 0.7


def make_chunk(filename: str, start_line: int, content: str = "pass") -> CodeChunk:
    return CodeChunk(
        filename=filename,
        content=content,
        start_line=start_line,
        end_line=start_line,
        chunk_type="function",
        name="f",
    )


def build_labeled_store(tmp_path) -> ChromaStore:
    store = ChromaStore(collection_name="rag_eval", persist_dir=str(tmp_path))
    chunks = [
        make_chunk("auth.py", 1, "def hash_password(pw): return hashlib.sha256(pw)"),
        make_chunk("auth.py", 10, "def verify_password(pw, hashed): return hash_password(pw)"),
        make_chunk("orders.py", 1, "class Order: pass"),
        make_chunk("orders.py", 10, "def calculate_tax(amount, rate): return amount * rate"),
    ]
    # Hand-crafted, well-separated vectors — no need for the real
    # sentence-transformers model to test the precision/recall *math*,
    # only that ChromaStore + rag_eval correctly compute it over
    # whatever similarity scores come back.
    embeddings = [
        [1.0, 0.0, 0.0, 0.0],
        [0.9, 0.1, 0.0, 0.0],
        [0.0, 0.0, 1.0, 0.0],
        [0.0, 0.0, 0.9, 0.1],
    ]
    store.upsert(chunks, embeddings)
    return store


def test_precision_and_recall_meet_threshold_on_a_clearly_separated_corpus(tmp_path):
    store = build_labeled_store(tmp_path)

    labeled_queries = [
        LabeledQuery(
            query_vector=[1.0, 0.0, 0.0, 0.0],
            relevant_chunk_ids={"auth.py:1", "auth.py:10"},
        ),
        LabeledQuery(
            query_vector=[0.0, 0.0, 1.0, 0.0],
            relevant_chunk_ids={"orders.py:1", "orders.py:10"},
        ),
    ]

    result = evaluate_retrieval(labeled_queries, store, top_k=2)

    assert result.precision >= _PRECISION_THRESHOLD, (
        f"RAG retrieval precision {result.precision:.2f} is below the "
        f"{_PRECISION_THRESHOLD} threshold — this is the CI gate that "
        f"blocks a regression in retrieval quality from merging."
    )
    assert result.recall >= _RECALL_THRESHOLD
    assert result.queries_evaluated == 2


def test_empty_labeled_queries_is_trivially_perfect():
    class _UnusedStore:
        def query(self, *a, **k):
            raise AssertionError("store must not be queried when there are no labeled queries")

    result = evaluate_retrieval([], store=_UnusedStore())
    assert result.precision == 1.0
    assert result.recall == 1.0
    assert result.queries_evaluated == 0


def test_eval_result_is_a_plain_dataclass():
    result = EvalResult(precision=0.5, recall=0.8, queries_evaluated=3)
    assert result.precision == 0.5
    assert result.recall == 0.8
    assert result.queries_evaluated == 3
