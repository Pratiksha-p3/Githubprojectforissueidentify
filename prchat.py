"""
pr_chat.py
Ask questions about a PR and get answers using Groq.

Usage:
    python pr_chat.py --mock
    python pr_chat.py --report reports/review_demo_repo_pr1_20240101.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from groq import Groq
from dotenv import load_dotenv
import os

load_dotenv()


# ─────────────────────────────────────────────────────────────────────────────
# LOAD PR CONTEXT
# ─────────────────────────────────────────────────────────────────────────────

def load_pr_context_from_mock() -> dict:
    """Use the same mock PR data as --mock mode."""
    return {
        "pr_title": "Add authentication",
        "repo": "demo/repo",
        "pr_number": 1,
        "files": [
            {
                "filename": "src/auth/login.py",
                "patch": (
                    "@@ -10,8 +10,22 @@\n"
                    " import sqlite3\n"
                    "+SECRET_KEY = 'hardcoded-secret-abc123'\n"
                    "+def login(username, password):\n"
                    "+    conn = sqlite3.connect('users.db')\n"
                    "+    query = f\"SELECT * FROM users WHERE name='{username}'\"\n"
                    "+    result = conn.execute(query)\n"
                    "+    return result.fetchone()\n"
                ),
                "full_content": (
                    "import sqlite3\n\n"
                    "SECRET_KEY = 'hardcoded-secret-abc123'\n\n"
                    "def login(username, password):\n"
                    "    conn = sqlite3.connect('users.db')\n"
                    "    query = f\"SELECT * FROM users WHERE name='{username}'\"\n"
                    "    result = conn.execute(query)\n"
                    "    return result.fetchone()\n\n"
                    "def logout(user_id): pass\n"
                ),
                "language": "python",
                "additions": 14,
                "deletions": 2,
            },
            {
                "filename": "src/utils/hash.py",
                "patch": (
                    "@@ -0,0 +1,4 @@\n"
                    "+import hashlib\n"
                    "+def hash_password(password: str) -> str:\n"
                    "+    return hashlib.md5(password.encode()).hexdigest()\n"
                ),
                "full_content": (
                    "import hashlib\n"
                    "def hash_password(password: str) -> str:\n"
                    "    return hashlib.md5(password.encode()).hexdigest()\n"
                ),
                "language": "python",
                "additions": 4,
                "deletions": 0,
            },
        ],
        "findings": [
            {
                "file": "src/auth/login.py",
                "line": 11,
                "severity": "critical",
                "category": "security",
                "message": "Hardcoded secret key: SECRET_KEY = 'hardcoded-secret-abc123'",
                "fix": "Use environment variable: SECRET_KEY = os.getenv('SECRET_KEY')",
            },
            {
                "file": "src/auth/login.py",
                "line": 14,
                "severity": "critical",
                "category": "security",
                "message": "SQL Injection: query = f\"SELECT * FROM users WHERE name='{username}'\"",
                "fix": "Use parameterized query: conn.execute('SELECT * FROM users WHERE name=?', (username,))",
            },
            {
                "file": "src/utils/hash.py",
                "line": 3,
                "severity": "critical",
                "category": "security",
                "message": "Weak hashing: hashlib.md5() is cryptographically broken for passwords",
                "fix": "Use bcrypt: import bcrypt; bcrypt.hashpw(password.encode(), bcrypt.gensalt())",
            },
        ],
        "overall_score": 0.1,
        "summary": "Critical security issues found: SQL injection, hardcoded secret, and weak MD5 password hashing.",
    }


def load_pr_context_from_report(report_path: str) -> dict:
    """Load context from a saved review report JSON."""
    data = json.loads(Path(report_path).read_text(encoding="utf-8"))

    findings = data.get("findings", [])
    # Also pull findings from nested file reviews
    if not findings:
        for fr in data.get("files", []):
            findings.extend(fr.get("review", {}).get("findings", []))

    files = []
    for fr in data.get("files", []):
        files.append({
            "filename": fr["file"],
            "summary": fr.get("review", {}).get("summary", ""),
            "score": fr.get("review", {}).get("overall_score", "?"),
        })

    return {
        "pr_title": data.get("pr_title", "Unknown PR"),
        "repo": data.get("repo", ""),
        "pr_number": data.get("pr_number", ""),
        "files": files,
        "findings": findings,
        "overall_score": data.get("overall_score", "?"),
        "summary": data.get("executive_summary", {}).get("executive_summary", ""),
    }


# ─────────────────────────────────────────────────────────────────────────────
# BUILD SYSTEM PROMPT WITH PR CONTEXT
# ─────────────────────────────────────────────────────────────────────────────

def build_system_prompt(pr_context: dict) -> str:
    findings_text = ""
    for f in pr_context.get("findings", []):
        findings_text += (
            f"\n- [{f.get('severity','?').upper()}] "
            f"{f.get('file','')}:L{f.get('line',0)} | "
            f"{f.get('category','')} | "
            f"{f.get('message','')}\n"
            f"  Fix: {f.get('fix','')}\n"
        )

    files_text = ""
    for f in pr_context.get("files", []):
        fname = f.get("filename", f.get("file", ""))
        if f.get("full_content"):
            files_text += f"\n\n--- File: {fname} ---\n{f['full_content'][:3000]}"
        elif f.get("patch"):
            files_text += f"\n\n--- File: {fname} (diff) ---\n{f['patch'][:2000]}"
        else:
            files_text += f"\n- {fname} (score: {f.get('score','?')}): {f.get('summary','')}"

    return f"""You are a code review assistant. A pull request has been reviewed and you have full context about it.

