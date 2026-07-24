"""
notifications/notifier.py

Feature 7: Slack + Email Notifications
Sends alerts when critical findings are detected.

Setup in .env:
  SLACK_WEBHOOK_URL=https://hooks.slack.com/services/xxx/yyy/zzz
  NOTIFY_EMAIL=team@company.com
  SMTP_HOST=smtp.gmail.com
  SMTP_PORT=587
  SMTP_USER=your@gmail.com
  SMTP_PASS=your_app_password
"""
from __future__ import annotations

import html
import smtplib
import requests
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from config import cfg

_MAX_FINDINGS_SHOWN = 5
_MAX_MESSAGE_CHARS = 150
_MAX_FIX_CHARS = 100
_MAX_CVES_SHOWN = 3


class Notifier:

    def __init__(self):
        self.slack_webhook = cfg.slack_webhook_url
        self.notify_email  = cfg.notify_email
        self.smtp_host     = cfg.smtp_host
        self.smtp_port     = cfg.smtp_port
        self.smtp_user     = cfg.smtp_user
        self.smtp_pass     = cfg.smtp_pass

    def notify(
        self,
        report: dict,
        repo: str,
        pr_number: int,
        comparison: dict | None = None,
    ) -> None:
        """
        Send notifications if critical findings exist.

        `comparison` (from IncrementalAgent.compare_reviews, when this PR
        has been reviewed before) narrows the alert to only critical
        findings that are new since the last run — without it, every
        re-run of the same PR re-sends an alert for the same unresolved
        findings, which is noisy when iterating on a PR over several
        commits.
        """
        critical = [
            f for f in report.get("findings", [])
            if f.get("severity") == "critical"
        ]

        if not critical:
            print("[notifier] No critical findings — notifications skipped")
            return

        if comparison is not None:
            from agents.incremental_agent import fingerprint

            new_fingerprints = {
                fingerprint(f) for f in comparison.get("new_issues", [])
                if f.get("severity") == "critical"
            }
            still_new = [f for f in critical if fingerprint(f) in new_fingerprints]
            if not still_new:
                print("[notifier] All critical findings already notified in a "
                      "previous run — skipping (no new critical findings)")
                return
            critical = still_new

        pr_url = f"https://github.com/{repo}/pull/{pr_number}"
        score  = report.get("overall_score", 0)

        print(f"[notifier] {len(critical)} critical findings — sending alerts")

        if self.slack_webhook:
            self._send_slack(critical, repo, pr_number, pr_url, score)

        if self.notify_email and self.smtp_user:
            self._send_email(critical, repo, pr_number, pr_url, score)

        if not self.slack_webhook and not (self.notify_email and self.smtp_user):
            print("[notifier] No notification channels configured.")
            print("[notifier] Add SLACK_WEBHOOK_URL or SMTP settings to .env")

    # ── Slack ─────────────────────────────────────────────

    def _send_slack(
        self,
        critical: list[dict],
        repo: str,
        pr_number: int,
        pr_url: str,
        score: float,
    ) -> None:
        findings_text = "\n".join(
            f"• `{_slack_escape(f.get('file', '?'))}:L{f.get('line', 0)}` — "
            f"{_slack_escape(f.get('message', '')[:_MAX_MESSAGE_CHARS])}"
            for f in critical[:_MAX_FINDINGS_SHOWN]
        )

        cve_text = ""
        for f in critical:
            if f.get("cve_ids"):
                cve_text += f"\n🔗 CVEs: {', '.join(f['cve_ids'][:_MAX_CVES_SHOWN])}"

        payload = {
            "text": "🚨 Critical Security Issues Found in PR",
            "blocks": [
                {
                    "type": "header",
                    "text": {
                        "type": "plain_text",
                        "text": "🚨 AI Code Review — Critical Issues Found",
                    },
                },
                {
                    "type": "section",
                    "fields": [
                        {"type": "mrkdwn", "text": f"*Repository:*\n`{_slack_escape(repo)}`"},
                        {"type": "mrkdwn", "text": f"*PR Number:*\n<{pr_url}|PR #{pr_number}>"},
                        {"type": "mrkdwn", "text": f"*Review Score:*\n{score:.2f} / 1.0"},
                        {"type": "mrkdwn", "text": f"*Critical Issues:*\n{len(critical)}"},
                    ],
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*Critical Findings:*\n{findings_text}{cve_text}",
                    },
                },
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "View PR"},
                            "url": pr_url,
                            "style": "danger",
                        }
                    ],
                },
            ],
        }

        try:
            resp = requests.post(
                self.slack_webhook,
                json=payload,
                timeout=10,
            )
            resp.raise_for_status()
            print("[notifier] Slack alert sent ✅")
        except Exception as e:
            print(f"[notifier] Slack failed: {e}")

    # ── Email ─────────────────────────────────────────────

    def _send_email(
        self,
        critical: list[dict],
        repo: str,
        pr_number: int,
        pr_url: str,
        score: float,
    ) -> None:
        subject = f"🚨 [{repo}] PR #{pr_number} — {len(critical)} Critical Security Issues"

        findings_html = "".join(
            f"""
            <tr>
                <td style="padding:8px;border:1px solid #ddd;color:#c00;">
                    {html.escape(f.get('file', '?'))}:L{f.get('line', 0)}
                </td>
                <td style="padding:8px;border:1px solid #ddd;">
                    {html.escape(f.get('message', '')[:_MAX_MESSAGE_CHARS])}
                </td>
                <td style="padding:8px;border:1px solid #ddd;font-family:monospace;font-size:12px;">
                    {html.escape(f.get('fix', '')[:_MAX_FIX_CHARS])}
                </td>
                <td style="padding:8px;border:1px solid #ddd;">
                    {html.escape(', '.join(f.get('cve_ids', [])) or '—')}
                </td>
            </tr>
            """
            for f in critical
        )

        html_body = f"""
        <html><body style="font-family:sans-serif;max-width:800px;margin:0 auto;">
        <h2 style="color:#c00;">🚨 Critical Security Issues Found</h2>
        <table style="border-collapse:collapse;width:100%;margin-bottom:16px;">
            <tr>
                <td><strong>Repository:</strong></td><td>{html.escape(repo)}</td>
            </tr>
            <tr>
                <td><strong>Pull Request:</strong></td>
                <td><a href="{pr_url}">PR #{pr_number}</a></td>
            </tr>
            <tr>
                <td><strong>Review Score:</strong></td>
                <td>{score:.2f} / 1.0</td>
            </tr>
        </table>

        <h3>Critical Findings</h3>
        <table style="border-collapse:collapse;width:100%;">
            <tr style="background:#f5f5f5;">
                <th style="padding:8px;border:1px solid #ddd;text-align:left;">Location</th>
                <th style="padding:8px;border:1px solid #ddd;text-align:left;">Issue</th>
                <th style="padding:8px;border:1px solid #ddd;text-align:left;">Fix</th>
                <th style="padding:8px;border:1px solid #ddd;text-align:left;">CVEs</th>
            </tr>
            {findings_html}
        </table>

        <p style="margin-top:24px;">
            <a href="{pr_url}"
               style="background:#c00;color:white;padding:10px 20px;
                      text-decoration:none;border-radius:4px;">
                View PR on GitHub
            </a>
        </p>
        </body></html>
        """

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = self.smtp_user
        msg["To"]      = self.notify_email
        msg.attach(MIMEText(html_body, "html"))

        try:
            with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
                server.starttls()
                server.login(self.smtp_user, self.smtp_pass)
                server.sendmail(self.smtp_user, self.notify_email, msg.as_string())
            print(f"[notifier] Email sent to {self.notify_email} ✅")
        except Exception as e:
            print(f"[notifier] Email failed: {e}")


def _slack_escape(text: str) -> str:
    """Slack mrkdwn requires &, <, > to be escaped or formatting breaks —
    see https://api.slack.com/reference/surfaces/formatting#escaping"""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
