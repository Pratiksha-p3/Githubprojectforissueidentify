from src.core.models import Finding, ReviewResult, ReviewStatus, Severity
from src.core.pr_gate import GateDecision, decide, gate_reason


def make_result(status: ReviewStatus, findings: list[Finding] | None = None) -> ReviewResult:
    return ReviewResult(
        repo="acme/widgets",
        commit_sha="abc123",
        status=status,
        findings=findings or [],
    )


def make_finding(severity: Severity) -> Finding:
    return Finding(
        file="app.py", line=1, category="runtime", severity=severity, message="test"
    )


def test_completed_with_no_critical_findings_approves():
    result = make_result(ReviewStatus.COMPLETED, [make_finding(Severity.WARNING)])
    assert decide(result) == GateDecision.APPROVE


def test_completed_with_critical_finding_blocks():
    result = make_result(ReviewStatus.COMPLETED, [make_finding(Severity.CRITICAL)])
    assert decide(result) == GateDecision.BLOCK


def test_degraded_is_always_review_required_even_with_zero_findings():
    result = make_result(ReviewStatus.DEGRADED, [])
    assert decide(result) == GateDecision.REVIEW_REQUIRED


def test_failed_is_always_review_required_even_with_zero_findings():
    result = make_result(ReviewStatus.FAILED, [])
    assert decide(result) == GateDecision.REVIEW_REQUIRED


def test_degraded_is_review_required_even_with_critical_findings():
    """Status honesty outranks finding severity -- an incomplete review
    doesn't get to claim BLOCK any more than it gets to claim APPROVE."""
    result = make_result(ReviewStatus.DEGRADED, [make_finding(Severity.CRITICAL)])
    assert decide(result) == GateDecision.REVIEW_REQUIRED


def test_gate_reason_mentions_critical_count_when_blocked():
    result = make_result(
        ReviewStatus.COMPLETED, [make_finding(Severity.CRITICAL), make_finding(Severity.CRITICAL)]
    )
    reason = gate_reason(result)
    assert "2 critical" in reason


def test_gate_reason_never_implies_clean_pass_when_incomplete():
    result = make_result(ReviewStatus.DEGRADED, [])
    reason = gate_reason(result)
    assert "not treat this as a clean pass" in reason
