"""
tools/devsecops.py

Phase 4: DevSecOps Tool Integration

Integrates:
  - Semgrep  (already working)
  - Bandit   (Python-specific security linter)
  - Trivy    (dependency/container vulnerability scanner)
  - SonarQube (code quality, if running locally)

All tools produce findings in the same schema as the LLM agents,
so they can be merged into a single consolidated report.

Install:
  pip install bandit
  # Trivy: https://github.com/aquasecurity/trivy/releases
  # SonarQube: docker run -p 9000:9000 sonarqube
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from ingestion.github_loader import PRFile


@dataclass
class ToolFinding:
    file:       str
    line:       int
    severity:   str      # critical | warning | info
    category:   str
    message:    str
    fix:        str
    tool:       str
    rule_id:    str = ""
    confidence: float = 0.9


class BanditScanner:
    """
    Bandit: Python-specific security linter.
    Catches issues Semgrep OSS misses:
    - assert statements in production code
    - Use of random for security
    - XML vulnerabilities
    - Jinja2 template injection
    - Paramiko shell injection
    """

    def scan(self, pr_files: list[PRFile]) -> list[ToolFinding]:
        python_files = [
            pf for pf in pr_files
            if pf.language == "python"
        ]
        if not python_files:
            return []

        if not self._is_installed():
            print("[bandit] Not installed. Run: pip install bandit")
            return []

        findings = []
        for pf in python_files:
            findings.extend(self._scan_file(pf))

        print(f"[bandit] Found {len(findings)} issues")
        return findings

    def _scan_file(self, pf: PRFile) -> list[ToolFinding]:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False, encoding="utf-8"
        ) as tmp:
            tmp.write(pf.full_content)
            tmp_path = tmp.name

        try:
            result = subprocess.run(
                ["bandit", "-f", "json", "-q", tmp_path],
                capture_output=True, text=True, timeout=30,
            )
            data     = json.loads(result.stdout or "{}")
            findings = []

            for issue in data.get("results", []):
                sev = issue.get("issue_severity", "LOW").lower()
                sev_map = {"high": "critical", "medium": "warning", "low": "info"}

                changed_lines = self._changed_line_numbers(pf)
                line = issue.get("line_number", 0)

                if changed_lines and line not in changed_lines:
                    continue

                findings.append(ToolFinding(
                    file       = pf.filename,
                    line       = line,
                    severity   = sev_map.get(sev, "info"),
                    category   = "security",
                    message    = issue.get("issue_text", ""),
                    fix        = f"See: {issue.get('more_info', '')}",
                    tool       = "bandit",
                    rule_id    = issue.get("test_id", ""),
                    confidence = {"HIGH": 0.95, "MEDIUM": 0.80, "LOW": 0.60}.get(
                        issue.get("issue_confidence", "LOW"), 0.60
                    ),
                ))

            return findings

        except (json.JSONDecodeError, subprocess.TimeoutExpired, FileNotFoundError):
            return []
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def _is_installed(self) -> bool:
        try:
            subprocess.run(["bandit", "--version"], capture_output=True, timeout=5)
            return True
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    def _changed_line_numbers(self, pf: PRFile) -> set[int]:
        import re
        changed = set()
        line_num = 0
        for line in getattr(pf, "patch", "").splitlines():
            if line.startswith("@@"):
                m = re.search(r"\+(\d+)", line)
                if m:
                    line_num = int(m.group(1))
            elif line.startswith("+") and not line.startswith("+++"):
                changed.add(line_num)
                line_num += 1
            elif not line.startswith("-"):
                line_num += 1
        return changed


class TrivyScanner:
    """
    Trivy: scans dependencies for known CVEs.
    Reads requirements.txt, package.json, go.mod, etc.
    from the repository root.

    Download Trivy: https://github.com/aquasecurity/trivy/releases
    Or: winget install AquaSecurity.Trivy
    """

    MANIFEST_FILES = [
        "requirements.txt", "requirements*.txt",
        "package.json", "go.mod", "Cargo.toml",
        "pom.xml", "build.gradle", "Gemfile",
    ]

    def scan(self, repo_path: str = ".") -> list[ToolFinding]:
        if not self._is_installed():
            print("[trivy] Not installed. See: https://github.com/aquasecurity/trivy/releases")
            return []

        try:
            result = subprocess.run(
                [
                    "trivy", "fs",
                    "--format", "json",
                    "--quiet",
                    "--scanners", "vuln,secret",
                    repo_path,
                ],
                capture_output=True, text=True, timeout=120,
            )

            data = json.loads(result.stdout or "{}")
            return self._parse_results(data)

        except subprocess.TimeoutExpired:
            print("[trivy] Scan timed out")
            return []
        except (json.JSONDecodeError, FileNotFoundError) as e:
            print(f"[trivy] Error: {e}")
            return []

    def _parse_results(self, data: dict) -> list[ToolFinding]:
        findings = []

        for result in data.get("Results", []):
            target = result.get("Target", "")

            for vuln in result.get("Vulnerabilities", []):
                severity = vuln.get("Severity", "LOW").lower()
                sev_map  = {
                    "critical": "critical",
                    "high":     "critical",
                    "medium":   "warning",
                    "low":      "info",
                    "unknown":  "info",
                }

                cvss = vuln.get("CVSS", {})
                score_val = 0.0
                for src in cvss.values():
                    score_val = max(score_val, src.get("V3Score", 0.0))

                findings.append(ToolFinding(
                    file     = target,
                    line     = 0,
                    severity = sev_map.get(severity, "info"),
                    category = "security",
                    message  = (
                        f"{vuln.get('VulnerabilityID','')} in "
                        f"{vuln.get('PkgName','')} "
                        f"{vuln.get('InstalledVersion','')} — "
                        f"{vuln.get('Title','')}"
                    ),
                    fix      = (
                        f"Upgrade to {vuln.get('FixedVersion','latest')}"
                        if vuln.get("FixedVersion")
                        else "No fix available yet"
                    ),
                    tool     = "trivy",
                    rule_id  = vuln.get("VulnerabilityID", ""),
                    confidence = min(1.0, score_val / 10.0) if score_val else 0.7,
                ))

        print(f"[trivy] Found {len(findings)} dependency vulnerabilities")
        return findings

    def _is_installed(self) -> bool:
        try:
            subprocess.run(["trivy", "--version"], capture_output=True, timeout=5)
            return True
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False


class SonarQubeScanner:
    """
    SonarQube: code quality and coverage.
    Requires a running SonarQube instance (local Docker or cloud).

    docker run -d -p 9000:9000 sonarqube:community
    Then set SONARQUBE_URL and SONARQUBE_TOKEN in .env
    """

    def __init__(self):
        self.url   = os.getenv("SONARQUBE_URL", "http://localhost:9000")
        self.token = os.getenv("SONARQUBE_TOKEN", "")

    def get_issues(self, project_key: str) -> list[ToolFinding]:
        if not self.token:
            print("[sonarqube] SONARQUBE_TOKEN not set in .env")
            return []

        try:
            import requests
            resp = requests.get(
                f"{self.url}/api/issues/search",
                params={
                    "componentKeys": project_key,
                    "statuses":      "OPEN,CONFIRMED",
                    "ps":            50,
                },
                auth=(self.token, ""),
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()

            findings = []
            sev_map  = {
                "BLOCKER":  "critical",
                "CRITICAL": "critical",
                "MAJOR":    "warning",
                "MINOR":    "info",
                "INFO":     "info",
            }

            for issue in data.get("issues", []):
                component = issue.get("component", "").split(":")[-1]
                findings.append(ToolFinding(
                    file     = component,
                    line     = issue.get("line", 0),
                    severity = sev_map.get(issue.get("severity", "INFO"), "info"),
                    category = issue.get("type", "CODE_SMELL").lower().replace("_", " "),
                    message  = issue.get("message", ""),
                    fix      = f"Rule: {issue.get('rule','')}",
                    tool     = "sonarqube",
                    rule_id  = issue.get("rule", ""),
                ))

            print(f"[sonarqube] Found {len(findings)} issues")
            return findings

        except Exception as e:
            print(f"[sonarqube] Failed: {e}")
            return []


class DevSecOpsScanner:
    """
    Orchestrates all DevSecOps tools and returns a merged finding list.
    Filters to only findings on changed lines (PR-relevant).
    """

    def __init__(self):
        self.bandit  = BanditScanner()
        self.trivy   = TrivyScanner()
        self.sonar   = SonarQubeScanner()

    def scan_pr(
        self,
        pr_files:    list[PRFile],
        repo_path:   str = ".",
        sonar_key:   str = "",
    ) -> list[dict]:
        """
        Run all available tools and return unified findings.
        """
        print("\n[devsecops] Running all security tools...")
        all_findings: list[ToolFinding] = []

        # Bandit (Python)
        all_findings.extend(self.bandit.scan(pr_files))

        # Trivy (dependencies)
        all_findings.extend(self.trivy.scan(repo_path))

        # SonarQube (if configured)
        if sonar_key and self.sonar.token:
            all_findings.extend(self.sonar.get_issues(sonar_key))

        print(
            f"[devsecops] Total: {len(all_findings)} findings "
            f"from all tools"
        )

        # Convert to dict format matching LLM finding schema
        return [
            {
                "file":       f.file,
                "line":       f.line,
                "severity":   f.severity,
                "category":   f.category,
                "message":    f.message,
                "fix":        f.fix,
                "source":     f.tool,
                "rule_id":    f.rule_id,
                "confidence": f.confidence,
                "agent":      f"devsecops/{f.tool}",
            }
            for f in all_findings
        ]