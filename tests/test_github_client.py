import pytest
import requests as requests_lib

from src.core.config import settings
from src.core.pr_gate import GateDecision
from src.integrations import github_client as gc


class _FakeResponse:
    def __init__(self, status_code: int = 200, json_data: dict | None = None):
        self.status_code = status_code
        self._json_data = json_data if json_data is not None else {}
        self.content = b"{}"

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests_lib.exceptions.HTTPError(f"{self.status_code} error")

    def json(self) -> dict:
        return self._json_data


@pytest.fixture(autouse=True)
def _configured_token(monkeypatch):
    monkeypatch.setattr(settings, "github_token", "test-token")


def test_requires_token_when_none_configured(monkeypatch):
    monkeypatch.setattr(settings, "github_token", "")
    with pytest.raises(RuntimeError, match="GITHUB_TOKEN"):
        gc.GitHubClient()


def test_post_issue_comment_sends_correct_request(monkeypatch):
    calls = []

    def fake_request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        return _FakeResponse(json_data={"id": 42})

    monkeypatch.setattr(gc.requests, "request", fake_request)

    client = gc.GitHubClient()
    result = client.post_issue_comment("acme/widgets", 7, "hello")

    assert result == {"id": 42}
    method, url, kwargs = calls[0]
    assert method == "POST"
    assert url == "https://api.github.com/repos/acme/widgets/issues/7/comments"
    assert kwargs["json"] == {"body": "hello"}
    assert kwargs["headers"]["Authorization"] == "Bearer test-token"


def test_update_issue_comment_sends_patch(monkeypatch):
    calls = []

    def fake_request(method, url, **kwargs):
        calls.append((method, url))
        return _FakeResponse(json_data={"id": 42})

    monkeypatch.setattr(gc.requests, "request", fake_request)

    client = gc.GitHubClient()
    client.update_issue_comment("acme/widgets", 42, "updated body")

    assert calls[0] == ("PATCH", "https://api.github.com/repos/acme/widgets/issues/comments/42")


@pytest.mark.parametrize(
    ("decision", "expected_conclusion"),
    [
        (GateDecision.APPROVE, "success"),
        (GateDecision.BLOCK, "failure"),
        (GateDecision.REVIEW_REQUIRED, "action_required"),
    ],
)
def test_check_run_conclusion_never_shows_success_for_an_incomplete_review(
    monkeypatch, decision, expected_conclusion
):
    captured = {}

    def fake_request(method, url, **kwargs):
        captured["json"] = kwargs["json"]
        return _FakeResponse(json_data={"id": 1})

    monkeypatch.setattr(gc.requests, "request", fake_request)

    client = gc.GitHubClient()
    client.create_check_run("acme/widgets", "abc123", decision=decision, summary="test")

    assert captured["json"]["conclusion"] == expected_conclusion


def test_auth_error_is_not_retried(monkeypatch):
    calls = []

    def fake_request(method, url, **kwargs):
        calls.append(1)
        return _FakeResponse(status_code=401)

    monkeypatch.setattr(gc.requests, "request", fake_request)

    client = gc.GitHubClient()
    with pytest.raises(requests_lib.exceptions.HTTPError):
        client.post_issue_comment("acme/widgets", 7, "hello")

    assert len(calls) == 1  # non-retryable — no wasted attempts


def test_get_pull_request_fetches_pr_metadata(monkeypatch):
    calls = []

    def fake_request(method, url, **kwargs):
        calls.append((method, url))
        return _FakeResponse(json_data={"number": 4, "head": {"sha": "abc123"}})

    monkeypatch.setattr(gc.requests, "request", fake_request)

    client = gc.GitHubClient()
    result = client.get_pull_request("acme/widgets", 4)

    assert result["head"]["sha"] == "abc123"
    assert calls[0] == ("GET", "https://api.github.com/repos/acme/widgets/pulls/4")


def test_list_pr_files_returns_a_list_not_a_dict(monkeypatch):
    def fake_request(method, url, **kwargs):
        return _FakeResponse(json_data=[{"filename": "app.py", "status": "modified"}])

    monkeypatch.setattr(gc.requests, "request", fake_request)

    client = gc.GitHubClient()
    result = client.list_pr_files("acme/widgets", 4)

    assert result == [{"filename": "app.py", "status": "modified"}]


def test_get_file_content_decodes_base64(monkeypatch):
    import base64 as b64

    encoded = b64.b64encode(b"x = 1\n").decode("ascii")
    calls = []

    def fake_request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        return _FakeResponse(json_data={"content": encoded})

    monkeypatch.setattr(gc.requests, "request", fake_request)

    client = gc.GitHubClient()
    content = client.get_file_content("acme/widgets", "app.py", ref="abc123")

    assert content == "x = 1\n"
    method, url, kwargs = calls[0]
    assert url == "https://api.github.com/repos/acme/widgets/contents/app.py"
    assert kwargs["params"] == {"ref": "abc123"}


def test_is_retryable_matches_transient_errors_only():
    assert gc._is_retryable(Exception("429 too many requests")) is True
    assert gc._is_retryable(Exception("503 Service Unavailable")) is True
    assert gc._is_retryable(Exception("Connection reset")) is True
    assert gc._is_retryable(Exception("401 Unauthorized")) is False
    assert gc._is_retryable(Exception("404 Not Found")) is False
