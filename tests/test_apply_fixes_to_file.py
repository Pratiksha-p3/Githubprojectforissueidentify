from src.cli.review_pr import apply_fixes_to_file
from src.core.models import ConfidenceTier, Finding, Severity


def make_finding(line: int, fix: str = "", source: str = "checker") -> Finding:
    return Finding(
        file="app.py",
        line=line,
        category="runtime",
        severity=Severity.WARNING,
        message="msg",
        fix=fix,
        confidence=ConfidenceTier.MEDIUM,
        source=source,
    )


def test_applies_a_single_line_fix():
    code = "def divide(a, b):\n    return a / b\n"
    fix = "    if b == 0:\n        raise ZeroDivisionError\n    return a / b"
    finding = make_finding(2, fix=fix)

    patched, applied, remaining = apply_fixes_to_file(code, [finding])

    assert applied == [finding]
    assert remaining == []
    assert "if b == 0:" in patched
    assert "return a / b" in patched


def test_findings_with_no_fix_are_never_applied():
    code = "x = 1\n"
    finding = make_finding(1, fix="")

    patched, applied, remaining = apply_fixes_to_file(code, [finding])

    assert patched == code
    assert applied == []
    assert remaining == [finding]


def test_applies_multiple_fixes_on_different_lines():
    code = "a = 1\nb = 2\nc = 3\n"
    f1 = make_finding(1, fix="a = 100")
    f3 = make_finding(3, fix="c = 300")

    patched, applied, remaining = apply_fixes_to_file(code, [f1, f3])

    assert sorted(applied, key=lambda f: f.line) == [f1, f3]
    assert remaining == []
    lines = patched.splitlines()
    assert lines[0] == "a = 100"
    assert lines[1] == "b = 2"
    assert lines[2] == "c = 300"


def test_reverse_order_application_does_not_corrupt_earlier_lines():
    """A fix that expands one line into three must not shift the line
    number a fix higher up in the file is anchored to."""
    code = "a = 1\nb = 2\nc = 3\n"
    f1 = make_finding(1, fix="a = 100")
    f2 = make_finding(2, fix="if True:\n    b = 200\nelse:\n    b = 0")

    patched, applied, remaining = apply_fixes_to_file(code, [f1, f2])

    assert sorted(applied, key=lambda f: f.line) == [f1, f2]
    assert remaining == []
    lines = patched.splitlines()
    assert lines[0] == "a = 100"
    assert "b = 200" in patched
    assert lines[-1] == "c = 3"


def test_two_findings_on_the_same_line_conflict_and_neither_is_applied():
    code = "x = 1\n"
    f1 = make_finding(1, fix="x = 2", source="checker_a")
    f2 = make_finding(1, fix="x = 3", source="checker_b")

    patched, applied, remaining = apply_fixes_to_file(code, [f1, f2])

    assert patched == code  # unchanged -- neither conflicting fix applied
    assert applied == []
    assert sorted(remaining, key=lambda f: f.fix) == sorted([f1, f2], key=lambda f: f.fix)


def test_three_findings_on_the_same_line_all_go_to_remaining():
    """Regression check: an earlier version of the conflict-detection
    logic could silently drop a third finding on an already-conflicted
    line instead of tracking it as remaining."""
    code = "x = 1\n"
    f1 = make_finding(1, fix="x = 2")
    f2 = make_finding(1, fix="x = 3")
    f3 = make_finding(1, fix="x = 4")

    _patched, applied, remaining = apply_fixes_to_file(code, [f1, f2, f3])

    assert applied == []
    assert sorted(remaining, key=lambda f: f.fix) == sorted([f1, f2, f3], key=lambda f: f.fix)


def test_out_of_range_line_number_is_not_applied():
    code = "x = 1\n"
    finding = make_finding(99, fix="y = 2")

    patched, applied, remaining = apply_fixes_to_file(code, [finding])

    assert patched == code
    assert applied == []
    assert remaining == [finding]


def test_preserves_trailing_newline_presence():
    code_with_newline = "x = 1\n"
    code_without_newline = "x = 1"
    finding = make_finding(1, fix="x = 2")

    patched_with, _, _ = apply_fixes_to_file(code_with_newline, [finding])
    patched_without, _, _ = apply_fixes_to_file(code_without_newline, [finding])

    assert patched_with.endswith("\n")
    assert not patched_without.endswith("\n")


def test_empty_findings_list_returns_code_unchanged():
    code = "x = 1\ny = 2\n"
    patched, applied, remaining = apply_fixes_to_file(code, [])
    assert patched == code
    assert applied == []
    assert remaining == []
