import json
import subprocess

from src.core.models import Severity
from src.tools import semgrep_runner


class _FakeCompletedProcess:
    def __init__(self, stdout: str):
        self.stdout = stdout
        self.returncode = 0


def make_semgrep_json(results: list[dict]) -> str:
    return json.dumps({"results": results})


def test_returns_empty_when_semgrep_not_on_path(monkeypatch):
    monkeypatch.setattr(semgrep_runner.shutil, "which", lambda name: None)
    assert semgrep_runner.scan_file("app.py") == []


def test_parses_a_single_result_into_a_finding(monkeypatch):
    monkeypatch.setattr(semgrep_runner.shutil, "which", lambda name: "/usr/bin/semgrep")
    stdout = make_semgrep_json(
        [
            {
                "path": "app.py",
                "start": {"line": 5},
                "extra": {
                    "severity": "ERROR",
                    "message": "Detected eval() with user input",
                    "lines": "eval(user_input)",
                },
                "check_id": "python.lang.security.eval-detected",
            }
        ]
    )
    monkeypatch.setattr(
        semgrep_runner.subprocess, "run", lambda *a, **k: _FakeCompletedProcess(stdout)
    )

    findings = semgrep_runner.scan_file("app.py")

    assert len(findings) == 1
    assert findings[0].severity == Severity.CRITICAL
    assert findings[0].source == "semgrep"
    assert findings[0].line == 5
    assert "eval" in findings[0].message.lower()


def test_severity_mapping():
    monkeypatch_map = semgrep_runner._SEVERITY_MAP
    assert monkeypatch_map["ERROR"] == Severity.CRITICAL
    assert monkeypatch_map["WARNING"] == Severity.WARNING
    assert monkeypatch_map["INFO"] == Severity.INFO


def test_empty_stdout_returns_empty_list(monkeypatch):
    monkeypatch.setattr(semgrep_runner.shutil, "which", lambda name: "/usr/bin/semgrep")
    monkeypatch.setattr(
        semgrep_runner.subprocess, "run", lambda *a, **k: _FakeCompletedProcess("")
    )
    assert semgrep_runner.scan_file("app.py") == []


def test_invalid_json_returns_empty_list(monkeypatch):
    monkeypatch.setattr(semgrep_runner.shutil, "which", lambda name: "/usr/bin/semgrep")
    monkeypatch.setattr(
        semgrep_runner.subprocess, "run", lambda *a, **k: _FakeCompletedProcess("not json")
    )
    assert semgrep_runner.scan_file("app.py") == []


def test_timeout_returns_empty_list(monkeypatch):
    monkeypatch.setattr(semgrep_runner.shutil, "which", lambda name: "/usr/bin/semgrep")

    def _raise_timeout(*a, **k):
        raise subprocess.TimeoutExpired(cmd="semgrep", timeout=60)

    monkeypatch.setattr(semgrep_runner.subprocess, "run", _raise_timeout)
    assert semgrep_runner.scan_file("app.py") == []


def test_no_results_key_returns_empty_list(monkeypatch):
    monkeypatch.setattr(semgrep_runner.shutil, "which", lambda name: "/usr/bin/semgrep")
    monkeypatch.setattr(
        semgrep_runner.subprocess, "run", lambda *a, **k: _FakeCompletedProcess("{}")
    )
    assert semgrep_runner.scan_file("app.py") == []


def test_requires_login_placeholder_is_not_passed_through_as_bad_code(monkeypatch):
    """Real behavior confirmed against a live, unauthenticated
    `semgrep --config auto` run: semgrep puts the literal string
    "requires login" into extra.lines for registry rules gated behind a
    login, instead of the actual matched code. Passing that through as
    bad_code would make it look like a fabricated LLM claim and get the
    finding silently discarded by grounding — bad_code must be emptied
    instead, which correctly makes the finding ungrounded-neutral (no
    claim about the code) rather than ungrounded-false (a claim that's
    wrong)."""
    monkeypatch.setattr(semgrep_runner.shutil, "which", lambda name: "/usr/bin/semgrep")
    stdout = make_semgrep_json(
        [
            {
                "path": "app.py",
                "start": {"line": 7},
                "extra": {
                    "severity": "WARNING",
                    "message": "Detected the use of eval().",
                    "lines": "requires login",
                },
                "check_id": "python.lang.security.audit.eval-detected.eval-detected",
            }
        ]
    )
    monkeypatch.setattr(
        semgrep_runner.subprocess, "run", lambda *a, **k: _FakeCompletedProcess(stdout)
    )

    findings = semgrep_runner.scan_file("app.py")

    assert len(findings) == 1
    assert findings[0].bad_code == ""
