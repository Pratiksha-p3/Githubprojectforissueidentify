"""
src/integrations/nvd_client.py

Looks up known CVEs for a package from the NVD (National Vulnerability
Database) REST API — the primary CVE source; src/integrations/
osv_client.py is the fallback when NVD has no match or is rate-limited.

NVD's public API is aggressively rate-limited without a key (5 requests
per 30s window) — reuses src/core/backoff.py rather than a custom retry
loop. Setting NVD_API_KEY raises that limit to 50/30s.
"""
from __future__ import annotations

import requests

from src.core.backoff import call_with_backoff
from src.core.config import settings

_API_BASE = "https://services.nvd.nist.gov/rest/json/cves/2.0"


def _is_retryable(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(m in text for m in ("429", "rate limit", "timeout", "connection", "502", "503"))


def lookup_cves(package_name: str, *, version: str | None = None) -> list[dict]:
    """
    Returns [{cve_id, cvss_score, description}, ...] for CVEs matching
    `package_name`. Empty list on no matches OR a failed lookup — this is
    best-effort enrichment, never a hard dependency for a review to
    complete (same pattern as every other optional pass in this project).
    """
    headers = {"apiKey": settings.nvd_api_key} if settings.nvd_api_key else {}
    params: dict[str, str | int] = {"keywordSearch": package_name, "resultsPerPage": 20}

    def _do_call() -> dict:
        resp = requests.get(_API_BASE, headers=headers, params=params, timeout=15)
        resp.raise_for_status()
        return resp.json()

    try:
        data = call_with_backoff(_do_call, should_retry=_is_retryable)
    except Exception as e:
        print(f"[nvd_client] Lookup failed for {package_name}: {e}")
        return []

    return [_to_cve_dict(v) for v in data.get("vulnerabilities", [])]


def _to_cve_dict(vuln: dict) -> dict:
    cve = vuln.get("cve", {})
    metrics = cve.get("metrics", {})

    cvss_score = None
    for key in ("cvssMetricV31", "cvssMetricV30", "cvssMetricV2"):
        entries = metrics.get(key)
        if entries:
            cvss_score = entries[0].get("cvssData", {}).get("baseScore")
            break

    descriptions = cve.get("descriptions", [])
    description = next((d["value"] for d in descriptions if d.get("lang") == "en"), "")

    return {"cve_id": cve.get("id", ""), "cvss_score": cvss_score, "description": description}
