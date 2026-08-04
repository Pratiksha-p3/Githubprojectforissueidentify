from src.core.confidence import (
    is_safe_to_auto_apply,
    manual_review_reason,
    review_reason,
    summarize_auto_fix_status,
)
from src.core.models import ConfidenceTier, Finding, Severity


def make_finding(
    confidence: ConfidenceTier, *, fix: str = "", source: str = "test", category: str = "runtime"
) -> Finding:
    return Finding(
        file="app.py",
        line=1,
        category=category,
        severity=Severity.WARNING,
        message="test finding",
        confidence=confidence,
        fix=fix,
        source=source,
    )


def test_only_high_confidence_is_safe_to_auto_apply():
    assert is_safe_to_auto_apply(make_finding(ConfidenceTier.HIGH)) is True
    assert is_safe_to_auto_apply(make_finding(ConfidenceTier.MEDIUM)) is False
    assert is_safe_to_auto_apply(make_finding(ConfidenceTier.LOW)) is False


def test_high_confidence_needs_no_manual_review_reason():
    assert manual_review_reason(make_finding(ConfidenceTier.HIGH, fix="x = 1")) == ""


def test_no_fix_reason_takes_priority_over_confidence_tier():
    reason = manual_review_reason(make_finding(ConfidenceTier.MEDIUM, fix=""))
    assert "No fix was generated" in reason


def test_medium_confidence_reason_mentions_judgment_call():
    reason = manual_review_reason(make_finding(ConfidenceTier.MEDIUM, fix="x = 1"))
    assert "MEDIUM" in reason
    assert "judgment call" in reason


def test_medium_confidence_syntax_finding_gets_reindent_specific_wording():
    """A MEDIUM-confidence syntax-category finding (orchestrator.py's
    block-reindent fix) isn't a "raise vs. return vs. skip" guard-behavior
    judgment call -- the generic checker wording would be actively
    misleading here, so this category gets its own accurate reason."""
    reason = manual_review_reason(
        make_finding(ConfidenceTier.MEDIUM, fix="    x = 1", category="syntax")
    )
    assert "smallest reindent" in reason
    assert "raise vs. return vs. skip" not in reason


def test_low_confidence_reason_mentions_llm():
    reason = manual_review_reason(make_finding(ConfidenceTier.LOW, fix="x = 1"))
    assert "LOW" in reason
    assert "LLM" in reason


def test_summarize_counts_are_accurate():
    findings = [
        make_finding(ConfidenceTier.HIGH, fix="x = 1"),
        make_finding(ConfidenceTier.MEDIUM, fix="x = 1"),
        make_finding(ConfidenceTier.LOW, fix=""),
    ]

    summary = summarize_auto_fix_status(findings)

    assert summary["auto_fixed_count"] == 1
    assert summary["manual_review_count"] == 2
    assert len(summary["manual_review_details"]) == 2


def test_summarize_with_no_findings_is_all_zero():
    summary = summarize_auto_fix_status([])
    assert summary == {
        "auto_fixed_count": 0,
        "manual_review_count": 0,
        "manual_review_details": [],
    }


def test_review_reason_matches_manual_review_reason_for_medium_and_low():
    medium = make_finding(ConfidenceTier.MEDIUM, fix="x = 1")
    assert review_reason(medium) == manual_review_reason(medium)

    no_fix = make_finding(ConfidenceTier.LOW, fix="")
    assert review_reason(no_fix) == manual_review_reason(no_fix)


def test_review_reason_for_high_confidence_without_auto_apply_suggests_the_flag():
    """manual_review_reason() alone returns "" for HIGH -- review_reason()
    fills that gap for a HIGH finding that's sitting unapplied because
    --auto-apply wasn't used this run, so a caller never has to
    special-case HIGH confidence separately when displaying reasons."""
    finding = make_finding(ConfidenceTier.HIGH, fix="x = 1")
    reason = review_reason(finding, auto_apply=False)
    assert reason != ""
    assert "--auto-apply" in reason


def test_review_reason_for_high_confidence_with_auto_apply_mentions_conflict():
    """When --auto-apply WAS used but a HIGH finding is still present,
    the only way that happens is a same-line conflict in
    apply_fixes_to_file() -- the reason should say so, not repeat the
    "re-run with --auto-apply" suggestion that's already moot."""
    finding = make_finding(ConfidenceTier.HIGH, fix="x = 1")
    reason = review_reason(finding, auto_apply=True)
    assert reason != ""
    assert "conflicted" in reason


def test_summarize_details_include_file_line_source_and_reason():
    findings = [make_finding(ConfidenceTier.MEDIUM, fix="x = 1", source="my_checker")]

    summary = summarize_auto_fix_status(findings)

    detail = summary["manual_review_details"][0]
    assert detail["file"] == "app.py"
    assert detail["line"] == 1
    assert detail["source"] == "my_checker"
    assert "judgment call" in detail["reason"]
