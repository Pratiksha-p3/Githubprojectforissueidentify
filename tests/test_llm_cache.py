from src.cache import llm_cache


def test_make_key_is_deterministic():
    key1 = llm_cache.make_key("groq", "model", "system", "user", "100")
    key2 = llm_cache.make_key("groq", "model", "system", "user", "100")
    assert key1 == key2


def test_make_key_differs_for_different_inputs():
    key1 = llm_cache.make_key("groq", "model", "system", "user", "100")
    key2 = llm_cache.make_key("groq", "model", "system", "other-user", "100")
    assert key1 != key2


def test_get_returns_none_for_missing_key(tmp_path):
    assert llm_cache.get("nonexistent", cache_dir=tmp_path) is None


def test_set_then_get_roundtrips(tmp_path):
    llm_cache.set("some-key", "some-value", cache_dir=tmp_path)
    assert llm_cache.get("some-key", cache_dir=tmp_path) == "some-value"


def test_get_returns_none_for_corrupted_cache_file(tmp_path):
    bad_file = tmp_path / "corrupted.json"
    tmp_path.mkdir(parents=True, exist_ok=True)
    bad_file.write_text("not valid json{{{", encoding="utf-8")
    assert llm_cache.get("corrupted", cache_dir=tmp_path) is None
