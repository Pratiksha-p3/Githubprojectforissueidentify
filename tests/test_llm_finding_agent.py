import json

from src.agents import _llm_finding_agent as agent_module
from src.agents._llm_finding_agent import run_finding_agent
from src.core.models import ConfidenceTier


def run(code="code here", filename="app.py", **kwargs):
    return run_finding_agent(
        code,
        filename,
        system_prompt="system",
        task_prompt="task",
        valid_categories={"logic", "runtime"},
        source_name="test_agent",
        **kwargs,
    )


def test_parses_valid_llm_response_into_findings(monkeypatch):
    response = json.dumps(
        {
            "findings": [
                {
                    "line": 2,
                    "category": "logic",
                    "severity": "warning",
                    "message": "inverted condition",
                    "bad_code": "if x > 0: return False",
                    "fix": "if x <= 0: return False",
                }
            ]
        }
    )
    monkeypatch.setattr(agent_module, "call_llm", lambda **kwargs: response)

    findings, succeeded = run()

    assert succeeded is True
    assert len(findings) == 1
    assert findings[0].line == 2
    assert findings[0].confidence == ConfidenceTier.LOW
    assert findings[0].source == "test_agent"


def test_strips_markdown_fences_before_parsing(monkeypatch):
    response = '```json\n{"findings": []}\n```'
    monkeypatch.setattr(agent_module, "call_llm", lambda **kwargs: response)

    findings, succeeded = run()
    assert findings == []
    assert succeeded is True


def test_malformed_json_reports_failure(monkeypatch):
    monkeypatch.setattr(agent_module, "call_llm", lambda **kwargs: "not json at all")

    findings, succeeded = run()
    assert findings == []
    assert succeeded is False


def test_llm_call_exception_reports_failure_not_raise(monkeypatch):
    def _raise(**kwargs):
        raise RuntimeError("rate limited")

    monkeypatch.setattr(agent_module, "call_llm", _raise)

    findings, succeeded = run()
    assert findings == []
    assert succeeded is False


def test_invalid_category_is_dropped(monkeypatch):
    response = json.dumps(
        {
            "findings": [
                {"line": 1, "category": "style", "severity": "info", "message": "nitpick"},
            ]
        }
    )
    monkeypatch.setattr(agent_module, "call_llm", lambda **kwargs: response)

    findings, succeeded = run()
    assert findings == []
    assert succeeded is True  # the call itself succeeded; the finding was just invalid


def test_empty_code_short_circuits_without_calling_llm(monkeypatch):
    calls = []
    monkeypatch.setattr(agent_module, "call_llm", lambda **kwargs: calls.append(1))

    findings, succeeded = run(code="   \n  ")
    assert findings == []
    assert succeeded is True
    assert calls == []


def test_context_is_included_in_the_prompt_sent_to_the_llm(monkeypatch):
    captured = {}

    def fake_call_llm(**kwargs):
        captured["user"] = kwargs["user"]
        return json.dumps({"findings": []})

    monkeypatch.setattr(agent_module, "call_llm", fake_call_llm)

    run(context="--- Context 1 | other.py ---\ndef helper(): pass")

    assert "SIMILAR CODE ELSEWHERE IN THE REPO" in captured["user"]
    assert "def helper(): pass" in captured["user"]


def test_no_context_section_when_context_is_empty(monkeypatch):
    captured = {}

    def fake_call_llm(**kwargs):
        captured["user"] = kwargs["user"]
        return json.dumps({"findings": []})

    monkeypatch.setattr(agent_module, "call_llm", fake_call_llm)

    run()

    assert "SIMILAR CODE ELSEWHERE IN THE REPO" not in captured["user"]


def test_missing_line_number_is_dropped(monkeypatch):
    response = json.dumps({"findings": [{"category": "logic", "message": "no line given"}]})
    monkeypatch.setattr(agent_module, "call_llm", lambda **kwargs: response)

    findings, succeeded = run()
    assert findings == []
    assert succeeded is True


def test_missing_message_is_dropped(monkeypatch):
    response = json.dumps({"findings": [{"line": 1, "category": "logic"}]})
    monkeypatch.setattr(agent_module, "call_llm", lambda **kwargs: response)

    findings, _succeeded = run()
    assert findings == []


def test_invalid_severity_falls_back_to_warning(monkeypatch):
    response = json.dumps(
        {"findings": [{"line": 1, "category": "logic", "severity": "extreme", "message": "x"}]}
    )
    monkeypatch.setattr(agent_module, "call_llm", lambda **kwargs: response)

    findings, _succeeded = run()
    assert findings[0].severity.value == "warning"
