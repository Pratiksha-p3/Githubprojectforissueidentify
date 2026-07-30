from src.core.config import settings
from src.ingestion.chunker import CodeChunk
from src.rag import retriever
from src.vectordb.chroma_store import RetrievedChunk


class _FakeStore:
    def __init__(self, results):
        self._results = results
        self.queried_with = None

    def query(self, query_vector, top_k=5):
        self.queried_with = (query_vector, top_k)
        return self._results


def make_retrieved(filename, score, name="f") -> RetrievedChunk:
    chunk = CodeChunk(
        filename=filename, content="def f(): pass", start_line=1, end_line=1,
        chunk_type="function", name=name,
    )
    return RetrievedChunk(chunk=chunk, score=score)


def test_filters_out_chunks_from_the_same_file(monkeypatch):
    monkeypatch.setattr(retriever, "embed_text", lambda text: [1.0, 0.0, 0.0])
    store = _FakeStore([make_retrieved("app.py", 0.9), make_retrieved("other.py", 0.9)])

    results = retriever.retrieve_context("code", "app.py", store=store)

    assert len(results) == 1
    assert results[0].chunk.filename == "other.py"


def test_filters_out_chunks_below_similarity_threshold(monkeypatch):
    monkeypatch.setattr(retriever, "embed_text", lambda text: [1.0, 0.0, 0.0])
    monkeypatch.setattr(settings, "min_similarity_score", 0.5)
    store = _FakeStore([make_retrieved("other.py", 0.9), make_retrieved("low.py", 0.1)])

    results = retriever.retrieve_context("code", "app.py", store=store)

    filenames = {r.chunk.filename for r in results}
    assert "low.py" not in filenames
    assert "other.py" in filenames


def test_respects_top_k_limit(monkeypatch):
    monkeypatch.setattr(retriever, "embed_text", lambda text: [1.0, 0.0, 0.0])
    store = _FakeStore([make_retrieved(f"file{i}.py", 0.9) for i in range(10)])

    results = retriever.retrieve_context("code", "app.py", store=store, top_k=3)

    assert len(results) == 3


def test_query_uses_only_a_prefix_of_long_code(monkeypatch):
    captured = {}

    def fake_embed(text):
        captured["text"] = text
        return [1.0, 0.0, 0.0]

    monkeypatch.setattr(retriever, "embed_text", fake_embed)
    store = _FakeStore([])

    long_code = "x" * 5000
    retriever.retrieve_context(long_code, "app.py", store=store)

    assert len(captured["text"]) == retriever._QUERY_TEXT_CHARS


def test_format_context_for_prompt_empty_list_returns_empty_string():
    assert retriever.format_context_for_prompt([]) == ""


def test_format_context_for_prompt_includes_filename_and_content():
    chunk = make_retrieved("utils.py", 0.85, name="helper")
    formatted = retriever.format_context_for_prompt([chunk])
    assert "utils.py" in formatted
    assert "helper" in formatted
    assert "def f(): pass" in formatted
