import json

from src.cli import review as review_cli
from src.core import orchestrator


def test_review_file_returns_zero_for_clean_approvable_code(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        orchestrator, "get_llm_findings_with_status", lambda code, filename, **_kw: ([], True)
    )
    fixture = tmp_path / "clean.py"
    fixture.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")

    exit_code = review_cli.review_file(str(fixture))

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "APPROVE" in out


def test_review_file_returns_nonzero_when_blocked(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        orchestrator, "get_llm_findings_with_status", lambda code, filename, **_kw: ([], True)
    )
    fixture = tmp_path / "buggy.py"
    fixture.write_text(
        "class Student:\n"
        "    def __init__(self, name, age):\n"
        "        self.name = name\n"
        "\n"
        "    def display(self):\n"
        "        print(self.age)\n",
        encoding="utf-8",
    )

    exit_code = review_cli.review_file(str(fixture))

    assert exit_code == 1
    out = capsys.readouterr().out
    assert "BLOCK" in out


def test_review_file_returns_nonzero_for_missing_file():
    assert review_cli.review_file("does_not_exist.py") == 1


def test_review_file_json_output_contains_gate_decision(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        orchestrator, "get_llm_findings_with_status", lambda code, filename, **_kw: ([], True)
    )
    fixture = tmp_path / "clean.py"
    fixture.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")

    review_cli.review_file(str(fixture), as_json=True)

    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["gate_decision"] == "approve"
    assert payload["status"] == "completed"


def test_review_file_reports_review_required_when_llm_fails(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        orchestrator, "get_llm_findings_with_status", lambda code, filename, **_kw: ([], False)
    )
    fixture = tmp_path / "clean.py"
    fixture.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")

    exit_code = review_cli.review_file(str(fixture))

    out = capsys.readouterr().out
    assert "REVIEW_REQUIRED" in out
    assert exit_code == 0  # not BLOCK -- exit code only signals BLOCK specifically
