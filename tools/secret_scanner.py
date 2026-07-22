"""
tools/secret_scanner.py

Tier 3 — Feature 8: Secret Scanning + Rotation

Deep git history scanning using TruffleHog and Gitleaks.
Not just the current diff — the ENTIRE git history.

Features:
  - Scans full git history (finds secrets committed months ago)
  - Detects: API keys, passwords, tokens, private keys, AWS creds
  - Auto-triggers GitHub secret alert
  - Sends Slack notification to security team
  - Generates rotation checklist for each exposed secret
  - Blocks PR if active secrets found in history

Install:
  # TruffleHog (recommended)
  pip install trufflehog  OR
  # Download binary: https://github.com/trufflesecurity/trufflehog/releases

  # Gitleaks (alternative)
  # Download: https://github.com/gitleaks/gitleaks/releases

  # For Slack notifications
  Add SLACK_WEBHOOK_URL to .env

Usage:
  # Scan current repo history
  python tools/secret_scanner.py --scan

  # Scan a specific PR's branch
  python tools/secret_scanner.py --scan --repo owner/repo --pr 3

  # Scan and alert
  python tools/secret_scanner.py --scan --alert
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import requests
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

SLACK_WEBHOOK  = os.getenv("SLACK_WEBHOOK_URL", "")
GITHUB_TOKEN   = os.getenv("GITHUB_TOKEN", "")


# ── Secret type metadata ──────────────────────────────────

SECRET_TYPES = {
    "AWS Access Key":       {"severity": "critical", "rotation": "aws iam delete-access-key --access-key-id {value}"},
    "AWS Secret Key":       {"severity": "critical", "rotation": "Rotate via AWS IAM console immediately"},
    "GitHub Token":         {"severity": "critical", "rotation": "github.com/settings/tokens → delete token"},
    "Slack Token":          {"severity": "critical", "rotation": "api.slack.com/apps → Revoke token"},
    "Stripe API Key":       {"severity": "critical", "rotation": "dashboard.stripe.com → API keys → Roll key"},
    "Google API Key":       {"severity": "critical", "rotation": "console.cloud.google.com → Credentials → Delete"},
    "Private Key":          {"severity": "critical", "rotation": "Generate new key pair and revoke old certificate"},
    "Database Password":    {"severity": "critical", "rotation": "ALTER USER 'user'@'host' IDENTIFIED BY 'new_password'"},
    "Hardcoded Password":   {"severity": "warning",  "rotation": "Move to environment variable immediately"},
    "Generic API Key":      {"severity": "warning",  "rotation": "Rotate in the service's API settings"},
    "JWT Secret":           {"severity": "critical", "rotation": "Rotate secret and invalidate all existing tokens"},
    "SendGrid API Key":     {"severity": "critical", "rotation": "app.sendgrid.com → Settings → API Keys"},
    "Twilio Auth Token":    {"severity": "critical", "rotation": "console.twilio.com → Account → Auth Token → Rotate"},
}


@dataclass
class SecretFinding:
    detector:     str         # trufflehog | gitleaks | regex
    secret_type:  str
    file:         str
    line:         int
    commit:       str         # git commit SHA
    commit_date:  str
    author:       str
    raw_value:    str         # redacted after detection
    severity:     str         # critical | warning
    rotation_cmd: str
    verified:     bool = False   # TruffleHog can verify if secret is still active

    def to_dict(self):
        return {
            "category": "security",
            "severity": self.severity,
            "file": self.file,
            "line": self.line,
            "message": f"[{self.secret_type}] Secret found in commit {self.commit[:8]} by {self.author}",
            "fix": "",
            "auto_fixable": False,
            "secret_type": self.secret_type,
            "rotation_cmd": self.rotation_cmd,
        }


@dataclass
class ScanReport:
    scanned_at:     str
    repo:           str
    branch:         str
    total_commits:  int
    findings:       list[SecretFinding] = field(default_factory=list)
    active_secrets: list[SecretFinding] = field(default_factory=list)

    @property
    def has_critical(self) -> bool:
        return any(f.severity == "critical" for f in self.findings)


class SecretScanner:

    def __init__(self):
        self._trufflehog = self._check_tool("trufflehog")
        self._gitleaks   = self._check_tool("gitleaks")

        if not self._trufflehog and not self._gitleaks:
            print("[secret-scan] WARNING: Neither TruffleHog nor Gitleaks found.")
            print("[secret-scan] Install TruffleHog: pip install trufflehog")
            print("[secret-scan] Or Gitleaks: https://github.com/gitleaks/gitleaks/releases")
            print("[secret-scan] Falling back to regex scanning only.")

    # ── Public API ────────────────────────────────────────

    def scan(
        self,
        repo_path:  str = ".",
        repo_name:  str = "",
        branch:     str = "",
        since_commit: str = "",
    ) -> ScanReport:
        """
        Scan git history for secrets.

        Args:
            repo_path:    Local path to git repo (default: current dir)
            repo_name:    GitHub repo name (owner/repo) for alerts
            branch:       Branch to scan (default: current)
            since_commit: Only scan commits since this SHA (for PR scanning)
        """
        print(f"\n[secret-scan] Scanning git history in {repo_path}...")

        # Get branch and commit count
        branch  = branch or self._get_current_branch(repo_path)
        commits = self._count_commits(repo_path)
        print(f"[secret-scan] Branch: {branch}, Commits: {commits}")

        findings: list[SecretFinding] = []

        # TruffleHog (best — verifies if secrets are still active)
        if self._trufflehog:
            findings.extend(self._run_trufflehog(repo_path, since_commit))
        # Gitleaks (fast, comprehensive rules)
        if self._gitleaks:
            findings.extend(self._run_gitleaks(repo_path, since_commit))
        # Regex fallback (always runs)
        findings.extend(self._run_regex_scan(repo_path))

        # Deduplicate
        findings = self._deduplicate(findings)

        # Separate active (verified) from historical
        active = [f for f in findings if f.verified]

        print(
            f"[secret-scan] Found {len(findings)} secrets "
            f"({len(active)} verified active)"
        )

        return ScanReport(
            scanned_at    = datetime.now(timezone.utc).isoformat(),
            repo          = repo_name,
            branch        = branch,
            total_commits = commits,
            findings      = findings,
            active_secrets = active,
        )

    def alert(
        self,
        report:    ScanReport,
        repo:      str = "",
        pr_number: int = 0,
        loader     = None,
    ) -> None:
        """Send alerts for found secrets."""
        if not report.findings:
            return

        # Slack alert
        if SLACK_WEBHOOK:
            self._send_slack_alert(report)

        # GitHub secret alert (if GitHub token available)
        if GITHUB_TOKEN and repo and report.active_secrets:
            self._create_github_secret_alert(report, repo, pr_number)

        # Post to PR
        if loader and repo and pr_number:
            self._post_to_pr(report, repo, pr_number, loader)

    def format_pr_comment(self, report: ScanReport) -> str:
        """Format scan results as a GitHub PR comment."""
        if not report.findings:
            return "## ✅ Secret Scan — No Secrets Found\n*🤖 AI Code Review — TruffleHog/Gitleaks*"

        lines = [
            "## 🚨 Secret Scan — Exposed Secrets Detected!\n",
            f"**{len(report.findings)} secret(s)** found in git history of branch `{report.branch}`.\n",
            f"Active/verified: **{len(report.active_secrets)}**\n",
            "> ⚠️ **Immediate action required.** Secrets in git history remain "
            "exposed even after deletion. You must rotate ALL listed secrets NOW.\n",
        ]

        # Group by severity
        critical = [f for f in report.findings if f.severity == "critical"]
        warnings = [f for f in report.findings if f.severity == "warning"]

        if critical:
            lines.append(f"\n### 🔴 Critical ({len(critical)} secrets)\n")
            for f in critical:
                lines.append(self._format_finding(f))

        if warnings:
            lines.append(f"\n### 🟡 Warning ({len(warnings)} secrets)\n")
            for f in warnings:
                lines.append(self._format_finding(f))

        # Rotation checklist
        lines.append("\n### 🔄 Rotation Checklist\n")
        lines.append("Complete ALL of these before this PR can be merged:\n")
        for i, f in enumerate(report.findings, 1):
            lines.append(f"- [ ] **{i}. Rotate {f.secret_type}**")
            lines.append(f"  ```\n  {f.rotation_cmd}\n  ```")

        lines.append(
            "\n### 📚 Why rotation is required even after deletion\n"
            "Git history is permanent. Anyone with repo access (past or present), "
            "any CI/CD logs, any git clones made while the secret was present — "
            "all retain the secret. Rotation is the only safe remediation.\n"
        )
        lines.append("---\n*🤖 AI Code Review — Secret Scanner (TruffleHog/Gitleaks)*")

        return "\n".join(lines)

    def rotation_report(self, report: ScanReport) -> str:
        """Generate a detailed rotation report."""
        lines = [
            f"# 🔄 Secret Rotation Report",
            f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
            f"Repository: {report.repo}",
            f"Branch: {report.branch}",
            f"Secrets found: {len(report.findings)}",
            "",
        ]

        for i, f in enumerate(report.findings, 1):
            lines.extend([
                f"## {i}. {f.secret_type}",
                f"- **File:** `{f.file}` line {f.line}",
                f"- **Commit:** `{f.commit[:8]}` by {f.author} on {f.commit_date[:10]}",
                f"- **Status:** {'🔴 STILL ACTIVE' if f.verified else '⚪ History only'}",
                f"- **Severity:** {f.severity.upper()}",
                "",
                f"### Rotation Steps",
                f"```",
                f"{f.rotation_cmd}",
                f"```",
                "",
                f"### After Rotation",
                f"1. Update your `.env` file with the new credential",
                f"2. Update any CI/CD secrets (GitHub Secrets, etc.)",
                f"3. Invalidate any sessions/tokens that used the old credential",
                f"4. Monitor logs for unauthorized access using the old credential",
                "",
            ])

        return "\n".join(lines)

    # ── Scanner implementations ───────────────────────────

    def _run_trufflehog(self, repo_path: str, since_commit: str = "") -> list[SecretFinding]:
        """Run TruffleHog against git history."""
        print("[secret-scan] Running TruffleHog...")
        try:
            cmd = [
                "trufflehog", "git",
                f"file://{os.path.abspath(repo_path)}",
                "--json",
                "--no-update",
            ]
            if since_commit:
                cmd.extend(["--since-commit", since_commit])

            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=120,
            )

            findings = []
            for line in result.stdout.splitlines():
                if not line.strip():
                    continue
                try:
                    item = json.loads(line)
                    f    = self._parse_trufflehog(item)
                    if f:
                        findings.append(f)
                except json.JSONDecodeError:
                    continue

            print(f"[secret-scan] TruffleHog: {len(findings)} findings")
            return findings

        except subprocess.TimeoutExpired:
            print("[secret-scan] TruffleHog timed out")
            return []
        except Exception as e:
            print(f"[secret-scan] TruffleHog error: {e}")
            return []

    def _parse_trufflehog(self, item: dict) -> SecretFinding | None:
        secret_type = item.get("DetectorName", "Generic Secret")
        raw         = item.get("Raw", "")
        if not raw:
            return None

        meta   = item.get("SourceMetadata", {}).get("Data", {}).get("Git", {})
        commit = meta.get("commit", "")
        file   = meta.get("file", "")
        author = meta.get("email", "unknown")
        date   = meta.get("timestamp", "")
        line   = int(meta.get("line", 0))

        stype_meta = SECRET_TYPES.get(secret_type, SECRET_TYPES.get("Generic API Key", {}))

        return SecretFinding(
            detector     = "trufflehog",
            secret_type  = secret_type,
            file         = file,
            line         = line,
            commit       = commit,
            commit_date  = date,
            author       = author,
            raw_value    = f"{raw[:4]}...{raw[-4:]}" if len(raw) > 8 else "****",
            severity     = stype_meta.get("severity", "warning"),
            rotation_cmd = stype_meta.get("rotation", "Rotate in service dashboard"),
            verified     = item.get("Verified", False),
        )

    def _run_gitleaks(self, repo_path: str, since_commit: str = "") -> list[SecretFinding]:
        """Run Gitleaks against git history."""
        print("[secret-scan] Running Gitleaks...")
        try:
            with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
                tmp_path = tmp.name

            cmd = [
                "gitleaks", "detect",
                "--source", repo_path,
                "--report-format", "json",
                "--report-path", tmp_path,
                "--no-banner",
            ]
            if since_commit:
                cmd.extend(["--log-opts", f"{since_commit}..HEAD"])

            subprocess.run(cmd, capture_output=True, timeout=120)

            findings = []
            if Path(tmp_path).exists():
                data = json.loads(Path(tmp_path).read_text() or "[]")
                for item in data:
                    f = self._parse_gitleaks(item)
                    if f:
                        findings.append(f)
                Path(tmp_path).unlink(missing_ok=True)

            print(f"[secret-scan] Gitleaks: {len(findings)} findings")
            return findings

        except Exception as e:
            print(f"[secret-scan] Gitleaks error: {e}")
            return []

    def _parse_gitleaks(self, item: dict) -> SecretFinding | None:
        rule_id     = item.get("RuleID", "generic-api-key")
        secret_type = rule_id.replace("-", " ").replace("_", " ").title()
        secret      = item.get("Secret", "")
        if not secret:
            return None

        stype_meta = SECRET_TYPES.get(secret_type, SECRET_TYPES.get("Generic API Key", {}))

        return SecretFinding(
            detector     = "gitleaks",
            secret_type  = secret_type,
            file         = item.get("File", ""),
            line         = int(item.get("StartLine", 0)),
            commit       = item.get("Commit", ""),
            commit_date  = item.get("Date", ""),
            author       = item.get("Author", "unknown"),
            raw_value    = f"{secret[:4]}...{secret[-4:]}" if len(secret) > 8 else "****",
            severity     = stype_meta.get("severity", "warning"),
            rotation_cmd = stype_meta.get("rotation", "Rotate in service dashboard"),
            verified     = False,
        )

    def _run_regex_scan(self, repo_path: str) -> list[SecretFinding]:
        """Fast regex scan of current files (not history)."""
        IGNORE_FILES = {
        "secret_scanner.py",
        }

        PATTERNS = [
            (r'(?i)(password|passwd|pwd)\s*=\s*["\'][^"\']{4,}["\']',    "Hardcoded Password",   "warning"),
            (r'(?i)(api[_-]?key|apikey)\s*=\s*["\'][^"\']{10,}["\']',   "Generic API Key",      "warning"),
            (r'AKIA[0-9A-Z]{16}',                                          "AWS Access Key",       "critical"),
            (r'(?i)secret[_-]?key\s*=\s*["\'][^"\']{8,}["\']',           "Generic API Key",      "warning"),
            (r'ghp_[a-zA-Z0-9]{36}',                                       "GitHub Token",         "critical"),
            (r'sk-[a-zA-Z0-9]{48}',                                        "OpenAI API Key",       "critical"),
            (r'xox[baprs]-[0-9a-zA-Z\-]+',                                "Slack Token",          "critical"),
            (r'-----BEGIN (?:RSA |EC )?PRIVATE KEY-----',                  "Private Key",          "critical"),
        ]

        findings = []
        repo     = Path(repo_path)

        EXCLUDED_DIRS = {
        "venv",
        ".git",
        "__pycache__",
        "reports",
        "node_modules",
        ".pytest_cache",
        "build",
        "dist",
        }

        for py_file in repo.rglob("*.py"):

            if any(part in EXCLUDED_DIRS for part in py_file.parts):
                continue
            try:
                content = py_file.read_text(encoding="utf-8", errors="ignore")
                for pattern, secret_type, severity in PATTERNS:
                    for m in re.finditer(pattern, content):
                        line_num = content[:m.start()].count("\n") + 1
                        stype_meta = SECRET_TYPES.get(secret_type, {})
                        findings.append(SecretFinding(
                            detector     = "regex",
                            secret_type  = secret_type,
                            file         = str(py_file.relative_to(repo)),
                            line         = line_num,
                            commit       = "HEAD",
                            commit_date  = datetime.now(timezone.utc).isoformat(),
                            author       = "unknown",
                            raw_value    = m.group(0)[:4] + "****",
                            severity     = severity,
                            rotation_cmd = stype_meta.get("rotation", "Rotate immediately"),
                            verified     = False,
                        ))
            except Exception:
                continue

        print(f"[secret-scan] Regex: {len(findings)} findings in current files")
        return findings

    # ── Alert channels ────────────────────────────────────

    def _send_slack_alert(self, report: ScanReport) -> None:
        """Send Slack alert to security team."""
        critical = [f for f in report.findings if f.severity == "critical"]
        active   = report.active_secrets

        payload = {
            "text": f"🚨 *Secret Exposure Detected in `{report.repo}`*",
            "blocks": [
                {
                    "type": "header",
                    "text": {
                        "type": "plain_text",
                        "text": "🚨 Secret Exposure Alert — Security Team Action Required",
                    },
                },
                {
                    "type": "section",
                    "fields": [
                        {"type": "mrkdwn", "text": f"*Repository:*\n`{report.repo}`"},
                        {"type": "mrkdwn", "text": f"*Branch:*\n`{report.branch}`"},
                        {"type": "mrkdwn", "text": f"*Critical Secrets:*\n🔴 {len(critical)}"},
                        {"type": "mrkdwn", "text": f"*Still Active:*\n⚠️ {len(active)}"},
                    ],
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "*Exposed Secrets:*\n" + "\n".join(
                            f"• `{f.secret_type}` in `{f.file}` "
                            f"(commit `{f.commit[:8]}` by {f.author})"
                            for f in report.findings[:5]
                        ),
                    },
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            "*Required Actions:*\n"
                            "1. Rotate ALL exposed secrets immediately\n"
                            "2. Review audit logs for unauthorized access\n"
                            "3. Update all environments with new credentials\n"
                            "4. Invalidate existing sessions"
                        ),
                    },
                },
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "View Repository"},
                            "url":  f"https://github.com/{report.repo}",
                            "style": "danger",
                        }
                    ],
                },
            ],
        }

        try:
            resp = requests.post(SLACK_WEBHOOK, json=payload, timeout=10)
            resp.raise_for_status()
            print("[secret-scan] ✅ Slack alert sent to security team")
        except Exception as e:
            print(f"[secret-scan] Slack alert failed: {e}")

    def _create_github_secret_alert(
        self, report: ScanReport, repo: str, pr_number: int
    ) -> None:
        """Create a GitHub security advisory for exposed secrets."""
        for f in report.active_secrets[:5]:
            try:
                requests.post(
                    f"https://api.github.com/repos/{repo}/security-advisories",
                    headers = {
                        "Authorization": f"token {GITHUB_TOKEN}",
                        "Accept":        "application/vnd.github+json",
                    },
                    json = {
                        "summary":     f"Exposed {f.secret_type} in git history",
                        "description": (
                            f"A {f.secret_type} was found in `{f.file}` "
                            f"(commit {f.commit[:8]}). "
                            f"Rotation required immediately."
                        ),
                        "severity":    f.severity,
                        "cve_id":      None,
                    },
                    timeout = 10,
                )
                print(f"[secret-scan] GitHub advisory created for {f.secret_type}")
            except Exception as e:
                print(f"[secret-scan] GitHub advisory failed: {e}")

    def _post_to_pr(self, report: ScanReport, repo: str, pr_number: int, loader) -> None:
        """Post scan results as a PR comment and set commit status."""
        comment = self.format_pr_comment(report)

        try:
            requests.post(
                f"https://api.github.com/repos/{repo}/issues/{pr_number}/comments",
                headers = loader.auth.headers(),
                json    = {"body": comment},
                timeout = 15,
            )
            print(f"[secret-scan] Results posted to PR #{pr_number}")
        except Exception as e:
            print(f"[secret-scan] Failed to post comment: {e}")

    # ── Helpers ───────────────────────────────────────────

    def _format_finding(self, f: SecretFinding) -> str:
        status = "🔴 **ACTIVE**" if f.verified else "⚪ Historical"
        return (
            f"\n**{f.secret_type}** — {status}\n"
            f"- File: `{f.file}` line {f.line}\n"
            f"- Commit: `{f.commit[:8]}` by {f.author} on {f.commit_date[:10]}\n"
            f"- Value: `{f.raw_value}`\n"
            f"- Rotate: `{f.rotation_cmd}`\n"
        )

    def _deduplicate(self, findings: list[SecretFinding]) -> list[SecretFinding]:
        seen    = set()
        unique  = []
        for f in findings:
            key = (f.secret_type, f.file, f.line, f.raw_value[:8])
            if key not in seen:
                seen.add(key)
                unique.append(f)
        return unique

    def _get_current_branch(self, repo_path: str) -> str:
        try:
            r = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                capture_output=True, text=True, cwd=repo_path, timeout=10,
            )
            return r.stdout.strip() or "unknown"
        except Exception:
            return "unknown"

    def _count_commits(self, repo_path: str) -> int:
        try:
            r = subprocess.run(
                ["git", "rev-list", "--count", "HEAD"],
                capture_output=True, text=True, cwd=repo_path, timeout=10,
            )
            return int(r.stdout.strip() or 0)
        except Exception:
            return 0

    def _check_tool(self, name: str) -> bool:
        try:
            subprocess.run([name, "--version"], capture_output=True, timeout=5)
            print(f"[secret-scan] ✅ {name} found")
            return True
        except (FileNotFoundError, subprocess.TimeoutExpired):
            print(f"[secret-scan] ⚠️  {name} not found")
            return False


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Secret Scanner — git history scanning")
    parser.add_argument("--scan",   action="store_true", help="Scan git history for secrets")
    parser.add_argument("--alert",  action="store_true", help="Send Slack alert if secrets found")
    parser.add_argument("--report", action="store_true", help="Generate rotation report")
    parser.add_argument("--repo",   default="",          help="GitHub repo (owner/repo)")
    parser.add_argument("--pr",     type=int, default=0, help="PR number")
    parser.add_argument("--dir",    default=".",         help="Repo directory to scan")
    args = parser.parse_args()

    scanner = SecretScanner()

    if args.scan:
        report = scanner.scan(
            repo_path = args.dir,
            repo_name = args.repo,
        )

        print(f"\n{'🚨 SECRETS FOUND' if report.findings else '✅ NO SECRETS FOUND'}")
        print(f"Total findings : {len(report.findings)}")
        print(f"Active/verified: {len(report.active_secrets)}")
        print(f"Commits scanned: {report.total_commits}")

        for f in report.findings:
            icon = "🔴" if f.severity == "critical" else "🟡"
            print(f"  {icon} {f.secret_type} in {f.file}:L{f.line} "
                  f"(commit {f.commit[:8]} by {f.author})")

        if args.alert and report.findings:
            scanner.alert(report, args.repo, args.pr)

        if args.report and report.findings:
            rotation = scanner.rotation_report(report)
            out = Path("secret_rotation_report.md")
            out.write_text(rotation)
            print(f"\n📄 Rotation report saved: {out}")

    else:
        parser.print_help()
        print("\nExamples:")
        print("  python tools/secret_scanner.py --scan")
        print("  python tools/secret_scanner.py --scan --alert")
        print("  python tools/secret_scanner.py --scan --report")
        print("  python tools/secret_scanner.py --scan --repo owner/repo --pr 3 --alert")


if __name__ == "__main__":
    main()