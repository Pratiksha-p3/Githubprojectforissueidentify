from src.core.models import Finding, ReviewResult, ReviewStatus, Severity
from src.notifications import notifier as notifier_module
from src.notifications.notifier import Notifier, should_notify


def make_result(status=ReviewStatus.COMPLETED, findings=None) -> ReviewResult:
    return ReviewResult(
        repo="acme/widgets", commit_sha="abc123def456", status=status, findings=findings or []
    )


def make_critical_finding() -> Finding:
    return Finding(
        file="app.py", line=1, category="runtime", severity=Severity.CRITICAL, message="boom"
    )


# ── should_notify gating ──────────────────────────────────────────────


def test_degraded_status_always_notifies_even_with_zero_findings():
    assert should_notify(make_result(status=ReviewStatus.DEGRADED)) is True


def test_failed_status_always_notifies_even_with_zero_findings():
    assert should_notify(make_result(status=ReviewStatus.FAILED)) is True


def test_completed_with_critical_finding_notifies():
    assert should_notify(make_result(findings=[make_critical_finding()])) is True


def test_completed_with_no_critical_finding_does_not_notify():
    assert should_notify(make_result()) is False


# ── Notifier channel behavior ─────────────────────────────────────────


def test_no_channels_configured_does_not_call_requests(monkeypatch):
    calls = []
    monkeypatch.setattr(notifier_module.requests, "post", lambda *a, **k: calls.append(1))

    notifier = Notifier()
    result = notifier.notify(make_result(status=ReviewStatus.DEGRADED))

    assert result["notified"] is False
    assert calls == []


def test_insignificant_result_never_calls_requests_even_with_channels_configured(monkeypatch):
    calls = []
    monkeypatch.setattr(notifier_module.requests, "post", lambda *a, **k: calls.append(1))

    notifier = Notifier(slack_webhook_url="https://hooks.slack.com/x")
    result = notifier.notify(make_result())  # completed, no critical findings

    assert result["notified"] is False
    assert calls == []


class _FakeResponse:
    def raise_for_status(self) -> None:
        pass


def test_slack_channel_is_called_when_configured(monkeypatch):
    calls = []

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append((url, json))
        return _FakeResponse()

    monkeypatch.setattr(notifier_module.requests, "post", fake_post)

    notifier = Notifier(slack_webhook_url="https://hooks.slack.com/x")
    result = notifier.notify(make_result(findings=[make_critical_finding()]))

    assert result["notified"] is True
    assert result["channels"]["slack"] is True
    assert calls[0][0] == "https://hooks.slack.com/x"
    assert "blocks" in calls[0][1]


def test_teams_channel_is_called_when_configured(monkeypatch):
    calls = []

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append((url, json))
        return _FakeResponse()

    monkeypatch.setattr(notifier_module.requests, "post", fake_post)

    notifier = Notifier(teams_webhook_url="https://outlook.office.com/webhook/x")
    result = notifier.notify(make_result(status=ReviewStatus.DEGRADED))

    assert result["channels"]["teams"] is True
    assert calls[0][1]["@type"] == "MessageCard"


def test_jira_ticket_created_when_all_three_fields_configured(monkeypatch):
    calls = []

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append((url, json, headers))
        return _FakeResponse()

    monkeypatch.setattr(notifier_module.requests, "post", fake_post)

    notifier = Notifier(
        jira_base_url="https://acme.atlassian.net",
        jira_api_token="tok",
        jira_project_key="SEC",
    )
    result = notifier.notify(make_result(findings=[make_critical_finding()]))

    assert result["channels"]["jira"] is True
    url, payload, headers = calls[0]
    assert url == "https://acme.atlassian.net/rest/api/2/issue"
    assert payload["fields"]["project"]["key"] == "SEC"
    assert headers["Authorization"] == "Bearer tok"


def test_jira_not_attempted_when_only_some_fields_configured(monkeypatch):
    calls = []
    monkeypatch.setattr(notifier_module.requests, "post", lambda *a, **k: calls.append(1))

    notifier = Notifier(jira_base_url="https://acme.atlassian.net")  # missing token/project
    result = notifier.notify(make_result(findings=[make_critical_finding()]))

    assert "jira" not in result.get("channels", {})
    assert calls == []


def test_one_channel_failing_does_not_block_another_from_being_tried(monkeypatch):
    def fake_post(url, json=None, headers=None, timeout=None):
        if "slack" in url:
            raise ConnectionError("slack is down")
        return _FakeResponse()

    monkeypatch.setattr(notifier_module.requests, "post", fake_post)

    notifier = Notifier(
        slack_webhook_url="https://hooks.slack.com/x",
        teams_webhook_url="https://outlook.office.com/webhook/x",
    )
    result = notifier.notify(make_result(findings=[make_critical_finding()]))

    assert result["channels"]["slack"] is False
    assert result["channels"]["teams"] is True


def test_slack_payload_includes_critical_findings():
    payload = notifier_module._build_slack_payload(make_result(findings=[make_critical_finding()]))
    text = payload["blocks"][-1]["text"]["text"]
    assert "app.py:1" in text
    assert "boom" in text
