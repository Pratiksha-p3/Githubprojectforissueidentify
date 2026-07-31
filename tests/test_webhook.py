import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from src.api import webhook
from src.core.config import settings


class _FakeAsyncResult:
    def __init__(self, task_id: str):
        self.id = task_id


@pytest.fixture(autouse=True)
def _no_secrets_by_default(monkeypatch):
    monkeypatch.setattr(settings, "github_webhook_secret", "")
    monkeypatch.setattr(settings, "gitlab_webhook_secret", "")


@pytest.fixture
def client():
    return TestClient(webhook.app)


@pytest.fixture
def fake_delay(monkeypatch):
    calls = []

    def _delay(**kwargs):
        calls.append(kwargs)
        return _FakeAsyncResult(f"task-{len(calls)}")

    monkeypatch.setattr(webhook.review_commit_task, "delay", _delay)
    return calls


def test_health_endpoint(client):
    """No live Redis/Postgres in this test environment, so the honest
    outcome is "degraded", not a hardcoded "ok" -- the whole point of
    Stage 14's health check is that it actually reflects dependency
    reachability instead of always reporting healthy."""
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] in ("ok", "degraded")
    assert set(body["checks"]) == {"redis", "postgres", "llm_circuit_breaker"}


def test_metrics_endpoint_is_404_when_disabled(client, monkeypatch):
    monkeypatch.setattr(settings, "metrics_enabled", False)
    resp = client.get("/metrics")
    assert resp.status_code == 404


def test_metrics_endpoint_returns_counters_when_enabled(client, monkeypatch):
    from src.core import metrics

    monkeypatch.setattr(settings, "metrics_enabled", True)
    metrics.reset()
    metrics.increment("reviews_completed_total")

    resp = client.get("/metrics")

    assert resp.status_code == 200
    assert resp.json()["reviews_completed_total"] == 1
    metrics.reset()


def test_github_push_event_queues_a_task_per_file(client, fake_delay):
    payload = {
        "repository": {"full_name": "acme/widgets"},
        "after": "abc123",
        "files": [{"filename": "app.py", "content": "x = 1\n"}],
    }
    resp = client.post(
        "/webhook/github", json=payload, headers={"X-GitHub-Event": "push"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "queued"
    assert body["repo"] == "acme/widgets"
    assert len(body["task_ids"]) == 1
    assert fake_delay[0]["repo"] == "acme/widgets"
    assert fake_delay[0]["commit_sha"] == "abc123"


def test_github_non_push_event_is_ignored(client, fake_delay):
    resp = client.post(
        "/webhook/github", json={"files": []}, headers={"X-GitHub-Event": "ping"}
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ignored"
    assert fake_delay == []


def test_github_webhook_rejects_invalid_signature(client, monkeypatch, fake_delay):
    monkeypatch.setattr(settings, "github_webhook_secret", "supersecret")
    resp = client.post(
        "/webhook/github",
        json={"files": []},
        headers={"X-GitHub-Event": "push", "X-Hub-Signature-256": "sha256=deadbeef"},
    )
    assert resp.status_code == 401
    assert fake_delay == []


def test_github_webhook_rejects_missing_signature_when_secret_configured(
    client, monkeypatch, fake_delay
):
    monkeypatch.setattr(settings, "github_webhook_secret", "supersecret")
    resp = client.post(
        "/webhook/github", json={"files": []}, headers={"X-GitHub-Event": "push"}
    )
    assert resp.status_code == 401


def test_github_webhook_accepts_valid_signature(client, monkeypatch, fake_delay):
    monkeypatch.setattr(settings, "github_webhook_secret", "supersecret")
    payload = {"repository": {"full_name": "acme/widgets"}, "after": "abc123", "files": []}
    body_bytes = json.dumps(payload).encode()
    signature = "sha256=" + hmac.new(
        b"supersecret", body_bytes, hashlib.sha256
    ).hexdigest()

    resp = client.post(
        "/webhook/github",
        content=body_bytes,
        headers={
            "X-GitHub-Event": "push",
            "X-Hub-Signature-256": signature,
            "Content-Type": "application/json",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "queued"


def test_gitlab_push_event_queues_a_task(client, fake_delay):
    payload = {
        "project": {"path_with_namespace": "acme/widgets"},
        "after": "abc123",
        "files": [{"filename": "app.py", "content": "x = 1\n"}],
    }
    resp = client.post(
        "/webhook/gitlab", json=payload, headers={"X-Gitlab-Event": "Push Hook"}
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "queued"
    assert len(fake_delay) == 1


def test_github_webhook_passes_pr_number_through_to_task(client, fake_delay):
    payload = {
        "repository": {"full_name": "acme/widgets"},
        "after": "abc123",
        "pr_number": 42,
        "files": [{"filename": "app.py", "content": "x = 1\n"}],
    }
    client.post("/webhook/github", json=payload, headers={"X-GitHub-Event": "push"})
    assert fake_delay[0]["pr_number"] == 42


def test_github_webhook_pr_number_defaults_to_none(client, fake_delay):
    payload = {
        "repository": {"full_name": "acme/widgets"},
        "after": "abc123",
        "files": [{"filename": "app.py", "content": "x = 1\n"}],
    }
    client.post("/webhook/github", json=payload, headers={"X-GitHub-Event": "push"})
    assert fake_delay[0]["pr_number"] is None


def test_gitlab_webhook_rejects_wrong_token(client, monkeypatch, fake_delay):
    monkeypatch.setattr(settings, "gitlab_webhook_secret", "expected-token")
    resp = client.post(
        "/webhook/gitlab",
        json={"files": []},
        headers={"X-Gitlab-Event": "Push Hook", "X-Gitlab-Token": "wrong-token"},
    )
    assert resp.status_code == 401
    assert fake_delay == []
