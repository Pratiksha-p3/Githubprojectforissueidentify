from src.core.grounding import is_grounded
from src.core.models import Finding, Severity


def make_finding(line: int, bad_code: str) -> Finding:
    return Finding(
        file="app.py",
        line=line,
        category="runtime",
        severity=Severity.WARNING,
        message="test finding",
        bad_code=bad_code,
    )


SOURCE = "def divide(a, b):\n    return a / b\n\nx = divide(4, 0)\n"


def test_exact_line_match_is_grounded():
    assert is_grounded(make_finding(2, "return a / b"), SOURCE) is True


def test_off_by_one_within_window_is_grounded():
    assert is_grounded(make_finding(3, "return a / b"), SOURCE) is True


def test_fabricated_content_is_not_grounded():
    finding = make_finding(2, "if b == 0: raise ValueError()")
    assert is_grounded(finding, SOURCE) is False


def test_diff_hunk_garbage_is_not_grounded():
    finding = make_finding(1, "@@ -0,0 +1,6 @@")
    assert is_grounded(finding, SOURCE) is False


def test_empty_bad_code_makes_no_claim_and_is_grounded():
    assert is_grounded(make_finding(2, ""), SOURCE) is True


def test_whitespace_differences_are_normalized():
    finding = make_finding(2, "return   a   /   b")
    assert is_grounded(finding, SOURCE) is True
