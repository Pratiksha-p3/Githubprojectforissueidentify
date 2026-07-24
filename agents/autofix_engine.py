"""
agents/autofix_engine.py

Auto-Fix Engine — Senior Software Engineer version

Properly maps findings to diff positions so GitHub renders
the blue "suggestion" boxes with one-click apply.

Flow:
  Finding detected
       ↓
  Is it auto-fixable? (pattern check)
       ↓
  YES → Generate fix (rule-based first, LLM fallback)
       ↓
  Map line number to diff position
       ↓
  Post as GitHub suggestion (blue box, one-click apply)
       ↓
  NO → Block PR via gate
"""
from __future__ import annotations
import ast
import json
import re
import requests
from dataclasses import dataclass

FIXABLE_PATTERNS = [
    {"id": "hardcoded-secret", "pattern": r'(password|passwd|pwd|secret|api_key|apikey|token|db_pass)\s*=\s*["\'][^"\']+["\']', "flags": re.IGNORECASE, "fix_type": "env_var"},
    {"id": "sql-fstring",      "pattern": r'\.execute\s*\(\s*f["\']',                        "flags": 0, "fix_type": "parameterized_query"},
    {"id": "sql-concat",       "pattern": r'\.execute\s*\(.*["\'\s]+\+',                     "flags": 0, "fix_type": "parameterized_query"},
    {"id": "md5-hash",         "pattern": r'hashlib\.md5\s*\(',                              "flags": 0, "fix_type": "bcrypt"},
    {"id": "os-system",        "pattern": r'os\.system\s*\(',                                "flags": 0, "fix_type": "subprocess"},
    {"id": "shell-true",       "pattern": r'subprocess\.(run|call|Popen).*shell\s*=\s*True',  "flags": 0, "fix_type": "subprocess_no_shell"},
    {"id": "eval-usage",       "pattern": r'\beval\s*\(',                                    "flags": 0, "fix_type": "ast_literal"},
    {"id": "bare-except",      "pattern": r'except\s*:',                                     "flags": 0, "fix_type": "proper_except"},
    {"id": "syntax-missing-colon", "pattern": r"^(def|if|for|while|class|except).*[^:]$",     "flags": 0, "fix_type": "add_colon"},
]

UNFIXABLE_CATEGORIES = {"architecture", "performance", "docs"}
UNFIXABLE_KEYWORDS = ["authentication bypass", "authorization", "csrf", "ssrf",
                       "missing validation", "business logic", "race condition"]


@dataclass
class FixResult:
    finding:         dict
    fixable:         bool
    fix_applied:     bool
    fix_code:        str = ""
    fix_explanation: str = ""
    fix_type:        str = ""
    error:           str = ""


