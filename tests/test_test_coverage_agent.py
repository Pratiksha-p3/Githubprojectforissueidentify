import json

from src.agents import _llm_finding_agent, test_coverage_agent


def test_wires_test_coverage_as_the_valid_category():
    assert test_coverage_agent._VALID_CATEGORIES == {"test_coverage"}


def test_any_severity_from_model_is_capped_to_info(monkeypatch):
    response = json.dumps(
        {
            "findings": [
                {
                    "line": 1,
                    "category": "test_coverage",
                    "severity": "critical",
                    "message": "no test for empty-list input",
                }
            ]
        }
    )
    monkeypatch.setattr(_llm_finding_agent, "call_llm", lambda **kwargs: response)

    findings = test_coverage_agent.get_test_coverage_findings("code", "app.py")

    assert len(findings) == 1
    assert findings[0].severity.value == "info"


def test_source_is_test_coverage_agent(monkeypatch):
    response = json.dumps(
        {
            "findings": [
                {"line": 1, "category": "test_coverage", "severity": "info", "message": "x"}
            ]
        }
    )
    monkeypatch.setattr(_llm_finding_agent, "call_llm", lambda **kwargs: response)

    findings = test_coverage_agent.get_test_coverage_findings("code", "app.py")
    assert findings[0].source == "test_coverage_agent"


def test_with_status_reports_failure_when_call_raises(monkeypatch):
    def _raise(**kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(_llm_finding_agent, "call_llm", _raise)

    findings, succeeded = test_coverage_agent.get_test_coverage_findings_with_status(
        "code", "app.py"
    )
    assert findings == []
    assert succeeded is False
