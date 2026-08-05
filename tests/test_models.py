from src.core.models import (
    ConfidenceTier,
    Finding,
    ReviewResult,
    ReviewStatus,
    Severity,
)


def test_review_status_has_exactly_three_members():
    assert {s.value for s in ReviewStatus} == {"completed", "degraded", "failed"}


def test_confidence_tier_has_exactly_three_members():
    assert {c.value for c in ConfidenceTier} == {"high", "medium", "low"}


def make_finding(**overrides) -> Finding:
    defaults = dict(
        file="app.py",
        line=10,
        category="runtime",
        severity=Severity.CRITICAL,
        message="division by zero",
    )
    defaults.update(overrides)
    return Finding(**defaults)


def test_completed_review_with_no_critical_findings_is_approvable():
    result = ReviewResult(
        repo="acme/widgets",
        commit_sha="abc123",
        status=ReviewStatus.COMPLETED,
        findings=[make_finding(severity=Severity.INFO)],
    )
    assert result.is_approvable is True
    assert result.critical_count == 0


def test_completed_review_with_critical_finding_is_not_approvable():
    result = ReviewResult(
        repo="acme/widgets",
        commit_sha="abc123",
        status=ReviewStatus.COMPLETED,
        findings=[make_finding(severity=Severity.CRITICAL)],
    )
    assert result.is_approvable is False
    assert result.critical_count == 1


def test_degraded_review_is_never_approvable_even_with_zero_findings():
    """The core regression this type exists to prevent: a rate-limited or
    otherwise incomplete review must never read as a clean pass just
    because it happens to have zero findings."""
    result = ReviewResult(
        repo="acme/widgets",
        commit_sha="abc123",
        status=ReviewStatus.DEGRADED,
        findings=[],
    )
    assert result.is_approvable is False


def test_failed_review_is_never_approvable_even_with_zero_findings():
    result = ReviewResult(
        repo="acme/widgets",
        commit_sha="abc123",
        status=ReviewStatus.FAILED,
        findings=[],
    )
    assert result.is_approvable is False


def test_finding_defaults_to_lowest_confidence_tier():
    finding = make_finding()
    assert finding.confidence == ConfidenceTier.LOW


def test_finding_with_no_fix_has_fix_is_false():
    assert make_finding(fix="").has_fix is False


def test_finding_with_real_fix_text_has_fix_is_true():
    assert make_finding(fix="x = 1").has_fix is True


def test_finding_marked_as_deletion_has_fix_is_true_even_with_empty_fix_text():
    """fix_is_deletion is what distinguishes an intentional "delete these
    lines, replacing them with nothing" (e.g.
    src/analyzers/unused_import_checker.py) from the ordinary "no fix was
    generated" meaning fix == "" has everywhere else -- both leave `fix`
    itself empty, so has_fix is the only thing that tells them apart."""
    finding = make_finding(fix="", fix_is_deletion=True)
    assert finding.fix == ""
    assert finding.has_fix is True


def test_fix_is_deletion_defaults_to_false():
    assert make_finding().fix_is_deletion is False