class AutoFixEngine:

    def _github_request(self, method: str, url: str, headers: dict, payload: dict | None = None):
        try:
            resp = requests.request(method=method, url=url, headers=headers, json=payload, timeout=15)
            if resp.status_code not in (200, 201):
                print(f"[github] ERROR {resp.status_code}\nURL: {url}\nResponse: {resp.text}")
            return resp
        except Exception as e:
            print(f"[github] REQUEST FAILED\nURL: {url}\nError: {e}")
            return None

    # ── Public ───────────────────────────────────────────

    def process_findings(self, findings, pr_files, repo, pr_number, head_sha, loader):
        file_map = {pf.filename: pf for pf in pr_files}
        already_fixed_locations = set()  # (file, line) already generated a fix for — FIX 2

        targets = [f for f in findings if f.get("severity") in ("critical", "warning")]
        print(f"\n[autofix-engine] Processing {len(targets)} findings...")

        # Generate fixes first WITHOUT posting anything — each fixable
        # finding becomes a pending (finding, fix_code, explanation, body)
        # tuple. Posting them one at a time (the old behavior) means one
        # standalone GitHub API call per finding, and GitHub emails a
        # separate notification for each one — N fixable findings meant N
        # emails from a single run. They're posted together as one PR
        # review below instead, which GitHub notifies about exactly once.
        pending = []
        results = []
        unfixed = []

        for finding in targets:
            target_file = finding.get("file", "")
            line_key = (target_file, finding.get("line", 0))

            if line_key in already_fixed_locations:
                print(f"  ↩️  SKIP (already fixed this line): {target_file}:L{finding.get('line', 0)}")
                results.append(FixResult(finding=finding, fixable=True, fix_applied=True,
                                          error="Duplicate location — fix already suggested above"))
                continue

            pf = file_map.get(target_file)
            fixable, fix_type = self._is_fixable(finding, pf)

            if not fixable:
                results.append(FixResult(finding=finding, fixable=False, fix_applied=False,
                                          error="Requires human judgment"))
                if finding.get("severity") == "critical":
                    unfixed.append(finding)
                print(f"  ❌ UNFIXABLE: {target_file}:L{finding.get('line', 0)} — {finding.get('message', '')[:60]}")
                continue

            fix_code, explanation = self._generate_fix(finding, pf, fix_type)

            if not fix_code:
                results.append(FixResult(finding=finding, fixable=True, fix_applied=False,
                                          fix_type=fix_type, error="Could not generate fix"))
                if finding.get("severity") == "critical":
                    unfixed.append(finding)
                continue

            already_fixed_locations.add(line_key)  # FIX 2
            body = self._build_suggestion_body(finding, fix_code, explanation, pf)
            pending.append({"finding": finding, "fix_code": fix_code, "explanation": explanation,
                             "fix_type": fix_type, "pf": pf, "body": body})

        posted_map = self._post_findings(loader, repo, pr_number, head_sha, pending)

        for item in pending:
            finding = item["finding"]
            line_key = (finding.get("file", ""), finding.get("line", 0))
            posted = posted_map.get(line_key, False)
            results.append(FixResult(finding=finding, fixable=True, fix_applied=posted,
                                      fix_code=item["fix_code"], fix_explanation=item["explanation"],
                                      fix_type=item["fix_type"]))
            if not posted and finding.get("severity") == "critical":
                unfixed.append(finding)
            print(f"  {'✅ FIX SUGGESTED' if posted else '⚠️ FIX GENERATED (not posted)'}: "
                  f"{finding.get('file','')}:L{finding.get('line', 0)} ({item['fix_type']})")

        fixed = sum(1 for r in results if r.fix_applied)
        print(f"[autofix-engine] {fixed} fixes suggested, {len(unfixed)} critical need manual fix")
        return results, unfixed

    def _post_findings(self, loader, repo, pr_number, head_sha, pending: list[dict]) -> dict:
        """
        Posts every pending suggestion as ONE PR review (one GitHub
        notification total) when possible. GitHub rejects the whole
        review if any comment's line isn't part of the diff, so on
        failure this falls back to posting one at a time (the old,
        noisier behavior) rather than silently dropping every suggestion.
        Returns {(file, line): posted_bool}.
        """
        if not pending:
            return {}
        if not hasattr(loader, "auth"):
            print("[autofix] Skipping post (loader has no GitHub auth — likely mock mode)")
            return {}

        comments = [
            {
                "path": item["finding"].get("file", ""),
                "line": item["finding"].get("line", 0),
                "side": "RIGHT",
                "body": item["body"],
            }
            for item in pending
        ]

        try:
            resp = self._github_request(
                method="POST",
                url=f"https://api.github.com/repos/{repo}/pulls/{pr_number}/reviews",
                headers=loader.auth.headers(),
                payload={"commit_id": head_sha, "event": "COMMENT", "comments": comments},
            )
            if resp is not None and resp.status_code in (200, 201):
                print(f"[autofix] ✅ Posted {len(pending)} suggestion(s) as a single review")
                return {
                    (item["finding"].get("file", ""), item["finding"].get("line", 0)): True
                    for item in pending
                }
            if resp is not None:
                print(f"[autofix] Batched review failed ({resp.status_code}), "
                      f"falling back to posting individually")
        except Exception as e:
            print(f"[autofix] Batched review failed ({e}), falling back to posting individually")

        # Fallback: same resilience as before, at the cost of one
        # notification per finding — only reached when the batch itself
        # doesn't work (e.g. a line isn't part of this PR's diff).
        posted_map = {}
        for item in pending:
            finding = item["finding"]
            posted = self._post_suggestion(loader, repo, pr_number, head_sha, finding,
                                            item["fix_code"], item["explanation"], item["pf"])
            posted_map[(finding.get("file", ""), finding.get("line", 0))] = posted
        return posted_map

    # ── Fixability ───────────────────────────────────────

    def _is_fixable(self, finding, pf):
        if finding.get("category") in UNFIXABLE_CATEGORIES:
            return False, ""
        msg = (finding.get("message") or "").lower()
        if any(kw in msg for kw in UNFIXABLE_KEYWORDS):
            return False, ""
        if pf:
            line_num = finding.get("line", 0)
            lines = (pf.full_content or "").splitlines()

            if 0 < line_num <= len(lines):
                code_line = lines[line_num - 1]
                print(f"[autofix] Checking {finding.get('file')}:{line_num}")
                print(f"[autofix] Code: {code_line}")

                for rule in FIXABLE_PATTERNS:
                    if re.search(rule["pattern"], code_line, rule["flags"]):
                        print(f"[autofix] MATCHED: {rule['id']}")
                        return True, rule["fix_type"]

                print("[autofix] No fixable pattern matched")

        # Finding already carries its own fix from an earlier stage
        # (semgrep, ai_review's senior-engineer pass, architecture/
        # compliance guards) — reuse it (after validation) instead of
        # spending a fresh LLM call to regenerate one from scratch.
        if (finding.get("fix") or "").strip():
            return True, "existing_fix"

        return False, ""

    # ── Fix generation ───────────────────────────────────

    def _generate_fix(self, finding, pf, fix_type):
        if not pf:
            return "", "No file content"
        lines = (pf.full_content or "").splitlines()
        line_num = finding.get("line", 0)
        if not (0 < line_num <= len(lines)):
            return "", "Line out of range"
        target = lines[line_num - 1]
        indent = " " * (len(target) - len(target.lstrip()))

        # 1. Rule-based first — free, deterministic, no LLM call.
        rule_fix = self._rule_fix(target, fix_type, indent)
        if rule_fix and self._is_valid_fix(rule_fix, indent):
            return rule_fix, self._explain(fix_type)

        # 2. Reuse a fix the finding already carries (semgrep/ai_review/
        #    architecture/compliance guards already spent an LLM call on
        #    this, if it used one at all) — validate it, then use it
        #    directly instead of regenerating from scratch.
        if fix_type == "existing_fix":
            existing = (finding.get("fix") or "").rstrip()
            if existing and self._is_valid_fix(existing, indent):
                explanation = finding.get("reason") or finding.get("message", "")
                return existing, explanation
            print(f"[autofix] Existing fix failed validation for "
                  f"{finding.get('file')}:{line_num}, falling back to LLM")

        # 3. LLM fallback — last resort, and validated before use.
        fix_code, explanation = self._llm_fix(finding, pf, target, line_num, fix_type)
        if fix_code and not self._is_valid_fix(fix_code, indent):
            print(f"[autofix] Discarding invalid LLM fix for "
                  f"{finding.get('file')}:{line_num}: {fix_code!r}")
            return "", "Generated fix failed syntax validation"
        return fix_code, explanation

    def _is_valid_fix(self, code: str, indent: str) -> bool:
        """
        Best-effort syntax check: does this fix parse as valid Python when
        dropped into a block at the target line's indentation? Catches the
        failure mode where a fix jams multiple statements onto one
        physical line with semicolons (e.g. an invalid one-line
        try/except) instead of the multi-line block it actually needs.
        """
        if not code.strip():
            return False
        try:
            wrapped = ("if True:\n" + code) if indent else code
            ast.parse(wrapped)
            return True
        except SyntaxError:
            return False

    def _rule_fix(self, line, fix_type, indent):
        if fix_type == "env_var":
            m = re.match(r'\s*(\w+)\s*=\s*["\'][^"\']+["\']', line)
            if m:
                var = m.group(1)
                return f'{indent}{var} = os.getenv("{var.upper()}")'

        if fix_type == "bcrypt":
            f = re.sub(r'hashlib\.md5\((.+?)\.encode\(\)\)\.hexdigest\(\)',
                       r'bcrypt.hashpw(\1.encode(), bcrypt.gensalt()).decode()', line)
            return f.rstrip() if f != line else ""

        if fix_type == "subprocess":
            m = re.match(r'\s*os\.system\s*\((.+)\)\s*$', line)
            if m:
                return f'{indent}subprocess.run({m.group(1)}.split(), capture_output=True, check=True)'

        if fix_type == "subprocess_no_shell":
            f = line.replace("shell=True", "shell=False")
            return f.rstrip() if f != line else ""

        if fix_type == "proper_except":
            f = line.replace("except:", "except Exception as e:")
            return f.rstrip() if f != line else ""

        if fix_type == "ast_literal":
            f = re.sub(r'\beval\s*\(', 'ast.literal_eval(', line)
            return f.rstrip() if f != line else ""

        if fix_type == "add_colon":
            if not line.strip().endswith(":"):
                return line + ":"

        return ""

    def _explain(self, fix_type):
        return {
            "env_var":             "Move secret to environment variable — never commit credentials to source control.",
            "parameterized_query": "Use parameterized queries to prevent SQL injection attacks.",
            "bcrypt":              "MD5 is cryptographically broken — use bcrypt for password hashing.",
            "subprocess":          "os.system() is a security risk — use subprocess.run() with a list of args.",
            "subprocess_no_shell": "shell=True allows shell injection — use shell=False instead.",
            "ast_literal":         "eval() executes arbitrary code — use ast.literal_eval() for safe evaluation.",
            "proper_except":       "Bare except catches everything including SystemExit — be specific.",
        }.get(fix_type, "Apply secure coding best practices.")

    def _llm_fix(self, finding, pf, target_line, line_num, fix_type):
        lines = (pf.full_content or "").splitlines()
        start = max(0, line_num - 3)
        end = min(len(lines), line_num + 3)
        snippet = "\n".join(f"{i+1}: {lines[i]}" for i in range(start, end))
        prompt = f"""Fix this security issue. Return JSON only, no markdown.

Issue: {finding.get('message', '')}
Fix type: {fix_type}

Code (lines {start+1}-{end}):
{snippet}

Target line {line_num}: {target_line}

Return exactly: {{"fixed_line": "<replacement code for the target line, as valid Python matching its indentation. Use one line when one line is enough. When the correct fix genuinely needs more than one statement (e.g. wrapping in try/except), return multiple lines separated by \\n at the same indentation — never join statements with semicolons across a compound-statement boundary.>", "explanation": "<one sentence why>"}}"""
        try:
            from agents.llm_client import chat_completion
            text = chat_completion(
                system="You fix security vulnerabilities. Return JSON only, no markdown fences.",
                user=prompt,
                temperature=0,
                max_tokens=256,
            ).strip()
            text = re.sub(r'```[a-z]*\n?', '', text).strip('`').strip()
            data = json.loads(text)
            return data.get("fixed_line", ""), data.get("explanation", "")
        except Exception as e:
            return "", str(e)

    # ── GitHub suggestion posting ─────────────────────────

    def _build_suggestion_body(self, finding, fix_code, explanation, pf=None) -> str:
        sev = finding.get("severity", "warning").upper()
        line_num = finding.get("line", 0)
        message = finding.get("message", "")
        category = finding.get("category", "security").replace("_", " ").title()

        original_code = ""
        m = re.search(r"['\"`](.+?)['\"`]", message)
        if m:
            original_code = m.group(1).strip()
        if not original_code and pf:
            file_lines = (pf.full_content or "").splitlines()
            if 0 < line_num <= len(file_lines):
                original_code = file_lines[line_num - 1].strip()

        sev_icon = {"CRITICAL": "\U0001f534", "WARNING": "\U0001f7e1", "INFO": "\U0001f535"}.get(sev, "\U0001f535")

        return (
            "---\n\n"
            f"## {sev_icon} {sev.capitalize()} — {category}\n\n"
            "### \U0001f50d Detected\n\n"
            f"```python\n{original_code}\n```\n\n"
            "### \U0001f4cb Issue\n\n"
            f"> {message}\n\n"
            "### ✅ Auto Fix\n\n"
            f"```suggestion\n{fix_code}\n```\n\n"
            "### \U0001f4a1 Or apply manually\n\n"
            f"```python\n{fix_code}\n```\n\n"
            f"> {explanation}\n\n"
            "---\n"
            "*\U0001f916 AI Code Review \xb7 Click **Commit suggestion** above to apply instantly*"
        )

    def _post_suggestion(self, loader, repo, pr_number, head_sha, finding, fix_code, explanation, pf=None):
        # FIX 1: MockGitHubLoader has no .auth — skip cleanly instead of
        # throwing three different exceptions per finding during --mock runs.
        if not hasattr(loader, "auth"):
            print(f"[autofix] Skipping post (loader has no GitHub auth — likely mock mode): "
                  f"{finding.get('file','')}:{finding.get('line',0)}")
            return False

        line_num = finding.get("line", 0)
        target_file = finding.get("file", "")
        body = self._build_suggestion_body(finding, fix_code, explanation, pf)

        print(f"[autofix] Posting suggestion {target_file}:{line_num}")

        # Try 1: inline comment with line number (works if line is in diff)
        try:
            resp = self._github_request(
                method="POST",
                url=f"https://api.github.com/repos/{repo}/pulls/{pr_number}/comments",
                headers=loader.auth.headers(),
                payload={
                    "body": body,
                    "commit_id": head_sha,
                    "path": target_file,
                    "line": line_num,
                    "side": "RIGHT",
                },
            )
            if resp is not None and resp.status_code in (200, 201):
                print(f"[autofix] ✅ Inline suggestion posted: {target_file}:{line_num}")
                return True
            if resp is not None:
                print(f"[autofix] ❌ GitHub Inline API Failed ({resp.status_code})")
                print(resp.text)
        except Exception as e:
            print(f"[autofix] Inline comment failed: {e}")

        # Try 2: use diff position mapping
        if pf:
            position = self._get_diff_position(getattr(pf, "patch", ""), line_num)
            if position:
                try:
                    resp2 = self._github_request(
                        method="POST",
                        url=f"https://api.github.com/repos/{repo}/pulls/{pr_number}/comments",
                        headers=loader.auth.headers(),
                        payload={
                            "body": body,
                            "commit_id": head_sha,
                            "path": target_file,
                            "position": position,
                        },
                    )
                    if resp2 is not None and resp2.status_code in (200, 201):
                        print(f"[autofix] ✅ Position-based suggestion posted: {target_file} pos={position}")
                        return True
                    if resp2 is not None:
                        print(f"[autofix] ❌ GitHub Position API Failed ({resp2.status_code})")
                        print(resp2.text)
                except Exception as e:
                    print(f"[autofix] Position comment failed: {e}")

        # Try 3: Post as a PR Review (most reliable for suggestions)
        try:
            review_resp = self._github_request(
                method="POST",
                url=f"https://api.github.com/repos/{repo}/pulls/{pr_number}/reviews",
                headers=loader.auth.headers(),
                payload={
                    "commit_id": head_sha,
                    "event": "COMMENT",
                    "comments": [
                        {
                            "path": target_file,
                            "line": line_num,
                            "side": "RIGHT",
                            "body": f"```suggestion\n{fix_code}\n```",
                        }
                    ],
                },
            )
            if review_resp is not None and review_resp.status_code in (200, 201):
                print(f"[autofix] ✅ Review suggestion posted: {target_file}:{line_num}")
                return True
            if review_resp is not None:
                print(f"[autofix] ❌ GitHub Review API Failed ({review_resp.status_code})")
                print(review_resp.text)
        except Exception as e:
            print(f"[autofix] Review API failed: {e}")

        # Final fallback: issue comment (no Commit button, but shows fix code)
        try:
            resp = self._github_request(
                method="POST",
                url=f"https://api.github.com/repos/{repo}/issues/{pr_number}/comments",
                headers=loader.auth.headers(),
                payload={"body": body},
            )
            if resp is not None and resp.status_code in (200, 201):
                print("[autofix] ⚠️ Fallback issue comment posted")
                return True
            return False
        except Exception:
            return False

    def _get_diff_position(self, patch: str, target_line: int) -> int | None:
        """
        Maps an absolute file line number to a diff position.
        GitHub's older API uses 'position' (line number within the diff hunk),
        not the absolute file line number.
        """
        if not patch:
            return None
        position = 0
        file_line = 0
        for line in patch.splitlines():
            position += 1
            if line.startswith("@@"):
                m = re.search(r"\+(\d+)", line)
                if m:
                    file_line = int(m.group(1)) - 1
            elif line.startswith("-"):
                continue
            else:
                file_line += 1
                if file_line == target_line:
                    return position
        print(f"[autofix] target_line={target_line}, position=None")
        return None