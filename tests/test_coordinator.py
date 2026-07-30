from src.agents import coordinator
from src.core.models import ConfidenceTier, Finding, Severity


def make_finding(line, category, source, bad_code="") -> Finding:
    return Finding(
        file="app.py",
        line=line,
        category=category,
        severity=Severity.WARNING,
        message="msg",
        bad_code=bad_code,
        confidence=ConfidenceTier.LOW,
        source=source,
    )


def _patch_agents(monkeypatch, results: dict):
    """results maps agent-module-attr-name -> (findings, succeeded)."""
    for name, result in results.items():
        monkeypatch.setattr(coordinator, name, lambda code, filename, context="", _r=result: _r)


def test_merges_findings_from_all_agents(monkeypatch):
    monkeypatch.setattr(
        coordinator.llm_supplement,
        "get_llm_findings_with_status",
        lambda code, filename, context="": ([make_finding(1, "logic", "llm_supplement")], True),
    )
    monkeypatch.setattr(
        coordinator.security_agent,
        "get_security_findings_with_status",
        lambda code, filename, context="": (
            [make_finding(2, "security", "security_agent")],
            True,
        ),
    )
    monkeypatch.setattr(
        coordinator.style_agent,
        "get_style_findings_with_status",
        lambda code, filename, context="": ([make_finding(3, "style", "style_agent")], True),
    )
    monkeypatch.setattr(
        coordinator.test_coverage_agent,
        "get_test_coverage_findings_with_status",
        lambda code, filename, context="": (
            [make_finding(4, "test_coverage", "test_coverage_agent")],
            True,
        ),
    )

    findings, succeeded = coordinator.run_all_agents("x = 1\n", "app.py")

    assert succeeded is True
    sources = {f.source for f in findings}
    assert sources == {"llm_supplement", "security_agent", "style_agent", "test_coverage_agent"}


def test_one_agent_failing_marks_overall_as_not_succeeded(monkeypatch):
    monkeypatch.setattr(
        coordinator.llm_supplement,
        "get_llm_findings_with_status",
        lambda code, filename, context="": ([], False),
    )
    monkeypatch.setattr(
        coordinator.security_agent,
        "get_security_findings_with_status",
        lambda code, filename, context="": ([], True),
    )
    monkeypatch.setattr(
        coordinator.style_agent,
        "get_style_findings_with_status",
        lambda code, filename, context="": ([], True),
    )
    monkeypatch.setattr(
        coordinator.test_coverage_agent,
        "get_test_coverage_findings_with_status",
        lambda code, filename, context="": ([], True),
    )

    _findings, succeeded = coordinator.run_all_agents("x = 1\n", "app.py")

    assert succeeded is False


def test_ungrounded_findings_are_dropped(monkeypatch):
    fabricated = make_finding(1, "logic", "llm_supplement", bad_code="this is not in the file")
    monkeypatch.setattr(
        coordinator.llm_supplement,
        "get_llm_findings_with_status",
        lambda code, filename, context="": ([fabricated], True),
    )
    monkeypatch.setattr(
        coordinator.security_agent,
        "get_security_findings_with_status",
        lambda code, filename, context="": ([], True),
    )
    monkeypatch.setattr(
        coordinator.style_agent,
        "get_style_findings_with_status",
        lambda code, filename, context="": ([], True),
    )
    monkeypatch.setattr(
        coordinator.test_coverage_agent,
        "get_test_coverage_findings_with_status",
        lambda code, filename, context="": ([], True),
    )

    findings, _succeeded = coordinator.run_all_agents("x = 1\n", "app.py")

    assert findings == []


def test_dedupes_same_line_and_category_across_agents(monkeypatch):
    dup_a = make_finding(5, "logic", "llm_supplement")
    dup_b = make_finding(5, "logic", "security_agent")  # same (line, category)

    monkeypatch.setattr(
        coordinator.llm_supplement,
        "get_llm_findings_with_status",
        lambda code, filename, context="": ([dup_a], True),
    )
    monkeypatch.setattr(
        coordinator.security_agent,
        "get_security_findings_with_status",
        lambda code, filename, context="": ([dup_b], True),
    )
    monkeypatch.setattr(
        coordinator.style_agent,
        "get_style_findings_with_status",
        lambda code, filename, context="": ([], True),
    )
    monkeypatch.setattr(
        coordinator.test_coverage_agent,
        "get_test_coverage_findings_with_status",
        lambda code, filename, context="": ([], True),
    )

    findings, _succeeded = coordinator.run_all_agents("x = 1\n", "app.py")

    assert len(findings) == 1
    assert findings[0].source == "llm_supplement"  # first agent in priority order wins
