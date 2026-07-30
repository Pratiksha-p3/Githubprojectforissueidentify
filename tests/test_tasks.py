import fakeredis
import pytest

from src.core.config import settings
from src.core.models import ReviewResult, ReviewStatus
from src.storage.idempotency_store import IdempotencyStore
from src.worker import tasks


@pytest.fixture
def fake_idempotency_store(monkeypatch):
    store = IdempotencyStore(client=fakeredis.FakeRedis())
    monkeypatch.setattr(tasks, "IdempotencyStore", lambda: store)
    return store


def test_execute_review_runs_and_marks_processed(monkeypatch, fake_idempotency_store):
    clean_result = ReviewResult(
        repo="acme/widgets", commit_sha="abc123", status=ReviewStatus.COMPLETED, findings=[]
    )
    monkeypatch.setattr(tasks, "review_code", lambda *a, **k: clean_result)

    outcome = tasks._execute_review(
        repo="acme/widgets", commit_sha="abc123", filename="app.py", code="x = 1\n"
    )

    assert outcome["status"] == "completed"
    assert outcome["gate_decision"] == "approve"
    assert fake_idempotency_store.already_processed("acme/widgets", "abc123") is True


def test_execute_review_skips_already_processed_commit(monkeypatch, fake_idempotency_store):
    fake_idempotency_store.mark_processed("acme/widgets", "abc123")

    calls = []
    monkeypatch.setattr(tasks, "review_code", lambda *a, **k: calls.append(1))

    outcome = tasks._execute_review(
        repo="acme/widgets", commit_sha="abc123", filename="app.py", code="x = 1\n"
    )

    assert outcome["status"] == "skipped"
    assert calls == []  # review_code was never called


def test_execute_review_reports_block_decision(monkeypatch, fake_idempotency_store):
    from src.core.models import Finding, Severity

    blocked_result = ReviewResult(
        repo="acme/widgets",
        commit_sha="abc123",
        status=ReviewStatus.COMPLETED,
        findings=[
            Finding(
                file="app.py", line=1, category="runtime",
                severity=Severity.CRITICAL, message="bad",
            )
        ],
    )
    monkeypatch.setattr(tasks, "review_code", lambda *a, **k: blocked_result)

    outcome = tasks._execute_review(
        repo="acme/widgets", commit_sha="abc123", filename="app.py", code="x = 1\n"
    )

    assert outcome["gate_decision"] == "block"
    assert outcome["critical_count"] == 1


def test_on_failure_pushes_to_dlq(monkeypatch):
    pushed = []

    class _FakeDLQStore:
        def push(self, *, repo, commit_sha, error):
            pushed.append({"repo": repo, "commit_sha": commit_sha, "error": error})

    monkeypatch.setattr(tasks, "DLQStore", _FakeDLQStore)

    task_instance = tasks._DLQOnFailureTask()
    task_instance.on_failure(
        RuntimeError("boom"),
        "task-id-123",
        (),
        {"repo": "acme/widgets", "commit_sha": "abc123"},
        None,
    )

    assert len(pushed) == 1
    assert pushed[0]["repo"] == "acme/widgets"
    assert pushed[0]["commit_sha"] == "abc123"
    assert "boom" in pushed[0]["error"]


def test_review_commit_task_is_configured_with_retry_and_backoff():
    assert tasks.review_commit_task.max_retries == 3
    assert tasks.review_commit_task.autoretry_for == (Exception,)
    assert tasks.review_commit_task.retry_backoff is True


