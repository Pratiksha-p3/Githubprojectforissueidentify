import sys
import types

import pytest

from src.core.config import settings
from src.embeddings import embed


class _FakeArray:
    def __init__(self, values: list[float]):
        self._values = values

    def tolist(self) -> list[float]:
        return self._values


class _FakeSentenceTransformerModel:
    def __init__(self):
        self.encode_calls: list[str] = []

    def encode(self, text: str, normalize_embeddings: bool = True) -> _FakeArray:
        self.encode_calls.append(text)
        return _FakeArray([float(len(text)), 0.0, 0.0])


@pytest.fixture
def fake_model(monkeypatch):
    """Installs a fake sentence_transformers module into sys.modules so
    _embed_local's lazy `from sentence_transformers import
    SentenceTransformer` never touches the real (~90MB) model."""
    model = _FakeSentenceTransformerModel()
    fake_module = types.ModuleType("sentence_transformers")
    fake_module.SentenceTransformer = lambda model_name: model  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_module)
    return model


@pytest.fixture(autouse=True)
def _isolated_state(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "embedding_provider", "local")
    monkeypatch.setattr(embed, "_model", None)
    import src.cache.embedding_cache as cache_mod

    monkeypatch.setattr(cache_mod, "_CACHE_DIR", tmp_path / "embeddings")
    yield
    monkeypatch.setattr(embed, "_model", None)


def test_local_embedding_uses_sentence_transformers_model(fake_model):
    vector = embed.embed_text("hello world")
    assert vector == [11.0, 0.0, 0.0]
    assert fake_model.encode_calls == ["hello world"]


def test_result_is_cached_and_second_call_skips_the_model(fake_model):
    first = embed.embed_text("hello")
    second = embed.embed_text("hello")

    assert first == second
    assert len(fake_model.encode_calls) == 1


def test_different_text_is_not_cached_together(fake_model):
    embed.embed_text("hello")
    embed.embed_text("goodbye")
    assert len(fake_model.encode_calls) == 2


def test_openai_provider_requires_api_key(monkeypatch):
    monkeypatch.setattr(settings, "embedding_provider", "openai")
    monkeypatch.setattr(settings, "openai_api_key", "")

    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        embed.embed_text("hello")


def test_embed_texts_embeds_each_item_independently(fake_model):
    vectors = embed.embed_texts(["a", "bb"])
    assert len(vectors) == 2
    assert vectors[0] != vectors[1]
