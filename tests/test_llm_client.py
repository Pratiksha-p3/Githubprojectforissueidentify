import pytest

from src.agents import llm_client
from src.core.circuit_breaker import CircuitOpenError
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


def test_repeated_failures_open_the_circuit_breaker_and_skip_the_call():
    class _AlwaysFails:
        def __getattr__(self, _name):
            raise ConnectionError("provider unreachable")

    llm_client._clients["groq"] = _AlwaysFails()

    for _ in range(5):
        with pytest.raises(ConnectionError):
            llm_client.call_llm("system", "user", temperature=0.9)

    # Breaker should now be open -- the 6th call must fail fast with
    # CircuitOpenError instead of attempting (and failing) a real call.
    calls: list[dict] = []
    llm_client._clients["groq"] = _FakeGroqClient("should not be reached", calls)

    with pytest.raises(CircuitOpenError):
        llm_client.call_llm("system", "user", temperature=0.9)
    assert calls == []


def test_canary_key_routes_to_the_canary_model_when_configured(monkeypatch):
    monkeypatch.setattr(settings, "canary_rollout_percent", 100)
    monkeypatch.setattr(settings, "canary_review_model", "candidate-model-v2")
    calls: list[dict] = []
    llm_client._clients["groq"] = _FakeGroqClient("response", calls)

    llm_client.call_llm("system", "user", temperature=0, canary_key="acme/widgets:abc123")

    assert calls[0]["model"] == "candidate-model-v2"


def test_no_canary_key_always_uses_the_stable_model(monkeypatch):
    monkeypatch.setattr(settings, "canary_rollout_percent", 100)
    monkeypatch.setattr(settings, "canary_review_model", "candidate-model-v2")
    calls: list[dict] = []
    llm_client._clients["groq"] = _FakeGroqClient("response", calls)

    llm_client.call_llm("system", "user", temperature=0)

    assert calls[0]["model"] == settings.review_model


def test_canary_key_falls_back_to_stable_model_when_no_canary_model_configured(monkeypatch):
    monkeypatch.setattr(settings, "canary_rollout_percent", 100)
    monkeypatch.setattr(settings, "canary_review_model", "")
    calls: list[dict] = []
    llm_client._clients["groq"] = _FakeGroqClient("response", calls)

    llm_client.call_llm("system", "user", temperature=0, canary_key="acme/widgets:abc123")

    assert calls[0]["model"] == settings.review_model
