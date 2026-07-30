"""
src/integrations/osv_client.py

Fallback CVE/vulnerability source when NVD (src/integrations/
nvd_client.py) has no match or is unavailable — OSV.dev's free, keyless
API, queried by ecosystem-mapped package name (PyPI, npm, Go, ...)
rather than NVD's more general keyword search.
"""
from __future__ import annotations

import requests

from src.core.backoff import call_with_backoff

_API_BASE = "https://api.osv.dev/v1/query"

_ECOSYSTEM_BY_EXTENSION = {
    ".py": "PyPI",
    ".js": "npm",
    ".ts": "npm",
    ".go": "Go",
    ".rb": "RubyGems",
}


def _is_retryable(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(m in text for m in ("429", "rate limit", "timeout", "connection", "502", "503"))


def ecosystem_for_file(filename: str) -> str | None:
    for ext, ecosystem in _ECOSYSTEM_BY_EXTENSION.items():
        if filename.endswith(ext):
            return ecosystem
    return None


def lookup_vulnerabilities(
    package_name: str, ecosystem: str, *, version: str | None = None
) -> list[dict]:
    payload: dict = {"package": {"name": package_name, "ecosystem": ecosystem}}
    if version:
        payload["version"] = version

    def _do_call() -> dict:
        resp = requests.post(_API_BASE, json=payload, timeout=15)
        resp.raise_for_status()
        return resp.json()

    try:
        data = call_with_backoff(_do_call, should_retry=_is_retryable)
    except Exception as e:
        print(f"[osv_client] Lookup failed for {package_name}: {e}")
        return []

    return [_to_vuln_dict(v) for v in data.get("vulns", [])]


def _to_vuln_dict(vuln: dict) -> dict:
    # OSV's severity[].score is NOT reliably numeric: many real entries
    # (verified against the live API) carry a CVSS vector string, e.g.
    # "CVSS:3.1/AV:N/AC:H/..." under type "CVSS_V3" — not a base score.
    # Only trust it as cvss_score when it actually parses as a number;
    # otherwise fall back to database_specific.severity, a qualitative
    # LOW/MODERATE/HIGH/CRITICAL string GitHub Security Advisories
    # (OSV's main source) provide instead.
    cvss_score = None
    for entry in vuln.get("severity", []):
        raw_score = entry.get("score")
        try:
            cvss_score = float(raw_score)
            break
        except (TypeError, ValueError):
            continue

    return {
        "cve_id": vuln.get("id", ""),
        "cvss_score": cvss_score,
        "qualitative_severity": vuln.get("database_specific", {}).get("severity"),
        "description": vuln.get("summary") or vuln.get("details", ""),
    }
