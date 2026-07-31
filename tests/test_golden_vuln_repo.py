"""
tests/test_golden_vuln_repo.py

Integration/regression suite: runs the REAL deterministic checker
pipeline (src.analyzers.registry) against a fixed set of golden
vulnerable-code fixtures (tests/golden_vuln_repo/), each with exactly
one known, deliberately-planted bug. This is the project plan's
"regression detection" mechanism — if a future change to a checker
stops catching one of these, this test fails loudly. The `_obfuscated`
variants double as the "adversarial" suite: different identifiers and
extra indirection than the minimal fixture, confirming checkers catch
the underlying pattern rather than a literal string match.
"""
from __future__ import annotations

from pathlib import Path

from src.analyzers.registry import CHECKERS, run_deterministic_checkers

_FIXTURE_DIR = Path(__file__).parent / "golden_vuln_repo"


def _run(filename: str) -> list:
    path = _FIXTURE_DIR / filename
    code = path.read_text(encoding="utf-8")
    return run_deterministic_checkers(code, filename)


def test_dict_key_bug_is_caught():
    findings = _run("dict_key_bug.py")
    assert any(f.source == "dict_key_checker" for f in findings)


def test_division_bug_is_caught():
    findings = _run("division_bug.py")
    assert any(f.source == "division_guard_checker" for f in findings)


def test_file_exists_bug_is_caught():
    findings = _run("file_exists_bug.py")
    assert any(f.source == "file_exists_checker" for f in findings)


def test_constructor_param_bug_is_caught():
    findings = _run("constructor_param_bug.py")
    assert any(f.source == "unstored_constructor_param_checker" for f in findings)


def test_http_timeout_bug_is_caught():
    findings = _run("http_timeout_bug.py")
    assert any(f.source == "http_timeout_checker" for f in findings)


def test_hardcoded_secret_bug_is_caught():
    findings = _run("hardcoded_secret_bug.py")
    assert any(f.source == "hardcoded_secret_checker" for f in findings)


def test_sql_injection_bug_is_caught():
    findings = _run("sql_injection_bug.py")
    assert any(f.source == "sql_injection_checker" for f in findings)


def test_obfuscated_dict_key_bug_is_still_caught():
    findings = _run("dict_key_bug_obfuscated.py")
    assert any(f.source == "dict_key_checker" for f in findings)


def test_obfuscated_constructor_param_bug_is_still_caught_for_both_params():
    findings = _run("constructor_param_bug_obfuscated.py")
    constructor_findings = [
        f for f in findings if f.source == "unstored_constructor_param_checker"
    ]
    assert len(constructor_findings) >= 1
    combined_message = " ".join(f.message for f in constructor_findings)
    assert "currency" in combined_message
    assert "tax_rate" in combined_message


def test_clean_code_produces_no_findings():
    """Negative control — every checker must report zero findings on
    genuinely clean code. A checker firing here is a false positive."""
    assert _run("clean_code.py") == []


def test_every_deterministic_checker_is_exercised_by_at_least_one_fixture():
    """Coverage check on the suite itself — fails if a new checker gets
    added to the registry without a corresponding golden fixture, so
    coverage can't silently rot as the checker set grows."""
    all_findings = []
    for fixture in _FIXTURE_DIR.glob("*bug*.py"):
        all_findings.extend(_run(fixture.name))

    sources_seen = {f.source for f in all_findings}
    expected_sources = {
        "dict_key_checker",
        "division_guard_checker",
        "file_exists_checker",
        "unstored_constructor_param_checker",
        "http_timeout_checker",
        "hardcoded_secret_checker",
        "sql_injection_checker",
    }
    assert expected_sources <= sources_seen
    assert len(CHECKERS) == len(expected_sources), (
        "A checker was added/removed from the registry without updating "
        "this golden-fixture suite to match — add a fixture (and a test "
        "above) for the new checker, then update expected_sources here."
    )
