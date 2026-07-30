from src.cli import index_repo
from src.vectordb.chroma_store import ChromaStore


def fake_embed_texts(texts):
    return [[float(i), 0.0, 0.0] for i, _ in enumerate(texts)]


def test_index_directory_indexes_python_files(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(index_repo, "embed_texts", fake_embed_texts)

    (tmp_path / "app.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (tmp_path / "utils.py").write_text("def helper():\n    pass\n", encoding="utf-8")

    store = ChromaStore(collection_name="test_index", persist_dir=str(tmp_path / ".chroma"))
    exit_code = index_repo.index_directory(str(tmp_path), store=store)

    assert exit_code == 0
    assert store.count() == 2  # one function chunk per file
    out = capsys.readouterr().out
    assert "app.py" in out
    assert "utils.py" in out


def test_index_directory_skips_venv_and_pycache(tmp_path, monkeypatch):
    monkeypatch.setattr(index_repo, "embed_texts", fake_embed_texts)

    (tmp_path / "app.py").write_text("def f():\n    pass\n", encoding="utf-8")
    skip_dir = tmp_path / "venv" / "lib"
    skip_dir.mkdir(parents=True)
    (skip_dir / "vendored.py").write_text("def should_not_index():\n    pass\n", encoding="utf-8")

    store = ChromaStore(collection_name="test_skip", persist_dir=str(tmp_path / ".chroma"))
    index_repo.index_directory(str(tmp_path), store=store)

    assert store.count() == 1


def test_index_directory_returns_nonzero_for_missing_directory():
    assert index_repo.index_directory("does_not_exist_dir") == 1


def test_reindexing_replaces_rather_than_duplicates(tmp_path, monkeypatch):
    monkeypatch.setattr(index_repo, "embed_texts", fake_embed_texts)

    app_file = tmp_path / "app.py"
    app_file.write_text("def f():\n    pass\n", encoding="utf-8")

    store = ChromaStore(collection_name="test_reindex", persist_dir=str(tmp_path / ".chroma"))
    index_repo.index_directory(str(tmp_path), store=store)
    index_repo.index_directory(str(tmp_path), store=store)

    assert store.count() == 1
