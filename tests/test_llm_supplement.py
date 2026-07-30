import json

from src.agents import _llm_finding_agent, llm_supplement


def test_wires_runtime_and_logic_as_the_valid_categories():
    assert llm_supplement._VALID_CATEGORIES == {"runtime", "logic"}


def test_get_llm_findings_calls_run_finding_agent_with_correct_source_name(monkeypatch):
    captured = {}

    def fake_run_finding_agent(code, filename, **kwargs):
        captured.update(kwargs)
        return [], True

    monkeypatch.setattr(llm_supplement, "run_finding_agent", fake_run_finding_agent)

    llm_supplement.get_llm_findings("code", "app.py")

    assert captured["source_name"] == "llm_supplement"
    assert captured["valid_categories"] == {"runtime", "logic"}


def test_end_to_end_parses_a_real_response_through_the_shared_agent(monkeypatch):
    response = json.dumps(
        {
            "findings": [
                {
                    "line": 3,
                    "category": "runtime",
                    "severity": "critical",
                    "message": "division by zero",
                }
            ]
        }
    )
    monkeypatch.setattr(_llm_finding_agent, "call_llm", lambda **kwargs: response)

    findings = llm_supplement.get_llm_findings("code", "app.py")

    assert len(findings) == 1
    assert findings[0].source == "llm_supplement"
    assert findings[0].line == 3


def test_end_to_end_with_status_reports_failure_when_call_raises(monkeypatch):
    def _raise(**kwargs):
        raise RuntimeError("rate limited")

    monkeypatch.setattr(_llm_finding_agent, "call_llm", _raise)

    findings, succeeded = llm_supplement.get_llm_findings_with_status("code", "app.py")

    assert findings == []
    assert succeeded is False
