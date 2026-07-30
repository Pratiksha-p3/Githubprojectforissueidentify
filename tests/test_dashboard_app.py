from fastapi.testclient import TestClient

from src.dashboard import app as dashboard_module


class _FakeStore:
    def __init__(self, history: list[dict]):
        self._history = history

    def get_history(self, repo: str, limit: int = 20) -> list[dict]:
        return self._history[:limit]


def make_row(commit_sha="abc", critical=0, total=0) -> dict:
    return {
        "repo": "acme/widgets",
        "commit_sha": commit_sha,
        "status": "completed",
        "critical_count": critical,
        "total_findings": total,
        "summary": "ok",
        "reviewed_at": "2024-01-01T00:00:00Z",
    }


def client_with_history(history: list[dict]) -> TestClient:
    fake_store = _FakeStore(history)
    dashboard_module.app.dependency_overrides[dashboard_module.get_store] = lambda: fake_store
    return TestClient(dashboard_module.app)


def test_health_endpoint():
    resp = client_with_history([]).get("/health")
    assert resp.status_code == 200


def test_repo_risk_endpoint_returns_score():
    resp = client_with_history([make_row(critical=2, total=2)]).get("/api/repos/acme/widgets/risk")
    assert resp.status_code == 200
    body = resp.json()
    assert body["repo"] == "acme/widgets"
    assert "score" in body
    assert "trend" in body


def test_repo_history_endpoint_returns_rows():
    client = client_with_history([make_row(commit_sha="c1"), make_row(commit_sha="c2")])
    resp = client.get("/api/repos/acme/widgets/history")
    assert resp.status_code == 200
    assert len(resp.json()["history"]) == 2


def test_repo_history_endpoint_respects_limit_query_param():
    client = client_with_history([make_row(commit_sha=f"c{i}") for i in range(5)])
    resp = client.get("/api/repos/acme/widgets/history?limit=2")
    assert len(resp.json()["history"]) == 2


def test_export_json_endpoint():
    client = client_with_history([make_row()])
    resp = client.get("/api/repos/acme/widgets/export/json")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/json")
    assert "risk_score" in resp.json()


def test_export_pdf_endpoint_returns_valid_pdf_bytes():
    client = client_with_history([make_row()])
    resp = client.get("/api/repos/acme/widgets/export/pdf")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.content.startswith(b"%PDF")
