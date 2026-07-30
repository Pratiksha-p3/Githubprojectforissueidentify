import json

from src.agents import _llm_finding_agent, security_agent


def test_wires_security_as_the_valid_category():
    assert security_agent._VALID_CATEGORIES == {"security"}


def test_get_security_findings_calls_run_finding_agent_with_correct_source_name(monkeypatch):
    captured = {}

    def fake_run_finding_agent(code, filename, **kwargs):
        captured.update(kwargs)
        return [], True

    monkeypatch.setattr(security_agent, "run_finding_agent", fake_run_finding_agent)

    security_agent.get_security_findings("code", "app.py")

    assert captured["source_name"] == "security_agent"
    assert captured["valid_categories"] == {"security"}


def test_end_to_end_parses_a_real_security_finding(monkeypatch):
    response = json.dumps(
        {
            "findings": [
                {
                    "line": 4,
                    "category": "security",
                    "severity": "critical",
                    "message": "SQL injection via string formatting",
                }
            ]
        }
    )
    monkeypatch.setattr(_llm_finding_agent, "call_llm", lambda **kwargs: response)

    findings = security_agent.get_security_findings("code", "app.py")

    assert len(findings) == 1
    assert findings[0].source == "security_agent"
    assert findings[0].category == "security"


def test_non_security_category_is_dropped(monkeypatch):
    response = json.dumps(
        {"findings": [{"line": 1, "category": "style", "severity": "info", "message": "x"}]}
    )
    monkeypatch.setattr(_llm_finding_agent, "call_llm", lambda **kwargs: response)

    assert security_agent.get_security_findings("code", "app.py") == []