def test_publishes_when_pr_number_given_and_token_configured(
    monkeypatch, fake_idempotency_store
):
    monkeypatch.setattr(settings, "github_token", "test-token")
    clean_result = ReviewResult(
        repo="acme/widgets", commit_sha="abc123", status=ReviewStatus.COMPLETED, findings=[]
    )
    monkeypatch.setattr(tasks, "review_code", lambda *a, **k: clean_result)

    publish_calls = []

    def fake_publish(result, pr_number, **_kwargs):
        publish_calls.append((result, pr_number))
        return {"comment_action": "created"}

    monkeypatch.setattr(tasks, "publish_review", fake_publish)

    outcome = tasks._execute_review(
        repo="acme/widgets", commit_sha="abc123", filename="app.py", code="x = 1\n", pr_number=7
    )

    assert len(publish_calls) == 1
    assert publish_calls[0][1] == 7
    assert outcome["publish"]["comment_action"] == "created"


def test_does_not_publish_when_pr_number_is_none(monkeypatch, fake_idempotency_store):
    monkeypatch.setattr(settings, "github_token", "test-token")
    clean_result = ReviewResult(
        repo="acme/widgets", commit_sha="abc123", status=ReviewStatus.COMPLETED, findings=[]
    )
    monkeypatch.setattr(tasks, "review_code", lambda *a, **k: clean_result)

    publish_calls = []
    monkeypatch.setattr(
        tasks, "publish_review", lambda *a, **k: publish_calls.append(1)
    )

    outcome = tasks._execute_review(
        repo="acme/widgets", commit_sha="abc123", filename="app.py", code="x = 1\n"
    )

    assert publish_calls == []
    assert "publish" not in outcome


def test_does_not_publish_when_no_github_token_configured(monkeypatch, fake_idempotency_store):
    monkeypatch.setattr(settings, "github_token", "")
    clean_result = ReviewResult(
        repo="acme/widgets", commit_sha="abc123", status=ReviewStatus.COMPLETED, findings=[]
    )
    monkeypatch.setattr(tasks, "review_code", lambda *a, **k: clean_result)

    publish_calls = []
    monkeypatch.setattr(
        tasks, "publish_review", lambda *a, **k: publish_calls.append(1)
    )

    outcome = tasks._execute_review(
        repo="acme/widgets", commit_sha="abc123", filename="app.py", code="x = 1\n", pr_number=7
    )

    assert publish_calls == []
    assert "publish" not in outcome


def test_notifier_is_always_invoked_with_the_review_result(monkeypatch, fake_idempotency_store):
    clean_result = ReviewResult(
        repo="acme/widgets", commit_sha="abc123", status=ReviewStatus.COMPLETED, findings=[]
    )
    monkeypatch.setattr(tasks, "review_code", lambda *a, **k: clean_result)

    notify_calls = []

    class _FakeNotifier:
        def notify(self, result):
            notify_calls.append(result)
            return {"notified": False, "reason": "not significant enough to alert on"}

    monkeypatch.setattr(tasks, "Notifier", _FakeNotifier)

    outcome = tasks._execute_review(
        repo="acme/widgets", commit_sha="abc123", filename="app.py", code="x = 1\n"
    )

    assert len(notify_calls) == 1
    assert notify_calls[0] is clean_result
    assert outcome["notification"]["notified"] is False


def test_notification_outcome_is_included_when_channels_fire(
    monkeypatch, fake_idempotency_store
):
    from src.core.models import Finding, Severity

    blocked_result = ReviewResult(
        repo="acme/widgets",
        commit_sha="abc123",
        status=ReviewStatus.COMPLETED,
        findings=[
            Finding(
                file="app.py", line=1, category="runtime",
                severity=Severity.CRITICAL, message="bad",
            )
        ],
    )
    monkeypatch.setattr(tasks, "review_code", lambda *a, **k: blocked_result)

    class _FakeNotifier:
        def notify(self, result):
            return {"notified": True, "channels": {"slack": True}}

    monkeypatch.setattr(tasks, "Notifier", _FakeNotifier)

    outcome = tasks._execute_review(
        repo="acme/widgets", commit_sha="abc123", filename="app.py", code="x = 1\n"
    )

    assert outcome["notification"]["notified"] is True
    assert outcome["notification"]["channels"]["slack"] is True
