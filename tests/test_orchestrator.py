from src.core import orchestrator
from src.core.models import ReviewStatus


def test_completed_status_when_llm_succeeds_with_no_findings(monkeypatch):
    monkeypatch.setattr(
        orchestrator, "get_llm_findings_with_status", lambda code, filename: ([], True)
    )
    result = orchestrator.review_code(
        "def add(a, b):\n    return a + b\n",
        "app.py",
        repo="acme/widgets",
        commit_sha="abc123",
    )
    assert result.status == ReviewStatus.COMPLETED
    assert result.is_approvable is True


def test_degraded_status_when_llm_fails(monkeypatch):
    monkeypatch.setattr(
        orchestrator, "get_llm_findings_with_status", lambda code, filename: ([], False)
    )
    result = orchestrator.review_code(
        "def add(a, b):\n    return a + b\n",
        "app.py",
        repo="acme/widgets",
        commit_sha="abc123",
    )
    assert result.status == ReviewStatus.DEGRADED
    assert result.is_approvable is False


def test_failed_status_on_syntax_error():
    result = orchestrator.review_code(
        "def broken(:\n    pass\n",
        "app.py",
        repo="acme/widgets",
        commit_sha="abc123",
        include_llm=False,
    )
    assert result.status == ReviewStatus.FAILED
    assert result.is_approvable is False
    assert result.critical_count == 1


def test_deterministic_findings_included_regardless_of_llm_status(monkeypatch):
    monkeypatch.setattr(
        orchestrator, "get_llm_findings_with_status", lambda code, filename: ([], False)
    )
    code = "def divide(a, b):\n    return a / b\n"
    result = orchestrator.review_code(
        code, "app.py", repo="acme/widgets", commit_sha="abc123"
    )
    assert any(f.source == "division_guard_checker" for f in result.findings)
    assert result.status == ReviewStatus.DEGRADED


def test_include_llm_false_never_calls_llm(monkeypatch):
    calls = []
    monkeypatch.setattr(
        orchestrator,
        "get_llm_findings_with_status",
        lambda code, filename: (calls.append(1), ([], True))[1],
    )
    result = orchestrator.review_code(
        "def add(a, b):\n    return a + b\n",
        "app.py",
        repo="acme/widgets",
        commit_sha="abc123",
        include_llm=False,
    )
    assert calls == []
    assert result.status == ReviewStatus.COMPLETED


def test_use_multi_agent_calls_coordinator_instead_of_single_agent(monkeypatch):
    single_agent_calls = []
    multi_agent_calls = []

    monkeypatch.setattr(
        orchestrator,
        "get_llm_findings_with_status",
        lambda code, filename: (single_agent_calls.append(1), ([], True))[1],
    )
    monkeypatch.setattr(
        orchestrator,
        "run_all_agents",
        lambda code, filename: (multi_agent_calls.append(1), ([], True))[1],
    )

    orchestrator.review_code(
        "def add(a, b):\n    return a + b\n",
        "app.py",
        repo="acme/widgets",
        commit_sha="abc123",
        use_multi_agent=True,
    )

    assert multi_agent_calls == [1]
    assert single_agent_calls == []


def test_use_multi_agent_false_still_uses_single_agent_by_default(monkeypatch):
    single_agent_calls = []
    multi_agent_calls = []

    monkeypatch.setattr(
        orchestrator,
        "get_llm_findings_with_status",
        lambda code, filename: (single_agent_calls.append(1), ([], True))[1],
    )
    monkeypatch.setattr(
        orchestrator,
        "run_all_agents",
        lambda code, filename: (multi_agent_calls.append(1), ([], True))[1],
    )

    orchestrator.review_code(
        "def add(a, b):\n    return a + b\n",
        "app.py",
        repo="acme/widgets",
        commit_sha="abc123",
    )

    assert single_agent_calls == [1]
    assert multi_agent_calls == []


def test_use_multi_agent_degraded_when_coordinator_reports_failure(monkeypatch):
    monkeypatch.setattr(orchestrator, "run_all_agents", lambda code, filename: ([], False))

    result = orchestrator.review_code(
        "def add(a, b):\n    return a + b\n",
        "app.py",
        repo="acme/widgets",
        commit_sha="abc123",
        use_multi_agent=True,
    )

    assert result.status == ReviewStatus.DEGRADED


def test_review_code_is_idempotent_for_identical_input(monkeypatch):
    monkeypatch.setattr(
        orchestrator, "get_llm_findings_with_status", lambda code, filename: ([], True)
    )
    code = (
        "class Order:\n"
        "    def __init__(self, total):\n"
        "        pass\n"
        "\n"
        "    def show(self):\n"
        "        return self.total\n"
    )

    first = orchestrator.review_code(code, "app.py", repo="acme/widgets", commit_sha="abc123")
    second = orchestrator.review_code(code, "app.py", repo="acme/widgets", commit_sha="abc123")

    assert first.status == second.status
    assert first.findings == second.findings
    assert first.summary == second.summary
