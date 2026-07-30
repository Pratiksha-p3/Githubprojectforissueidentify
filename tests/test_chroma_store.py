from src.ingestion.chunker import CodeChunk
from src.vectordb.chroma_store import ChromaStore


def make_store(tmp_path) -> ChromaStore:
    return ChromaStore(collection_name="test_collection", persist_dir=str(tmp_path))


def make_chunk(filename="app.py", start=1, end=2, name="f") -> CodeChunk:
    return CodeChunk(
        filename=filename, content="def f():\n    pass",
        start_line=start, end_line=end, chunk_type="function", name=name,
    )


def test_empty_store_returns_no_results(tmp_path):
    store = make_store(tmp_path)
    assert store.count() == 0
    assert store.query([1.0, 0.0, 0.0]) == []


def test_upsert_then_query_returns_the_chunk(tmp_path):
    store = make_store(tmp_path)
    chunk = make_chunk()
    store.upsert([chunk], [[1.0, 0.0, 0.0]])

    assert store.count() == 1
    results = store.query([1.0, 0.0, 0.0], top_k=5)
    assert len(results) == 1
    assert results[0].chunk.filename == "app.py"
    assert results[0].chunk.name == "f"
    assert results[0].score > 0.9  # identical vector -> cosine similarity ~1.0


def test_query_ranks_closer_vectors_higher(tmp_path):
    store = make_store(tmp_path)
    store.upsert(
        [make_chunk(name="close"), make_chunk(name="far", start=10, end=11)],
        [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]],
    )

    results = store.query([1.0, 0.0, 0.0], top_k=2)
    assert results[0].chunk.name == "close"


def test_delete_file_removes_only_that_files_chunks(tmp_path):
    store = make_store(tmp_path)
    store.upsert(
        [make_chunk(filename="a.py"), make_chunk(filename="b.py")],
        [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]],
    )
    assert store.count() == 2

    store.delete_file("a.py")

    assert store.count() == 1
    remaining = store.query([0.0, 1.0, 0.0], top_k=5)
    assert remaining[0].chunk.filename == "b.py"


def test_upsert_with_no_chunks_is_a_no_op(tmp_path):
    store = make_store(tmp_path)
    store.upsert([], [])
    assert store.count() == 0


def test_reupserting_same_chunk_location_replaces_not_duplicates(tmp_path):
    store = make_store(tmp_path)
    chunk = make_chunk()
    store.upsert([chunk], [[1.0, 0.0, 0.0]])
    store.upsert([chunk], [[1.0, 0.0, 0.0]])  # same filename/start/end -> same id
    assert store.count() == 1
