from fastapi.testclient import TestClient

from src.api import fix_actions as fix_actions_module


class _FakeDecisionLog:
    def __init__(self):
        self.recorded: list[dict] = []

    def record_decision(self, **kwargs):
        self.recorded.append(kwargs)

    def acceptance_rates(self):
        return [
            {"finding_source": "checker_a", "accepted": 2, "total": 3, "acceptance_rate": 0.667}
        ]


def client_with_fake_log():
    fake_log = _FakeDecisionLog()
    fix_actions_module.app.dependency_overrides[fix_actions_module.get_decision_log] = (
        lambda: fake_log
    )
    return TestClient(fix_actions_module.app), fake_log


def make_payload(confidence="medium", **overrides):
    payload = {
        "repo": "acme/widgets",
        "commit_sha": "abc123",
        "file": "app.py",
        "line": 5,
        "finding_source": "division_guard_checker",
        "confidence": confidence,
        "actor": "alice",
    }
    payload.update(overrides)
    return payload


def test_health_endpoint():
    client, _log = client_with_fake_log()
    resp = client.get("/health")
    assert resp.status_code == 200


def test_accept_records_decision():
    client, log = client_with_fake_log()
    resp = client.post("/fix-actions/accept", json=make_payload())

    assert resp.status_code == 200
    assert len(log.recorded) == 1
    assert log.recorded[0]["decision"] == "accepted"
    assert log.recorded[0]["actor"] == "alice"


def test_reject_records_decision():
    client, log = client_with_fake_log()
    resp = client.post("/fix-actions/reject", json=make_payload())

    assert resp.status_code == 200
    assert log.recorded[0]["decision"] == "rejected"
    assert resp.json()["auto_apply_permitted"] is False


def test_accept_high_confidence_permits_auto_apply():
    client, _log = client_with_fake_log()
    resp = client.post("/fix-actions/accept", json=make_payload(confidence="high"))

    assert resp.json()["auto_apply_permitted"] is True


def test_accept_medium_confidence_never_permits_auto_apply():
    client, _log = client_with_fake_log()
    resp = client.post("/fix-actions/accept", json=make_payload(confidence="medium"))

    assert resp.json()["auto_apply_permitted"] is False


def test_accept_low_confidence_never_permits_auto_apply():
    client, _log = client_with_fake_log()
    resp = client.post("/fix-actions/accept", json=make_payload(confidence="low"))

    assert resp.json()["auto_apply_permitted"] is False


def test_reject_never_permits_auto_apply_even_for_high_confidence():
    client, _log = client_with_fake_log()
    resp = client.post("/fix-actions/reject", json=make_payload(confidence="high"))

    assert resp.json()["auto_apply_permitted"] is False


def test_acceptance_rates_endpoint():
    client, _log = client_with_fake_log()
    resp = client.get("/fix-actions/acceptance-rates")

    assert resp.status_code == 200
    assert resp.json()["rates"][0]["finding_source"] == "checker_a"


def test_invalid_confidence_value_is_rejected():
    client, _log = client_with_fake_log()
    resp = client.post("/fix-actions/accept", json=make_payload(confidence="super-high"))

    assert resp.status_code == 422
