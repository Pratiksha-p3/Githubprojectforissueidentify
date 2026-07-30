import json

from src.agents import _llm_finding_agent, style_agent


def test_wires_style_as_the_valid_category():
    assert style_agent._VALID_CATEGORIES == {"style"}


def test_critical_severity_from_model_is_capped_to_warning(monkeypatch):
    response = json.dumps(
        {
            "findings": [
                {
                    "line": 1,
                    "category": "style",
                    "severity": "critical",  # the model shouldn't say this, but if it does...
                    "message": "confusing variable name",
                }
            ]
        }
    )
    monkeypatch.setattr(_llm_finding_agent, "call_llm", lambda **kwargs: response)

    findings = style_agent.get_style_findings("code", "app.py")

    assert len(findings) == 1
    assert findings[0].severity.value == "warning"  # never critical, regardless of model output


def test_warning_and_info_pass_through_unchanged(monkeypatch):
    response = json.dumps(
        {
            "findings": [
                {"line": 1, "category": "style", "severity": "warning", "message": "a"},
                {"line": 2, "category": "style", "severity": "info", "message": "b"},
            ]
        }
    )
    monkeypatch.setattr(_llm_finding_agent, "call_llm", lambda **kwargs: response)

    findings = style_agent.get_style_findings("code", "app.py")

    severities = {f.severity.value for f in findings}
    assert severities == {"warning", "info"}


def test_source_is_style_agent(monkeypatch):
    response = json.dumps(
        {"findings": [{"line": 1, "category": "style", "severity": "info", "message": "x"}]}
    )
    monkeypatch.setattr(_llm_finding_agent, "call_llm", lambda **kwargs: response)

    findings = style_agent.get_style_findings("code", "app.py")
    assert findings[0].source == "style_agent"


def test_with_status_reports_failure_when_call_raises(monkeypatch):
    def _raise(**kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(_llm_finding_agent, "call_llm", _raise)

    findings, succeeded = style_agent.get_style_findings_with_status("code", "app.py")
    assert findings == []
    assert succeeded is False
