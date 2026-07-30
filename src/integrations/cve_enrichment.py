"""
src/integrations/cve_enrichment.py

Combines NVD (primary) and OSV.dev (fallback) into a single "look up
CVEs for this package" call, and turns any CVE with a meaningful CVSS
score into a Finding — NVD is tried first; OSV is only queried if NVD
returned nothing (rate limited, no match, or genuinely no hits) and the
file's ecosystem can be determined.

KNOWN LIMITATION (confirmed against the live API, not just a theory):
NVD's keywordSearch is free-text over CVE descriptions, not a package-
name match — searching "requests" returns ~20 CVEs that merely mention
the English word "requests" in unrelated advisories (portmapper, Apache,
HP OpenMail, ...), none about the Python package. Because that search
almost always returns *something* for a common package name, the "only
fall back to OSV when NVD returns nothing" rule rarely actually
triggers OSV's far more precise ecosystem+package+version match for
exactly the packages most likely to need it. A real fix needs CPE-based
filtering (matching NVD's configurations.nodes[].cpeMatch against the
actual package), which is out of scope here — this module's real,
verified behavior today is "NVD noise, rarely correctly falls back",
not "NVD primary, OSV fallback" working as cleanly as the docstring
above implies in isolation.

Severity is derived from a numeric CVSS score when one is available
(>=9.0 critical, >=7.0 warning, else info). NVD always provides one.
OSV frequently does NOT — many real entries (GitHub Security Advisories,
OSV's main source) carry only a CVSS *vector string* alongside a
qualitative LOW/MODERATE/HIGH/CRITICAL label, verified against the live
API — so a qualitative fallback is required, not optional, for OSV
results to ever produce a correctly-severitied finding instead of
silently landing on INFO for every one of them.
"""
from __future__ import annotations

from src.core.models import ConfidenceTier, Finding, Severity
from src.integrations import nvd_client, osv_client

_QUALITATIVE_SEVERITY = {
    "CRITICAL": Severity.CRITICAL,
    "HIGH": Severity.WARNING,
    "MODERATE": Severity.WARNING,
    "LOW": Severity.INFO,
}


def lookup_cves(package_name: str, filename: str, *, version: str | None = None) -> list[dict]:
    results = nvd_client.lookup_cves(package_name, version=version)
    if results:
        return results

    ecosystem = osv_client.ecosystem_for_file(filename)
    if ecosystem is None:
        return []
    return osv_client.lookup_vulnerabilities(package_name, ecosystem, version=version)


def _severity_for(cvss_score: float | None, qualitative_severity: str | None) -> Severity:
    if cvss_score is not None:
        if cvss_score >= 9.0:
            return Severity.CRITICAL
        if cvss_score >= 7.0:
            return Severity.WARNING
        return Severity.INFO
    if qualitative_severity:
        return _QUALITATIVE_SEVERITY.get(qualitative_severity.upper(), Severity.INFO)
    return Severity.INFO


def enrich_dependency(
    package_name: str, filename: str, *, version: str | None = None
) -> list[Finding]:
    """Looks up CVEs for `package_name` and returns one Finding per CVE
    found — empty list if the package has no known vulnerabilities (or
    the lookup itself failed; this is best-effort enrichment)."""
    cves = lookup_cves(package_name, filename, version=version)
    findings = []
    for cve in cves:
        if not cve.get("cve_id"):
            continue
        severity = _severity_for(cve.get("cvss_score"), cve.get("qualitative_severity"))
        findings.append(
            Finding(
                file=filename,
                line=0,
                category="dependency",
                severity=severity,
                message=(
                    f"'{package_name}' has a known vulnerability {cve['cve_id']} "
                    f"(CVSS {cve.get('cvss_score', 'unknown')}): "
                    f"{cve.get('description', '')[:200]}"
                ),
                confidence=ConfidenceTier.MEDIUM,
                source="cve_enrichment",
            )
        )
    return findings
