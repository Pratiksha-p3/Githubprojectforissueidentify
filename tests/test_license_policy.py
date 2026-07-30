from src.core.models import Severity
from src.tools.license_policy import check_licenses, to_findings


def test_banned_license_is_flagged():
    results = check_licenses({"bad-pkg": "GPL-3.0"})
    assert results[0].banned is True


def test_permissive_license_is_not_flagged():
    results = check_licenses({"good-pkg": "MIT"})
    assert results[0].banned is False


def test_to_findings_only_includes_banned_packages():
    results = check_licenses({"bad-pkg": "AGPL-3.0", "good-pkg": "Apache-2.0"})
    findings = to_findings(results)

    assert len(findings) == 1
    assert "bad-pkg" in findings[0].message
    assert findings[0].severity == Severity.CRITICAL
    assert findings[0].source == "license_policy"


def test_to_findings_empty_when_nothing_banned():
    results = check_licenses({"good-pkg": "MIT", "other-pkg": "BSD-3-Clause"})
    assert to_findings(results) == []


def test_custom_banned_license_set():
    results = check_licenses(
        {"pkg": "Commons-Clause"}, banned_licenses=frozenset({"Commons-Clause"})
    )
    assert results[0].banned is True
