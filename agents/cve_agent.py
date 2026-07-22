"""
agents/cve_agent.py

Feature 3: CVE Database Lookup
When Semgrep finds a vulnerability, cross-reference NVD/OSV to find:
- Real CVE IDs
- CVSS scores
- Known exploits
- Affected versions
"""
from __future__ import annotations

import requests
import time
from dataclasses import dataclass


@dataclass
class CVEResult:
    cve_id: str
    description: str
    cvss_score: float
    severity: str
    url: str
    published: str


class CVEAgent:

    NVD_URL = "https://services.nvd.nist.gov/rest/json/cves/2.0"
    OSV_URL = "https://api.osv.dev/v1/query"

    def __init__(self):
        self._cache: dict[str, list[CVEResult]] = {}

    def lookup(self, keyword: str, language: str = "") -> list[CVEResult]:
        """Look up CVEs related to a security finding keyword."""
        cache_key = f"{keyword}:{language}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        results = []

        # Try NVD first
        try:
            results = self._query_nvd(keyword)
        except Exception as e:
            print(f"[cve] NVD lookup failed: {e}")

        # Fall back to OSV if NVD returns nothing
        if not results:
            try:
                results = self._query_osv(keyword, language)
            except Exception as e:
                print(f"[cve] OSV lookup failed: {e}")

        self._cache[cache_key] = results[:3]  # top 3 only
        return self._cache[cache_key]

    def enrich_findings(self, findings: list[dict]) -> list[dict]:
        """
        For each security finding, add CVE data if available.
        Adds: cve_ids, cvss_score, cve_urls fields to finding.
        """
        for finding in findings:
            if finding.get("category") != "security":
                continue

            # Build search keyword from rule_id or message
            keyword = self._extract_keyword(finding)
            if not keyword:
                continue

            cves = self.lookup(keyword)
            if cves:
                finding["cve_ids"]   = [c.cve_id for c in cves]
                finding["cvss_score"] = max(c.cvss_score for c in cves)
                finding["cve_urls"]   = [c.url for c in cves]
                finding["cve_detail"] = cves[0].description[:200]

                # Upgrade severity if CVE score is high
                if finding["cvss_score"] >= 9.0:
                    finding["severity"] = "critical"
                elif finding["cvss_score"] >= 7.0 and finding["severity"] == "info":
                    finding["severity"] = "warning"

                print(
                    f"[cve] {finding.get('file','?')} — "
                    f"matched {len(cves)} CVEs "
                    f"(top CVSS: {finding['cvss_score']})"
                )

        return findings

    # ── Internal ──────────────────────────────────────────

    def _query_nvd(self, keyword: str) -> list[CVEResult]:
        time.sleep(0.6)  # NVD rate limit: 5 req/30s without API key
        resp = requests.get(
            self.NVD_URL,
            params={"keywordSearch": keyword, "resultsPerPage": 5},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()

        results = []
        for item in data.get("vulnerabilities", []):
            cve = item.get("cve", {})
            cve_id = cve.get("id", "")

            # Get CVSS score
            metrics = cve.get("metrics", {})
            cvss_score = 0.0
            for key in ("cvssMetricV31", "cvssMetricV30", "cvssMetricV2"):
                if key in metrics and metrics[key]:
                    cvss_score = (
                        metrics[key][0]
                        .get("cvssData", {})
                        .get("baseScore", 0.0)
                    )
                    break

            # Get description
            desc = ""
            for d in cve.get("descriptions", []):
                if d.get("lang") == "en":
                    desc = d.get("value", "")
                    break

            severity = self._score_to_severity(cvss_score)

            results.append(CVEResult(
                cve_id      = cve_id,
                description = desc[:300],
                cvss_score  = cvss_score,
                severity    = severity,
                url         = f"https://nvd.nist.gov/vuln/detail/{cve_id}",
                published   = cve.get("published", ""),
            ))

        return results

    def _query_osv(self, keyword: str, language: str) -> list[CVEResult]:
        ecosystem_map = {
            "python": "PyPI", "javascript": "npm",
            "typescript": "npm", "go": "Go",
            "java": "Maven", "rust": "crates.io",
        }
        ecosystem = ecosystem_map.get(language, "")

        payload: dict = {"version": "1.0"}
        if ecosystem:
            payload["package"] = {"name": keyword, "ecosystem": ecosystem}
        else:
            payload["query"] = keyword

        resp = requests.post(self.OSV_URL, json=payload, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        results = []
        for vuln in data.get("vulns", [])[:5]:
            cve_id = next(
                (a["id"] for a in vuln.get("aliases", []) if a.startswith("CVE-")),
                vuln.get("id", "OSV-?"),
            )
            results.append(CVEResult(
                cve_id      = cve_id,
                description = vuln.get("summary", "")[:300],
                cvss_score  = 5.0,  # OSV doesn't always have CVSS
                severity    = "warning",
                url         = f"https://osv.dev/vulnerability/{vuln.get('id','')}",
                published   = vuln.get("published", ""),
            ))

        return results

    def _extract_keyword(self, finding: dict) -> str:
        rule_id = finding.get("rule_id", "")
        message = finding.get("message", "")

        keywords = {
            "sql":        "SQL injection",
            "injection":  "injection vulnerability",
            "xss":        "cross-site scripting",
            "csrf":       "CSRF",
            "pickle":     "pickle deserialization",
            "eval":       "eval code injection",
            "md5":        "MD5 weak hash",
            "hardcoded":  "hardcoded credential",
            "secret":     "hardcoded secret",
            "ssrf":       "SSRF server-side request forgery",
        }

        combined = (rule_id + " " + message).lower()
        for kw, search_term in keywords.items():
            if kw in combined:
                return search_term

        return ""

    @staticmethod
    def _score_to_severity(score: float) -> str:
        if score >= 9.0:
            return "critical"
        elif score >= 7.0:
            return "high"
        elif score >= 4.0:
            return "medium"
        return "low"