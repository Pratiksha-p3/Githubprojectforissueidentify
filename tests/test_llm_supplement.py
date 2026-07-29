import json

from src.agents import llm_supplement
from src.core.models import ConfidenceTier


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
    monkeypatch.setattr(llm_supplement, "call_llm", lambda **kwargs: response)

    findings = llm_supplement.get_llm_findings("code here", "app.py")

    assert len(findings) == 1
    assert findings[0].line == 2
    assert findings[0].confidence == ConfidenceTier.LOW
    assert findings[0].source == "llm_supplement"


def test_strips_markdown_fences_before_parsing(monkeypatch):
    response = '```json\n{"findings": []}\n```'
    monkeypatch.setattr(llm_supplement, "call_llm", lambda **kwargs: response)

    assert llm_supplement.get_llm_findings("code", "app.py") == []


def test_malformed_json_returns_empty_list(monkeypatch):
    monkeypatch.setattr(llm_supplement, "call_llm", lambda **kwargs: "not json at all")

    assert llm_supplement.get_llm_findings("code", "app.py") == []


def test_llm_call_exception_returns_empty_list_not_raise(monkeypatch):
    def _raise(**kwargs):
        raise RuntimeError("rate limited")

    monkeypatch.setattr(llm_supplement, "call_llm", _raise)

    assert llm_supplement.get_llm_findings("code", "app.py") == []


def test_invalid_category_is_dropped(monkeypatch):
    response = json.dumps(
        {
            "findings": [
                {"line": 1, "category": "style", "severity": "info", "message": "nitpick"},
            ]
        }
    )
    monkeypatch.setattr(llm_supplement, "call_llm", lambda **kwargs: response)

    assert llm_supplement.get_llm_findings("code", "app.py") == []


def test_empty_code_short_circuits_without_calling_llm(monkeypatch):
    calls = []
    monkeypatch.setattr(llm_supplement, "call_llm", lambda **kwargs: calls.append(1))

    assert llm_supplement.get_llm_findings("   \n  ", "app.py") == []
    assert calls == []
