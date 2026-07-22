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

import os
import json
import smtplib
import requests
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv

load_dotenv()

SLACK_WEBHOOK  = os.getenv("SLACK_WEBHOOK_URL", "")
NOTIFY_EMAIL   = os.getenv("NOTIFY_EMAIL", "")
SMTP_HOST      = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT      = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER      = os.getenv("SMTP_USER", "")
SMTP_PASS      = os.getenv("SMTP_PASS", "")


class Notifier:

    def notify(self, report: dict, repo: str, pr_number: int) -> None:
        """Send notifications if critical findings exist."""
        critical = [
            f for f in report.get("findings", [])
            if f.get("severity") == "critical"
        ]

        if not critical:
            print("[notifier] No critical findings — notifications skipped")
            return

        pr_url = f"https://github.com/{repo}/pull/{pr_number}"
        score  = report.get("overall_score", 0)

        print(f"[notifier] {len(critical)} critical findings — sending alerts")

        if SLACK_WEBHOOK:
            self._send_slack(critical, repo, pr_number, pr_url, score)

        if NOTIFY_EMAIL and SMTP_USER:
            self._send_email(critical, repo, pr_number, pr_url, score)

        if not SLACK_WEBHOOK and not (NOTIFY_EMAIL and SMTP_USER):
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
            f"• `{f.get('file','?')}:L{f.get('line',0)}` — {f.get('message','')[:100]}"
            for f in critical[:5]
        )

        cve_text = ""
        for f in critical:
            if f.get("cve_ids"):
                cve_text += f"\n🔗 CVEs: {', '.join(f['cve_ids'][:3])}"

        payload = {
            "text": f"🚨 *Critical Security Issues Found in PR*",
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
                        {"type": "mrkdwn", "text": f"*Repository:*\n`{repo}`"},
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
                SLACK_WEBHOOK,
                json=payload,
                timeout=10,
            )
            resp.raise_for_status()
            print(f"[notifier] Slack alert sent ✅")
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
                    {f.get('file','?')}:L{f.get('line',0)}
                </td>
                <td style="padding:8px;border:1px solid #ddd;">{f.get('message','')[:150]}</td>
                <td style="padding:8px;border:1px solid #ddd;font-family:monospace;font-size:12px;">
                    {f.get('fix','')[:100]}
                </td>
                <td style="padding:8px;border:1px solid #ddd;">
                    {', '.join(f.get('cve_ids', [])) or '—'}
                </td>
            </tr>
            """
            for f in critical
        )

        html = f"""
        <html><body style="font-family:sans-serif;max-width:800px;margin:0 auto;">
        <h2 style="color:#c00;">🚨 Critical Security Issues Found</h2>
        <table style="border-collapse:collapse;width:100%;margin-bottom:16px;">
            <tr>
                <td><strong>Repository:</strong></td><td>{repo}</td>
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
        msg["From"]    = SMTP_USER
        msg["To"]      = NOTIFY_EMAIL
        msg.attach(MIMEText(html, "html"))

        try:
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
                server.starttls()
                server.login(SMTP_USER, SMTP_PASS)
                server.sendmail(SMTP_USER, NOTIFY_EMAIL, msg.as_string())
            print(f"[notifier] Email sent to {NOTIFY_EMAIL} ✅")
        except Exception as e:
            print(f"[notifier] Email failed: {e}")
            