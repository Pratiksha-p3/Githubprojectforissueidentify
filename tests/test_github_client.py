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
    # Explicitly cleared, not just left to whatever's absent on disk --
    # this used to rely on secrets/github_app.pem not existing to keep
    # GitHubClient on the PAT path, which broke the moment a real App
    # key + real .env values were introduced in this environment (every
    # PAT-focused test below started silently authenticating as the App
    # instead). Tests that specifically want App auth active override
    # these explicitly (see _configure_app_auth()).
    monkeypatch.setattr(settings, "github_app_id", "")
    monkeypatch.setattr(settings, "github_installation_id", "")
    monkeypatch.setattr(settings, "github_app_private_key_path", "")


def test_requires_token_when_none_configured(monkeypatch):
    monkeypatch.setattr(settings, "github_token", "")
    with pytest.raises(RuntimeError, match="GITHUB_TOKEN"):
        gc.GitHubClient()


def _configure_app_auth(monkeypatch, tmp_path):
    key_file = tmp_path / "github_app.pem"
    key_file.write_text("fake-pem-content", encoding="utf-8")
    monkeypatch.setattr(settings, "github_app_id", "12345")
    monkeypatch.setattr(settings, "github_installation_id", "999")
    monkeypatch.setattr(settings, "github_app_private_key_path", str(key_file))
    return key_file


def test_uses_pat_when_app_auth_is_not_configured(monkeypatch):
    calls = []

    def fake_request(method, url, **kwargs):
        calls.append(kwargs["headers"]["Authorization"])
        return _FakeResponse(json_data={"id": 1})

    monkeypatch.setattr(gc.requests, "request", fake_request)

    client = gc.GitHubClient()
    client.post_issue_comment("acme/widgets", 1, "hi")

    assert calls[0] == "Bearer test-token"


def test_uses_app_auth_when_fully_configured(monkeypatch, tmp_path):
    _configure_app_auth(monkeypatch, tmp_path)
    monkeypatch.setattr(
        gc.github_app_auth, "get_installation_token",
        lambda app_id, installation_id, pem: "ghs_installation_token",
    )
    calls = []

    def fake_request(method, url, **kwargs):
        calls.append(kwargs["headers"]["Authorization"])
        return _FakeResponse(json_data={"id": 1})

    monkeypatch.setattr(gc.requests, "request", fake_request)

    client = gc.GitHubClient()
    client.post_issue_comment("acme/widgets", 1, "hi")

    assert calls[0] == "Bearer ghs_installation_token"


def test_explicit_token_takes_priority_over_app_auth(monkeypatch, tmp_path):
    _configure_app_auth(monkeypatch, tmp_path)
    monkeypatch.setattr(
        gc.github_app_auth, "get_installation_token",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not be called")),
    )
    calls = []

    def fake_request(method, url, **kwargs):
        calls.append(kwargs["headers"]["Authorization"])
        return _FakeResponse(json_data={"id": 1})

    monkeypatch.setattr(gc.requests, "request", fake_request)

    client = gc.GitHubClient(token="explicit-token")
    client.post_issue_comment("acme/widgets", 1, "hi")

    assert calls[0] == "Bearer explicit-token"


def test_falls_back_to_pat_when_app_auth_is_only_partially_configured(monkeypatch):
    # app_id and installation_id set, but no private key file -- must not
    # attempt app auth (github_app_auth.get_installation_token is never
    # called, would fail loudly if it were).
    monkeypatch.setattr(settings, "github_app_id", "12345")
    monkeypatch.setattr(settings, "github_installation_id", "999")
    monkeypatch.setattr(settings, "github_app_private_key_path", "does/not/exist.pem")
    calls = []

    def fake_request(method, url, **kwargs):
        calls.append(kwargs["headers"]["Authorization"])
        return _FakeResponse(json_data={"id": 1})

    monkeypatch.setattr(gc.requests, "request", fake_request)

    client = gc.GitHubClient()
    client.post_issue_comment("acme/widgets", 1, "hi")

    assert calls[0] == "Bearer test-token"


def test_constructor_does_not_raise_when_only_app_auth_is_configured(monkeypatch, tmp_path):
    _configure_app_auth(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "github_token", "")

    gc.GitHubClient()  # must not raise


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


def test_list_review_comments_returns_a_list(monkeypatch):
    calls = []

    def fake_request(method, url, **kwargs):
        calls.append((method, url))
        return _FakeResponse(json_data=[{"path": "app.py", "line": 5, "body": "x"}])

    monkeypatch.setattr(gc.requests, "request", fake_request)

    client = gc.GitHubClient()
    result = client.list_review_comments("acme/widgets", 4)

    assert result == [{"path": "app.py", "line": 5, "body": "x"}]
    method, url = calls[0]
    assert method == "GET"
    assert url == "https://api.github.com/repos/acme/widgets/pulls/4/comments"


def test_get_file_content_decodes_base64(monkeypatch):
    import base64 as b64

    encoded = b64.b64encode(b"x = 1\n").decode("ascii")
    calls = []

    def fake_request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        return _FakeResponse(json_data={"content": encoded, "sha": "blobsha1"})

    monkeypatch.setattr(gc.requests, "request", fake_request)

    client = gc.GitHubClient()
    content = client.get_file_content("acme/widgets", "app.py", ref="abc123")

    assert content == "x = 1\n"
    method, url, kwargs = calls[0]
    assert url == "https://api.github.com/repos/acme/widgets/contents/app.py"
    assert kwargs["params"] == {"ref": "abc123"}


