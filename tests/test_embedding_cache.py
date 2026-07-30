from src.cache import embedding_cache


def test_make_key_is_deterministic():
    key1 = embedding_cache.make_key("local", "model", "some text")
    key2 = embedding_cache.make_key("local", "model", "some text")
    assert key1 == key2


def test_make_key_differs_for_different_text():
    key1 = embedding_cache.make_key("local", "model", "text A")
    key2 = embedding_cache.make_key("local", "model", "text B")
    assert key1 != key2


def test_get_returns_none_for_missing_key(tmp_path):
    assert embedding_cache.get("missing", cache_dir=tmp_path) is None


def test_set_then_get_roundtrips_a_vector(tmp_path):
    vector = [0.1, 0.2, 0.3]
    embedding_cache.set("some-key", vector, cache_dir=tmp_path)
    assert embedding_cache.get("some-key", cache_dir=tmp_path) == vector


def test_get_returns_none_for_corrupted_cache_file(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "corrupted.json").write_text("not valid json{{{", encoding="utf-8")
    assert embedding_cache.get("corrupted", cache_dir=tmp_path) is None
