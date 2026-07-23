"""
tools/sonarqube_scanner.py

SonarQube as a second static analyzer alongside Semgrep (agents/security_agent.py).
Same output shape (findings in the shared review schema) so results merge
with everything else via merge_findings().

Unlike Semgrep, SonarQube analyzes a whole project against a server-side
project key, not one temp file at a time — so this runs `sonar-scanner`
against the repo root (the standard CI usage pattern: check out the repo,
run the scanner, read results back from the SonarQube Web API) and then
filters issues down to files actually touched by the PR.

Inert until SONAR_TOKEN, SONAR_HOST_URL, and SONAR_PROJECT_KEY are all set
and the `sonar-scanner` CLI is on PATH — scan_files() returns [] with a
clear log line otherwise, exactly like SecurityAgent does when Semgrep
isn't installed.

Install: https://docs.sonarsource.com/sonarqube/latest/analyzing-source-code/scanners/sonarscanner/
"""
from __future__ import annotations

import shutil
import subprocess
import requests

from config import cfg

_SEVERITY_MAP = {
    "BLOCKER":  "critical",
    "CRITICAL": "critical",
    "MAJOR":    "warning",
    "MINOR":    "info",
    "INFO":     "info",
}


class SonarQubeScanner:

    def __init__(self, timeout: int = 300):
        self.timeout = timeout
        self._scanner_available = shutil.which("sonar-scanner") is not None
        self._configured = bool(
            cfg.sonar_token and cfg.sonar_host_url and cfg.sonar_project_key
        )

    @property
    def available(self) -> bool:
        return self._scanner_available and self._configured

    def scan_files(self, pr_files, repo_root: str = ".") -> list[dict]:
        """Runs sonar-scanner against the repo and returns findings for
        only the files touched by this PR."""
        if not self._configured:
            print("[sonarqube] SONAR_TOKEN/SONAR_HOST_URL/SONAR_PROJECT_KEY not fully "
                  "set — skipping SonarQube scan.")
            return []
        if not self._scanner_available:
            print("[sonarqube] sonar-scanner CLI not found on PATH — skipping. "
                  "Install: https://docs.sonarsource.com/sonarqube/latest/analyzing-source-code/scanners/sonarscanner/")
            return []

        changed_files = {pf.filename for pf in pr_files}
        if not changed_files:
            return []

        if not self._run_scanner(repo_root):
            return []

        issues = self._fetch_new_issues()
        findings = []
        for issue in issues:
            component = issue.get("component", "")
            # component looks like "<projectKey>:path/to/file.py"
            file_path = component.split(":", 1)[-1]
            if file_path not in changed_files:
                continue

            severity = _SEVERITY_MAP.get(issue.get("severity", "MAJOR"), "warning")
            findings.append({
                "file": file_path,
                "line": issue.get("line", 0),
                "severity": severity,
                "category": "security" if issue.get("type") == "VULNERABILITY" else "quality",
                "message": issue.get("message", ""),
                "fix": "Review against the SonarQube rule for this issue: "
                       f"{issue.get('rule', '')}",
                "source": "sonarqube",
                "rule_id": issue.get("rule", ""),
                "confidence": 0.9,
                "risk_weight": 50 if severity == "critical" else 10 if severity == "warning" else 2,
            })

        if findings:
            print(f"[sonarqube] Found {len(findings)} issues in changed files")
        return findings

    # ── Internal ──────────────────────────────────────────

    def _run_scanner(self, repo_root: str) -> bool:
        cmd = [
            "sonar-scanner",
            f"-Dsonar.projectKey={cfg.sonar_project_key}",
            f"-Dsonar.host.url={cfg.sonar_host_url}",
            f"-Dsonar.token={cfg.sonar_token}",
            "-Dsonar.sources=.",
        ]
        try:
            result = subprocess.run(
                cmd, cwd=repo_root, capture_output=True, text=True, timeout=self.timeout,
            )
            if result.returncode != 0:
                print(f"[sonarqube] scanner exited {result.returncode}: {result.stderr[:500]}")
                return False
            return True
        except subprocess.TimeoutExpired:
            print("[sonarqube] scan timed out")
            return False
        except FileNotFoundError:
            self._scanner_available = False
            return False

    def _fetch_new_issues(self) -> list[dict]:
        try:
            resp = requests.get(
                f"{cfg.sonar_host_url.rstrip('/')}/api/issues/search",
                params={
                    "componentKeys": cfg.sonar_project_key,
                    "statuses": "OPEN,CONFIRMED",
                    "resolved": "false",
                },
                auth=(cfg.sonar_token, ""),
                timeout=30,
            )
            resp.raise_for_status()
            return resp.json().get("issues", [])
        except Exception as e:
            print(f"[sonarqube] Failed to fetch issues: {e}")
            return []