def test_get_file_sha_returns_the_blob_sha(monkeypatch):
    import base64 as b64

    encoded = b64.b64encode(b"x = 1\n").decode("ascii")

    def fake_request(method, url, **kwargs):
        return _FakeResponse(json_data={"content": encoded, "sha": "blobsha1"})

    monkeypatch.setattr(gc.requests, "request", fake_request)

    client = gc.GitHubClient()
    sha = client.get_file_sha("acme/widgets", "app.py", ref="abc123")

    assert sha == "blobsha1"


def test_update_file_content_sends_base64_encoded_content(monkeypatch):
    import base64 as b64

    calls = []

    def fake_request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        return _FakeResponse(json_data={"commit": {"sha": "newcommitsha"}})

    monkeypatch.setattr(gc.requests, "request", fake_request)

    client = gc.GitHubClient()
    result = client.update_file_content(
        "acme/widgets", "app.py",
        message="fix it", content="x = 2\n", sha="blobsha1", branch="main",
    )

    assert result == {"commit": {"sha": "newcommitsha"}}
    method, url, kwargs = calls[0]
    assert method == "PUT"
    assert url == "https://api.github.com/repos/acme/widgets/contents/app.py"
    body = kwargs["json"]
    assert body["message"] == "fix it"
    assert body["sha"] == "blobsha1"
    assert body["branch"] == "main"
    assert b64.b64decode(body["content"]).decode("utf-8") == "x = 2\n"


def test_update_file_content_omits_sha_when_creating_a_new_file(monkeypatch):
    """GitHub treats a `sha` on a path with no existing file as an
    error, not "create it" -- omitting `sha` entirely (the default) is
    what makes this method double as file creation, not just updates."""
    calls = []

    def fake_request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        return _FakeResponse(json_data={"commit": {"sha": "newcommitsha"}})

    monkeypatch.setattr(gc.requests, "request", fake_request)

    client = gc.GitHubClient()
    client.update_file_content(
        "acme/widgets", "new_file.py", message="add it", content="x = 1\n", branch="main",
    )

    _method, _url, kwargs = calls[0]
    assert "sha" not in kwargs["json"]


def test_create_review_comment_sends_the_suggestion_to_the_right_line(monkeypatch):
    calls = []

    def fake_request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        return _FakeResponse(json_data={"id": 99})

    monkeypatch.setattr(gc.requests, "request", fake_request)

    client = gc.GitHubClient()
    body = "**[WARNING] bad**\n\n```suggestion\nfixed line\n```"
    result = client.create_review_comment(
        "acme/widgets", 4, commit_id="abc123", path="app.py", line=5, body=body
    )

    assert result == {"id": 99}
    method, url, kwargs = calls[0]
    assert method == "POST"
    assert url == "https://api.github.com/repos/acme/widgets/pulls/4/comments"
    assert kwargs["json"] == {
        "body": body, "commit_id": "abc123", "path": "app.py", "line": 5, "side": "RIGHT",
    }


def test_create_review_comment_with_start_line_sends_a_multi_line_range(monkeypatch):
    """A fix spanning more than one original line (e.g. orchestrator.py's
    block-reindent fix) needs GitHub's multi-line suggestion range --
    start_line/start_side alongside line/side -- or the ```suggestion```
    block would only ever be offered to replace the single last line."""
    calls = []

    def fake_request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        return _FakeResponse(json_data={"id": 99})

    monkeypatch.setattr(gc.requests, "request", fake_request)

    client = gc.GitHubClient()
    body = "**[CRITICAL] bad**\n\n```suggestion\nfixed\nlines\n```"
    client.create_review_comment(
        "acme/widgets", 4, commit_id="abc123", path="app.py", line=4, body=body, start_line=3,
    )

    _method, _url, kwargs = calls[0]
    assert kwargs["json"] == {
        "body": body, "commit_id": "abc123", "path": "app.py", "line": 4, "side": "RIGHT",
        "start_line": 3, "start_side": "RIGHT",
    }


def test_create_review_comment_ignores_start_line_when_not_below_line(monkeypatch):
    """start_line must be strictly less than line for GitHub's API to
    accept it -- a single-line finding's start_line == line (from
    review_pr.py's _span()) must not get sent as a bogus zero-width
    range."""
    calls = []

    def fake_request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        return _FakeResponse(json_data={"id": 99})

    monkeypatch.setattr(gc.requests, "request", fake_request)

    client = gc.GitHubClient()
    client.create_review_comment(
        "acme/widgets", 4, commit_id="abc123", path="app.py", line=5, body="b", start_line=5,
    )

    _method, _url, kwargs = calls[0]
    assert "start_line" not in kwargs["json"]
    assert "start_side" not in kwargs["json"]


def test_is_retryable_matches_transient_errors_only():
    assert gc._is_retryable(Exception("429 too many requests")) is True
    assert gc._is_retryable(Exception("503 Service Unavailable")) is True
    assert gc._is_retryable(Exception("Connection reset")) is True
    assert gc._is_retryable(Exception("401 Unauthorized")) is False
    assert gc._is_retryable(Exception("404 Not Found")) is False
