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
import json
import re
import requests
from dataclasses import dataclass
from config import cfg

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

    def __init__(self):
        self._groq = None

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
        results = []
        unfixed = []
        already_fixed_locations = set()  # (file, line) already generated+posted a fix for — FIX 2

        targets = [f for f in findings if f.get("severity") in ("critical", "warning")]
        print(f"\n[autofix-engine] Processing {len(targets)} findings...")

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

            posted = self._post_suggestion(loader, repo, pr_number, head_sha,
                                            finding, fix_code, explanation, pf)
            if posted:
                already_fixed_locations.add(line_key)  # FIX 2
            results.append(FixResult(finding=finding, fixable=True, fix_applied=posted,
                                      fix_code=fix_code, fix_explanation=explanation,
                                      fix_type=fix_type))

            if not posted and finding.get("severity") == "critical":
                unfixed.append(finding)

            print(f"  {'✅ FIX SUGGESTED' if posted else '⚠️ FIX GENERATED (not posted)'}: "
                  f"{target_file}:L{finding.get('line', 0)} ({fix_type})")

        fixed = sum(1 for r in results if r.fix_applied)
        print(f"[autofix-engine] {fixed} fixes suggested, {len(unfixed)} critical need manual fix")
        return results, unfixed

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
        fix_hint = (finding.get("fix") or "").lower()
        if any(kw in fix_hint for kw in ["os.getenv", "parameterized", "bcrypt", "subprocess.run"]):
            return True, "llm_hint"
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

        # Rule-based first (no LLM cost)
        rule_fix = self._rule_fix(target, fix_type, indent)
        if rule_fix:
            return rule_fix, self._explain(fix_type)

        # LLM fallback
        return self._llm_fix(finding, pf, target, line_num, fix_type)

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

Return exactly: {{"fixed_line": "<corrected single line>", "explanation": "<one sentence why>"}}"""
        try:
            resp = self._get_groq().chat.completions.create(
                model=cfg.review_model, temperature=0, max_tokens=256,
                messages=[
                    {"role": "system", "content": "You fix security vulnerabilities. Return JSON only, no markdown fences."},
                    {"role": "user", "content": prompt},
                ],
            )
            text = resp.choices[0].message.content.strip()
            text = re.sub(r'```[a-z]*\n?', '', text).strip('`').strip()
            data = json.loads(text)
            return data.get("fixed_line", ""), data.get("explanation", "")
        except Exception as e:
            return "", str(e)

    # ── GitHub suggestion posting ─────────────────────────

    def _post_suggestion(self, loader, repo, pr_number, head_sha, finding, fix_code, explanation, pf=None):
        # FIX 1: MockGitHubLoader has no .auth — skip cleanly instead of
        # throwing three different exceptions per finding during --mock runs.
        if not hasattr(loader, "auth"):
            print(f"[autofix] Skipping post (loader has no GitHub auth — likely mock mode): "
                  f"{finding.get('file','')}:{finding.get('line',0)}")
            return False

        sev = finding.get("severity", "warning").upper()
        line_num = finding.get("line", 0)
        target_file = finding.get("file", "")
        message = finding.get("message", "")
        category = finding.get("category", "security").replace("_", " ").title()

        # Extract original bad code
        original_code = ""
        m = re.search(r"['\"`](.+?)['\"`]", message)
        if m:
            original_code = m.group(1).strip()
        if not original_code and pf:
            file_lines = (pf.full_content or "").splitlines()
            if 0 < line_num <= len(file_lines):
                original_code = file_lines[line_num - 1].strip()

        sev_icon = {"CRITICAL": "\U0001f534", "WARNING": "\U0001f7e1", "INFO": "\U0001f535"}.get(sev, "\U0001f535")

        body = f"""---

## {sev_icon} {sev.capitalize()} \u2014 {category}

### \U0001f50d Detected

```python
{original_code}
```

### \U0001f4cb Issue

> {message}

### \u2705 Auto Fix

```suggestion
{fix_code}
```

### \U0001f4a1 Or apply manually

```python
{fix_code}
```

> {explanation}

---
*\U0001f916 AI Code Review \xb7 Click **Commit suggestion** above to apply instantly*"""

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

    def _get_groq(self):
        if self._groq is None:
            from groq import Groq
            self._groq = Groq(api_key=cfg.groq_api_key)
        return self._groq