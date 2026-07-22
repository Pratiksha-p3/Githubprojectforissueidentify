"""
tools/skill_profiler.py

Tier 2 — Feature 5: Developer Skill Profiling

Tracks each developer's mistakes over time and generates:
  - Personalised growth reports
  - Weekly email digest
  - Skill gap analysis
  - Tutorial recommendations

Usage:
  # Record findings after each review
  from tools.skill_profiler import SkillProfiler
  profiler = SkillProfiler()
  profiler.record(author="pratiksha", findings=report["findings"], pr_number=3)

  # Generate growth report for a developer
  profiler.growth_report("pratiksha")

  # Send weekly digest to all developers
  profiler.send_weekly_digest()

  # CLI
  python tools/skill_profiler.py --report pratiksha
  python tools/skill_profiler.py --digest
  python tools/skill_profiler.py --all
"""
from __future__ import annotations

import json
import os
import smtplib
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

PROFILES_PATH = Path("./vectordb/skill_profiles.json")

# ── Tutorial links per issue category ────────────────────
TUTORIALS = {
    "sql_injection": {
        "title":    "SQL Injection Prevention",
        "url":      "https://owasp.org/www-community/attacks/SQL_Injection",
        "summary":  "Use parameterized queries. Never concatenate user input into SQL strings.",
        "example":  "cursor.execute('SELECT * FROM users WHERE id=?', (user_id,))",
    },
    "hardcoded_secret": {
        "title":    "Secrets Management",
        "url":      "https://12factor.net/config",
        "summary":  "Store secrets in environment variables or a secrets manager. Never in code.",
        "example":  "API_KEY = os.getenv('API_KEY')",
    },
    "weak_crypto": {
        "title":    "Cryptography Best Practices",
        "url":      "https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html",
        "summary":  "Use bcrypt, scrypt, or argon2 for passwords. MD5/SHA1 are broken.",
        "example":  "import bcrypt; bcrypt.hashpw(pwd.encode(), bcrypt.gensalt())",
    },
    "command_injection": {
        "title":    "Command Injection Prevention",
        "url":      "https://owasp.org/www-community/attacks/Command_Injection",
        "summary":  "Never pass user input to os.system(). Use subprocess with shell=False.",
        "example":  "subprocess.run(['ping', host], capture_output=True, check=True)",
    },
    "eval_usage": {
        "title":    "Avoiding eval()",
        "url":      "https://realpython.com/python-eval-function/#minimizing-the-security-issues",
        "summary":  "eval() executes arbitrary code. Use ast.literal_eval() for safe evaluation.",
        "example":  "import ast; ast.literal_eval(user_input)",
    },
    "complexity": {
        "title":    "Clean Code & Refactoring",
        "url":      "https://refactoring.guru/refactoring",
        "summary":  "Functions should do ONE thing. Target cyclomatic complexity ≤ 10.",
        "example":  "Extract complex logic into well-named helper functions.",
    },
    "missing_tests": {
        "title":    "Test-Driven Development",
        "url":      "https://www.obeythetestinggoat.com/",
        "summary":  "Write tests before code. Aim for >80% coverage on critical paths.",
        "example":  "def test_login_rejects_empty_password(): assert login('', '') == False",
    },
    "error_handling": {
        "title":    "Python Exception Handling",
        "url":      "https://realpython.com/python-exceptions/",
        "summary":  "Never use bare except. Always catch specific exceptions.",
        "example":  "except ValueError as e: logger.error(f'Invalid input: {e}')",
    },
    "performance": {
        "title":    "Python Performance Optimization",
        "url":      "https://wiki.python.org/moin/PythonSpeed/PerformanceTips",
        "summary":  "Avoid N+1 queries. Use list comprehensions. Profile before optimizing.",
        "example":  "users = User.objects.select_related('profile').all()  # not N+1",
    },
}

# Maps finding message keywords → tutorial keys
KEYWORD_MAP = {
    "sql injection":        "sql_injection",
    "sql_injection":        "sql_injection",
    "f\"select":            "sql_injection",
    "f'select":             "sql_injection",
    "hardcoded":            "hardcoded_secret",
    "api_key":              "hardcoded_secret",
    "password":             "hardcoded_secret",
    "secret":               "hardcoded_secret",
    "md5":                  "weak_crypto",
    "sha1":                 "weak_crypto",
    "weak hash":            "weak_crypto",
    "os.system":            "command_injection",
    "command injection":    "command_injection",
    "shell=true":           "command_injection",
    "eval(":                "eval_usage",
    "complexity":           "complexity",
    "cyclomatic":           "complexity",
    "test coverage":        "missing_tests",
    "missing test":         "missing_tests",
    "bare except":          "error_handling",
    "except:":              "error_handling",
    "n+1":                  "performance",
    "unnecessary loop":     "performance",
}


