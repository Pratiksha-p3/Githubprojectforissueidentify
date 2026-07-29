import pytest

from src.agents import llm_client
from src.core.config import settings


class _FakeMessage:
    def __init__(self, content: str):
        self.content = content


class _FakeChoice:
    def __init__(self, content: str):
        self.message = _FakeMessage(content)


class _FakeCompletionResponse:
    def __init__(self, content: str):
        self.choices = [_FakeChoice(content)]


class _FakeChatCompletions:
    def __init__(self, content: str, calls: list[dict]):
        self._content = content
        self._calls = calls

    def create(self, **kwargs):
        self._calls.append(kwargs)
        return _FakeCompletionResponse(self._content)


class _FakeChat:
    def __init__(self, content: str, calls: list[dict]):
        self.completions = _FakeChatCompletions(content, calls)


class _FakeGroqClient:
    def __init__(self, content: str, calls: list[dict]):
        self.chat = _FakeChat(content, calls)


@pytest.fixture(autouse=True)
def _isolated_client_and_cache(monkeypatch, tmp_path):
    llm_client._clients.clear()
    monkeypatch.setattr(settings, "llm_provider", "groq")
    import src.cache.llm_cache as llm_cache

    monkeypatch.setattr(llm_cache, "_CACHE_DIR", tmp_path / "llm_cache")
    yield
    llm_client._clients.clear()


def test_groq_provider_is_used_by_default():
    calls: list[dict] = []
    llm_client._clients["groq"] = _FakeGroqClient("hello from groq", calls)

    result = llm_client.call_llm("system", "user", temperature=0)

    assert result == "hello from groq"
    assert calls[0]["model"] == settings.review_model


def test_deterministic_calls_are_cached_and_skip_a_second_api_call():
    calls: list[dict] = []
    llm_client._clients["groq"] = _FakeGroqClient("cached response", calls)

    first = llm_client.call_llm("system", "user", temperature=0)
    second = llm_client.call_llm("system", "user", temperature=0)

    assert first == second == "cached response"
    assert len(calls) == 1


def test_non_deterministic_calls_are_never_cached():
    calls: list[dict] = []
    llm_client._clients["groq"] = _FakeGroqClient("response", calls)

    llm_client.call_llm("system", "user", temperature=0.7)
    llm_client.call_llm("system", "user", temperature=0.7)

    assert len(calls) == 2


def test_max_tokens_is_capped_at_the_configured_budget(monkeypatch):
    monkeypatch.setattr(settings, "max_review_tokens", 500)
    calls: list[dict] = []
    llm_client._clients["groq"] = _FakeGroqClient("response", calls)

    llm_client.call_llm("system", "user", temperature=0.5, max_tokens=99999)

    assert calls[0]["max_tokens"] == 500


def test_openai_provider_requires_api_key(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "openai")
    monkeypatch.setattr(settings, "openai_api_key", "")

    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        llm_client.call_llm("system", "user", temperature=0.5)


def test_anthropic_provider_requires_api_key(monkeypatch):
    monkeypatch.setattr(settings, "llm_provider", "anthropic")
    monkeypatch.setattr(settings, "anthropic_api_key", "")

    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        llm_client.call_llm("system", "user", temperature=0.5)