PR DETAILS:
  Title      : {pr_context.get('pr_title', 'Unknown')}
  Repo       : {pr_context.get('repo', '')}
  PR Number  : {pr_context.get('pr_number', '')}
  Score      : {pr_context.get('overall_score', '?')} / 1.0
  Summary    : {pr_context.get('summary', '')}

FINDINGS ({len(pr_context.get('findings', []))} total):
{findings_text if findings_text else '  No findings.'}

FILES IN PR:
{files_text if files_text else '  No file details available.'}

INSTRUCTIONS:
- Answer questions about this specific PR only.
- If asked about a finding, quote the exact code and explain clearly.
- If asked for a fix, provide complete, working code.
- Be concise but thorough.
- If the question is unrelated to this PR, say so politely.
"""


# ─────────────────────────────────────────────────────────────────────────────
# CHAT LOOP
# ─────────────────────────────────────────────────────────────────────────────

def chat(pr_context: dict):
    client = Groq(api_key=os.getenv("GROQ_API_KEY", ""))
    model  = os.getenv("REVIEW_MODEL", "llama-3.3-70b-versatile")

    system_prompt = build_system_prompt(pr_context)
    history       = []  # stores {"role": ..., "content": ...}

    findings_count = len(pr_context.get("findings", []))
    score          = pr_context.get("overall_score", "?")

    print("\n" + "═" * 55)
    print("  PR Chat Assistant")
    print("═" * 55)
    print(f"  PR     : {pr_context.get('pr_title', 'Unknown')}")
    print(f"  Score  : {score} / 1.0")
    print(f"  Issues : {findings_count} findings")
    print("═" * 55)
    print("  Ask anything about this PR.")
    print("  Examples:")
    print("    > What security issues were found?")
    print("    > Explain the SQL injection")
    print("    > How do I fix the hardcoded secret?")
    print("    > Show me the fixed version of login.py")
    print("    > Is this PR safe to merge?")
    print("  Type 'quit' to exit.")
    print("═" * 55 + "\n")

    while True:
        try:
            user_input = input("You: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nGoodbye!")
            break

        if not user_input:
            continue

        if user_input.lower() in ("quit", "exit", "q", "bye"):
            print("Goodbye!")
            break

        # Add user message to history
        history.append({"role": "user", "content": user_input})

        try:
            response = client.chat.completions.create(
                model=model,
                temperature=0.2,
                max_tokens=1024,
                messages=[
                    {"role": "system", "content": system_prompt},
                    *history,
                ],
            )

            answer = response.choices[0].message.content.strip()

            # Add assistant reply to history (for follow-up context)
            history.append({"role": "assistant", "content": answer})

            print(f"\nAssistant: {answer}\n")

        except Exception as e:
            print(f"\n[ERROR] {e}\n")
            # Remove the failed user message from history
            history.pop()


# ─────────────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Chat about a reviewed PR")
    parser.add_argument(
        "--mock", action="store_true",
        help="Use mock PR data (no files needed)",
    )
    parser.add_argument(
        "--report", type=str, default="",
        help="Path to a review report JSON (from app.py --mock)",
    )
    args = parser.parse_args()

    if args.report:
        print(f"Loading report: {args.report}")
        pr_context = load_pr_context_from_report(args.report)
    elif args.mock:
        pr_context = load_pr_context_from_mock()
    else:
        # Auto-find latest report
        reports = sorted(Path("reports").glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        if reports:
            print(f"Auto-loading latest report: {reports[0]}")
            pr_context = load_pr_context_from_report(str(reports[0]))
        else:
            print("No report found. Using mock data.")
            print("Tip: Run  python app.py --mock  first to generate a report.")
            pr_context = load_pr_context_from_mock()

    chat(pr_context)


if __name__ == "__main__":
    main()