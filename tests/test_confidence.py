from src.core.confidence import is_safe_to_auto_apply
from src.core.models import ConfidenceTier, Finding, Severity


def make_finding(confidence: ConfidenceTier) -> Finding:
    return Finding(
        file="app.py",
        line=1,
        category="runtime",
        severity=Severity.WARNING,
        message="test finding",
        confidence=confidence,
    )


def test_only_high_confidence_is_safe_to_auto_apply():
    assert is_safe_to_auto_apply(make_finding(ConfidenceTier.HIGH)) is True
    assert is_safe_to_auto_apply(make_finding(ConfidenceTier.MEDIUM)) is False
    assert is_safe_to_auto_apply(make_finding(ConfidenceTier.LOW)) is False