@dataclass
class PRRecord:
    pr_number:  int
    repo:       str
    date:       str
    findings:   list[dict]
    score:      float


@dataclass
class DeveloperProfile:
    author:        str
    email:         str = ""
    pr_history:    list[PRRecord] = field(default_factory=list)
    issue_counts:  dict = field(default_factory=lambda: defaultdict(int))
    monthly_counts: dict = field(default_factory=lambda: defaultdict(int))
    total_prs:     int = 0
    total_critical: int = 0


class SkillProfiler:

    def __init__(self):
        self._profiles: dict[str, DeveloperProfile] = {}
        self._load()

    # ── Public API ────────────────────────────────────────

    def record(
        self,
        author:    str,
        findings:  list[dict],
        pr_number: int,
        repo:      str = "",
        score:     float = 1.0,
        email:     str = "",
    ) -> None:
        """Record a developer's PR findings into their profile."""
        if author not in self._profiles:
            self._profiles[author] = DeveloperProfile(author=author)

        profile = self._profiles[author]

        if email and not profile.email:
            profile.email = email

        month = datetime.now(timezone.utc).strftime("%Y-%m")

        pr_record = PRRecord(
            pr_number = pr_number,
            repo      = repo,
            date      = datetime.now(timezone.utc).isoformat(),
            findings  = findings,
            score     = score,
        )
        profile.pr_history.append(pr_record)
        profile.total_prs += 1

        for f in findings:
            if f.get("severity") == "critical":
                profile.total_critical += 1

            # Map finding to tutorial category
            category = self._categorize(f)
            if category:
                profile.issue_counts[category] = \
                    profile.issue_counts.get(category, 0) + 1
                key = f"{month}:{category}"
                profile.monthly_counts[key] = \
                    profile.monthly_counts.get(key, 0) + 1

        self._save()
        print(f"[skill-profiler] Recorded {len(findings)} findings for {author}")

    def growth_report(self, author: str) -> str:
        """Generate a personalised growth report for a developer."""
        if author not in self._profiles:
            return f"No data found for developer: {author}"

        profile = self._profiles[author]
        report  = self._build_growth_report(profile)
        print(report)
        return report

    def send_weekly_digest(self, smtp_config: dict = None) -> None:
        """Send weekly digest emails to all developers with email addresses."""
        smtp = smtp_config or {
            "host":     os.getenv("SMTP_HOST", "smtp.gmail.com"),
            "port":     int(os.getenv("SMTP_PORT", "587")),
            "user":     os.getenv("SMTP_USER", ""),
            "password": os.getenv("SMTP_PASS", ""),
        }

        sent = 0
        for author, profile in self._profiles.items():
            if not profile.email:
                print(f"[skill-profiler] No email for {author} — skipping")
                continue

            report_text = self._build_growth_report(profile)
            html        = self._to_html(profile, report_text)

            success = self._send_email(
                to      = profile.email,
                subject = f"📊 Weekly Code Review Growth Report — {author}",
                html    = html,
                smtp    = smtp,
            )
            if success:
                sent += 1
                print(f"[skill-profiler] ✅ Digest sent to {author} ({profile.email})")
            else:
                print(f"[skill-profiler] ❌ Failed to send to {author}")

        print(f"[skill-profiler] Weekly digest: {sent}/{len(self._profiles)} sent")

    def all_profiles_summary(self) -> str:
        """Print a summary table of all developers."""
        if not self._profiles:
            return "No developer profiles yet."

        lines = ["## 👥 Developer Profiles\n"]
        lines.append(
            "| Developer | PRs | Critical | Top Issue | Trend |\n"
            "|-----------|-----|----------|-----------|-------|\n"
        )

        for author, profile in sorted(
            self._profiles.items(),
            key=lambda x: x[1].total_critical,
            reverse=True,
        ):
            top_issue = self._top_issue(profile)
            trend     = self._trend(profile)
            lines.append(
                f"| {author} | {profile.total_prs} | "
                f"{profile.total_critical} | {top_issue} | {trend} |"
            )

        return "\n".join(lines)

    # ── Growth report builder ─────────────────────────────

    def _build_growth_report(self, profile: DeveloperProfile) -> str:
        now        = datetime.now(timezone.utc)
        this_month = now.strftime("%Y-%m")
        last_month = (now.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")

        # This month's issues
        this_issues: dict[str, int] = {}
        last_issues: dict[str, int] = {}
        for key, count in profile.monthly_counts.items():
            month, cat = key.split(":", 1)
            if month == this_month:
                this_issues[cat] = count
            elif month == last_month:
                last_issues[cat] = count

        # Overall top issues
        top_issues = sorted(
            profile.issue_counts.items(),
            key=lambda x: x[1],
            reverse=True,
        )[:5]

        lines = [
            f"# 📈 Growth Report — {profile.author}",
            f"Generated: {now.strftime('%Y-%m-%d')}",
            "",
            "## 📊 Overview",
            f"- **Total PRs reviewed:** {profile.total_prs}",
            f"- **Total critical issues:** {profile.total_critical}",
            f"- **Average issues per PR:** "
            f"{profile.total_critical / max(profile.total_prs, 1):.1f}",
            "",
        ]

        # Repeated mistakes
        if top_issues:
            lines.append("## 🔁 Repeated Mistakes (All Time)")
            for cat, count in top_issues:
                tutorial = TUTORIALS.get(cat, {})
                icon = "🔴" if count >= 5 else "🟡" if count >= 2 else "🔵"
                lines.append(f"\n### {icon} {cat.replace('_', ' ').title()} — {count}x")
                if count >= 2:
                    lines.append(
                        f"You've introduced this pattern **{count} times**. "
                        "This needs your attention."
                    )
                if tutorial:
                    lines.append(f"\n**Fix:** {tutorial.get('summary', '')}")
                    lines.append(f"\n```python\n{tutorial.get('example', '')}\n```")
                    lines.append(f"\n📚 Learn more: {tutorial.get('url', '')}")

        # This month vs last month comparison
        if this_issues or last_issues:
            lines.append("\n## 📅 This Month vs Last Month")
            all_cats = set(this_issues) | set(last_issues)
            for cat in sorted(all_cats):
                this = this_issues.get(cat, 0)
                last = last_issues.get(cat, 0)
                if this > last:
                    trend = f"📈 +{this-last} (getting worse)"
                elif this < last:
                    trend = f"📉 -{last-this} (improving!)"
                else:
                    trend = "➡️ same"
                lines.append(
                    f"- **{cat.replace('_', ' ').title()}**: "
                    f"{last} → {this}  {trend}"
                )

        # Recommendations
        lines.append("\n## 🎯 This Week's Focus")
        if top_issues:
            top_cat  = top_issues[0][0]
            top_tut  = TUTORIALS.get(top_cat, {})
            top_count = top_issues[0][1]
            lines.append(
                f"Your most frequent issue is **{top_cat.replace('_',' ')}** "
                f"({top_count}x). Focus here first."
            )
            if top_tut:
                lines.append(f"\n**Action:** {top_tut.get('summary','')}")
                lines.append(f"\n**Tutorial:** {top_tut.get('url','')}")
        else:
            lines.append("No recurring issues found. Keep it up! ✅")

        # Score trend
        if len(profile.pr_history) >= 2:
            recent_scores = [p.score for p in profile.pr_history[-5:]]
            avg_recent    = sum(recent_scores) / len(recent_scores)
            older_scores  = [p.score for p in profile.pr_history[:-5]]
            avg_older     = (
                sum(older_scores) / len(older_scores) if older_scores else avg_recent
            )
            lines.append(f"\n## 📈 Score Trend")
            lines.append(
                f"Recent avg: **{avg_recent:.2f}** vs Earlier avg: **{avg_older:.2f}**"
            )
            if avg_recent > avg_older:
                lines.append("✅ Your code quality is **improving!**")
            elif avg_recent < avg_older:
                lines.append("⚠️ Your code quality is **declining** — review the tutorials above.")
            else:
                lines.append("➡️ Your code quality is **consistent**.")

        return "\n".join(lines)

    # ── Email ─────────────────────────────────────────────

    def _to_html(self, profile: DeveloperProfile, report_text: str) -> str:
        """Convert markdown report to HTML email."""
        rows = ""
        for cat, count in sorted(
            profile.issue_counts.items(), key=lambda x: x[1], reverse=True
        )[:5]:
            tut   = TUTORIALS.get(cat, {})
            color = "#ef4444" if count >= 5 else "#f59e0b" if count >= 2 else "#3b82f6"
            rows += f"""
            <tr>
                <td style="padding:8px;border:1px solid #e2e8f0">
                    {cat.replace('_',' ').title()}
                </td>
                <td style="padding:8px;border:1px solid #e2e8f0;
                           color:{color};font-weight:bold;text-align:center">
                    {count}x
                </td>
                <td style="padding:8px;border:1px solid #e2e8f0">
                    <a href="{tut.get('url','#')}" style="color:#3b82f6">
                        {tut.get('title','Learn more')}
                    </a>
                </td>
            </tr>"""

        return f"""
        <html><body style="font-family:sans-serif;max-width:700px;margin:0 auto;color:#1e293b">
        <div style="background:#1e293b;padding:24px;border-radius:12px 12px 0 0">
            <h1 style="color:white;margin:0">📊 Weekly Growth Report</h1>
            <p style="color:#94a3b8;margin:8px 0 0">
                Developer: <strong style="color:white">{profile.author}</strong> ·
                {datetime.now(timezone.utc).strftime('%B %d, %Y')}
            </p>
        </div>

        <div style="padding:24px;background:#f8fafc;border-radius:0 0 12px 12px">

            <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:16px;margin-bottom:24px">
                <div style="background:white;padding:16px;border-radius:8px;
                            border:1px solid #e2e8f0;text-align:center">
                    <div style="font-size:32px;font-weight:700;color:#1e293b">
                        {profile.total_prs}
                    </div>
                    <div style="font-size:12px;color:#64748b">PRs Reviewed</div>
                </div>
                <div style="background:white;padding:16px;border-radius:8px;
                            border:1px solid #e2e8f0;text-align:center">
                    <div style="font-size:32px;font-weight:700;color:#ef4444">
                        {profile.total_critical}
                    </div>
                    <div style="font-size:12px;color:#64748b">Critical Issues</div>
                </div>
                <div style="background:white;padding:16px;border-radius:8px;
                            border:1px solid #e2e8f0;text-align:center">
                    <div style="font-size:32px;font-weight:700;color:#3b82f6">
                        {len(profile.issue_counts)}
                    </div>
                    <div style="font-size:12px;color:#64748b">Issue Types</div>
                </div>
            </div>

            <h2 style="color:#1e293b">🔁 Repeated Mistakes</h2>
            <table style="width:100%;border-collapse:collapse;margin-bottom:24px">
                <thead>
                    <tr style="background:#e2e8f0">
                        <th style="padding:8px;border:1px solid #e2e8f0;text-align:left">
                            Issue
                        </th>
                        <th style="padding:8px;border:1px solid #e2e8f0">Count</th>
                        <th style="padding:8px;border:1px solid #e2e8f0;text-align:left">
                            Tutorial
                        </th>
                    </tr>
                </thead>
                <tbody>{rows}</tbody>
            </table>

            <div style="background:#eff6ff;border:1px solid #bfdbfe;
                        border-radius:8px;padding:16px;margin-bottom:24px">
                <h3 style="margin:0 0 8px;color:#1e40af">🎯 This Week's Focus</h3>
                <p style="margin:0;color:#1e3a8a">
                    {self._top_recommendation(profile)}
                </p>
            </div>

        </div>

        <p style="text-align:center;color:#94a3b8;font-size:12px;margin-top:16px">
            🤖 AI Code Review · Auto-generated weekly digest
        </p>
        </body></html>"""

    def _send_email(self, to: str, subject: str, html: str, smtp: dict) -> bool:
        if not smtp.get("user") or not smtp.get("password"):
            print(f"[skill-profiler] SMTP not configured — add SMTP_USER/SMTP_PASS to .env")
            return False
        try:
            msg             = MIMEMultipart("alternative")
            msg["Subject"]  = subject
            msg["From"]     = smtp["user"]
            msg["To"]       = to
            msg.attach(MIMEText(html, "html"))

            with smtplib.SMTP(smtp["host"], smtp["port"]) as server:
                server.starttls()
                server.login(smtp["user"], smtp["password"])
                server.sendmail(smtp["user"], to, msg.as_string())
            return True
        except Exception as e:
            print(f"[skill-profiler] Email error: {e}")
            return False

    # ── Helpers ───────────────────────────────────────────

    def _categorize(self, finding: dict) -> str | None:
        text = (
            (finding.get("message") or "") + " " +
            (finding.get("category") or "") + " " +
            (finding.get("rule_id") or "")
        ).lower()

        for keyword, category in KEYWORD_MAP.items():
            if keyword in text:
                return category
        return finding.get("category") or None

    def _top_issue(self, profile: DeveloperProfile) -> str:
        if not profile.issue_counts:
            return "none"
        return max(profile.issue_counts, key=profile.issue_counts.get)\
               .replace("_", " ")

    def _trend(self, profile: DeveloperProfile) -> str:
        if len(profile.pr_history) < 2:
            return "➡️ new"
        recent = [p.score for p in profile.pr_history[-3:]]
        older  = [p.score for p in profile.pr_history[:-3]]
        if not older:
            return "➡️ new"
        if sum(recent)/len(recent) > sum(older)/len(older):
            return "📈 improving"
        elif sum(recent)/len(recent) < sum(older)/len(older):
            return "📉 declining"
        return "➡️ stable"

    def _top_recommendation(self, profile: DeveloperProfile) -> str:
        if not profile.issue_counts:
            return "No recurring issues found. Keep it up! ✅"
        top_cat = max(profile.issue_counts, key=profile.issue_counts.get)
        count   = profile.issue_counts[top_cat]
        tut     = TUTORIALS.get(top_cat, {})
        return (
            f"You've introduced <strong>{top_cat.replace('_',' ')}</strong> "
            f"{count} times. {tut.get('summary','')} "
            f"<a href='{tut.get('url','#')}'>Read more →</a>"
        )

    # ── Persistence ───────────────────────────────────────

    def _save(self) -> None:
        PROFILES_PATH.parent.mkdir(parents=True, exist_ok=True)
        data = {}
        for author, p in self._profiles.items():
            data[author] = {
                "author":         p.author,
                "email":          p.email,
                "total_prs":      p.total_prs,
                "total_critical": p.total_critical,
                "issue_counts":   dict(p.issue_counts),
                "monthly_counts": dict(p.monthly_counts),
                "pr_history": [
                    {
                        "pr_number": r.pr_number,
                        "repo":      r.repo,
                        "date":      r.date,
                        "score":     r.score,
                        "findings":  r.findings[:5],  # keep small
                    }
                    for r in p.pr_history[-50:]  # last 50 PRs only
                ],
            }
        PROFILES_PATH.write_text(json.dumps(data, indent=2))

    def _load(self) -> None:
        if not PROFILES_PATH.exists():
            return
        try:
            data = json.loads(PROFILES_PATH.read_text())
            for author, d in data.items():
                profile = DeveloperProfile(
                    author         = d["author"],
                    email          = d.get("email", ""),
                    total_prs      = d.get("total_prs", 0),
                    total_critical = d.get("total_critical", 0),
                    issue_counts   = defaultdict(int, d.get("issue_counts", {})),
                    monthly_counts = defaultdict(int, d.get("monthly_counts", {})),
                )
                for r in d.get("pr_history", []):
                    profile.pr_history.append(PRRecord(
                        pr_number = r["pr_number"],
                        repo      = r["repo"],
                        date      = r["date"],
                        score     = r["score"],
                        findings  = r.get("findings", []),
                    ))
                self._profiles[author] = profile
        except Exception as e:
            print(f"[skill-profiler] Could not load profiles: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="Developer Skill Profiler"
    )
    parser.add_argument("--report",  type=str, help="Growth report for a developer")
    parser.add_argument("--digest",  action="store_true", help="Send weekly email digest")
    parser.add_argument("--all",     action="store_true", help="Show all developer profiles")
    parser.add_argument("--email",   type=str, default="",
                        help="Set email for a developer (use with --report)")
    args = parser.parse_args()

    profiler = SkillProfiler()

    if args.report:
        if args.email and args.report in profiler._profiles:
            profiler._profiles[args.report].email = args.email
            profiler._save()
        profiler.growth_report(args.report)

    elif args.digest:
        profiler.send_weekly_digest()

    elif args.all:
        print(profiler.all_profiles_summary())

    else:
        parser.print_help()
        print("\nExamples:")
        print("  python tools/skill_profiler.py --all")
        print("  python tools/skill_profiler.py --report pratiksha")
        print("  python tools/skill_profiler.py --report pratiksha --email dev@company.com")
        print("  python tools/skill_profiler.py --digest")


if __name__ == "__main__":
    main()