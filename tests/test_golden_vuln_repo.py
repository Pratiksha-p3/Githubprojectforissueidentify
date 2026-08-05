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


def test_unsafe_yaml_bug_is_caught():
    findings = _run("unsafe_yaml_bug.py")
    assert any(f.source == "unsafe_yaml_checker" for f in findings)


def test_insecure_http_bug_is_caught():
    findings = _run("insecure_http_bug.py")
    assert any(f.source == "insecure_http_checker" for f in findings)


def test_command_injection_bug_is_caught():
    findings = _run("command_injection_bug.py")
    assert any(f.source == "command_injection_checker" for f in findings)


def test_resource_leak_bug_is_caught():
    findings = _run("resource_leak_bug.py")
    assert any(f.source == "resource_leak_checker" for f in findings)


def test_index_guard_bug_is_caught():
    findings = _run("index_guard_bug.py")
    assert any(f.source == "index_guard_checker" for f in findings)


def test_none_attribute_bug_is_caught():
    findings = _run("none_attribute_bug.py")
    assert any(f.source == "none_attribute_checker" for f in findings)


def test_weak_crypto_bug_is_caught():
    findings = _run("weak_crypto_bug.py")
    assert any(f.source == "weak_crypto_checker" for f in findings)


def test_insecure_deserialization_bug_is_caught():
    findings = _run("insecure_deserialization_bug.py")
    assert any(f.source == "insecure_deserialization_checker" for f in findings)


def test_path_traversal_bug_is_caught():
    findings = _run("path_traversal_bug.py")
    assert any(f.source == "path_traversal_checker" for f in findings)


def test_zip_slip_bug_is_caught():
    findings = _run("zip_slip_bug.py")
    assert any(f.source == "zip_slip_checker" for f in findings)


def test_undefined_name_bug_is_caught():
    findings = _run("undefined_name_bug.py")
    assert any(f.source == "undefined_name_checker" for f in findings)


def test_unused_import_bug_is_caught():
    findings = _run("unused_import_bug.py")
    assert any(f.source == "unused_import_checker" for f in findings)


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
        "unsafe_yaml_checker",
        "insecure_http_checker",
        "command_injection_checker",
        "resource_leak_checker",
        "index_guard_checker",
        "none_attribute_checker",
        "weak_crypto_checker",
        "insecure_deserialization_checker",
        "path_traversal_checker",
        "zip_slip_checker",
        "undefined_name_checker",
        "unused_import_checker",
    }
    assert expected_sources <= sources_seen
    assert len(CHECKERS) == len(expected_sources), (
        "A checker was added/removed from the registry without updating "
        "this golden-fixture suite to match — add a fixture (and a test "
        "above) for the new checker, then update expected_sources here."
    )
