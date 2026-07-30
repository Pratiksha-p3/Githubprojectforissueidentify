from src.core.models import Severity
from src.integrations import cve_enrichment


def test_uses_nvd_result_when_available(monkeypatch):
    monkeypatch.setattr(
        cve_enrichment.nvd_client,
        "lookup_cves",
        lambda name, **k: [{"cve_id": "CVE-2024-1", "cvss_score": 9.5, "description": "bad"}],
    )
    osv_calls = []
    monkeypatch.setattr(
        cve_enrichment.osv_client, "lookup_vulnerabilities",
        lambda *a, **k: osv_calls.append(1),
    )

    results = cve_enrichment.lookup_cves("pkg", "app.py")

    assert results[0]["cve_id"] == "CVE-2024-1"
    assert osv_calls == []  # OSV never called since NVD had a result


def test_falls_back_to_osv_when_nvd_has_no_results(monkeypatch):
    monkeypatch.setattr(cve_enrichment.nvd_client, "lookup_cves", lambda name, **k: [])
    monkeypatch.setattr(
        cve_enrichment.osv_client, "lookup_vulnerabilities",
        lambda name, ecosystem, **k: [{"cve_id": "GHSA-1", "cvss_score": 6.0, "description": "x"}],
    )

    results = cve_enrichment.lookup_cves("pkg", "app.py")

    assert results[0]["cve_id"] == "GHSA-1"


def test_no_fallback_when_ecosystem_unknown(monkeypatch):
    monkeypatch.setattr(cve_enrichment.nvd_client, "lookup_cves", lambda name, **k: [])
    osv_calls = []
    monkeypatch.setattr(
        cve_enrichment.osv_client, "lookup_vulnerabilities",
        lambda *a, **k: osv_calls.append(1),
    )

    results = cve_enrichment.lookup_cves("pkg", "README.md")

    assert results == []
    assert osv_calls == []


def test_enrich_dependency_creates_critical_finding_for_high_cvss(monkeypatch):
    monkeypatch.setattr(
        cve_enrichment,
        "lookup_cves",
        lambda name, filename, **k: [
            {"cve_id": "CVE-2024-1", "cvss_score": 9.8, "description": "RCE"}
        ],
    )

    findings = cve_enrichment.enrich_dependency("pkg", "app.py")

    assert len(findings) == 1
    assert findings[0].severity == Severity.CRITICAL
    assert findings[0].source == "cve_enrichment"
    assert "CVE-2024-1" in findings[0].message


def test_severity_mapping_from_numeric_cvss():
    assert cve_enrichment._severity_for(9.5, None) == Severity.CRITICAL
    assert cve_enrichment._severity_for(7.2, None) == Severity.WARNING
    assert cve_enrichment._severity_for(3.0, None) == Severity.INFO


def test_severity_falls_back_to_qualitative_when_no_numeric_score():
    """The real bug this guards against: OSV frequently gives no numeric
    score at all (only a CVSS vector string, handled in osv_client.py) —
    the qualitative GitHub Security Advisory label must still map to a
    real severity instead of silently defaulting to INFO."""
    assert cve_enrichment._severity_for(None, "CRITICAL") == Severity.CRITICAL
    assert cve_enrichment._severity_for(None, "HIGH") == Severity.WARNING
    assert cve_enrichment._severity_for(None, "MODERATE") == Severity.WARNING
    assert cve_enrichment._severity_for(None, "LOW") == Severity.INFO


def test_severity_is_info_when_nothing_available():
    assert cve_enrichment._severity_for(None, None) == Severity.INFO


def test_numeric_cvss_takes_priority_over_qualitative():
    assert cve_enrichment._severity_for(9.5, "LOW") == Severity.CRITICAL


def test_enrich_dependency_uses_qualitative_severity_when_cvss_is_absent(monkeypatch):
    monkeypatch.setattr(
        cve_enrichment,
        "lookup_cves",
        lambda name, filename, **k: [
            {
                "cve_id": "GHSA-xxxx",
                "cvss_score": None,
                "qualitative_severity": "HIGH",
                "description": "leak",
            }
        ],
    )

    findings = cve_enrichment.enrich_dependency("pkg", "app.py")

    assert findings[0].severity == Severity.WARNING


def test_enrich_dependency_returns_empty_list_for_clean_package(monkeypatch):
    monkeypatch.setattr(cve_enrichment, "lookup_cves", lambda name, filename, **k: [])
    assert cve_enrichment.enrich_dependency("safe-pkg", "app.py") == []
