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

from analyzers.unused_imports import DELETE_LINE_SENTINEL


def _starts_indented(code: str) -> bool:
    """Does this fix's own first line start with leading whitespace? Used
    to decide whether an ast.parse() check needs an `if True:` wrapper —
    based on the fix text itself, NOT the original (possibly-zero, since
    that's often exactly the bug being fixed) indentation of the line it
    replaces."""
    first_line = code.splitlines()[0] if code else ""
    return first_line[:1] in (" ", "\t")


def _first_word(code: str) -> str:
    first_line = code.splitlines()[0] if code else ""
    m = re.match(r"\s*([A-Za-z_]+)", first_line)
    return m.group(1) if m else ""


# elif/else/except/finally can never parse standalone, no matter how
# they're wrapped — they're only legal immediately after their matching
# opener (if/for/while, or specifically try for except/finally), and
# that opener is an earlier, UNCHANGED line, not part of the fix being
# validated. Checked against a synthetic matching opener instead.
_CONTINUATION_OPENERS = {"elif": "if True:", "else": "if True:",
                          "except": "try:", "finally": "try:"}


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


# These are the ONLY findings routed to manual review before an attempt
# is even made — reserved for what genuinely can't be safely inferred
# without human context (a design call, or a security fix where a
# confident-sounding but subtly wrong change is worse than no change).
# Everything else attempts a real fix (rule-based, reused, or LLM-
# generated) and lets the confidence-scoring gate in process_findings
# decide whether that specific attempt is trustworthy enough to
# auto-suggest — "docs", "performance", and "missing validation" (a
# bounds/null check, usually) used to skip the attempt entirely despite
# being exactly the kind of thing this engine is good at; they no
# longer get a blanket exclusion, just the same confidence check as
# everything else.
UNFIXABLE_CATEGORIES = {"architecture"}
UNFIXABLE_KEYWORDS = ["authentication bypass", "authorization", "csrf", "ssrf",
                       "business logic", "race condition"]

# A generated fix only gets auto-suggested at this confidence or above;
# anything lower is routed to manual review instead — "the system
# generated a candidate but isn't sure enough to put a one-click apply
# button in front of a developer" is a materially different, safer
# claim than silently posting it anyway.
_CONFIDENCE_RANK = {"low": 0, "medium": 1, "high": 2}
MIN_AUTO_APPLY_CONFIDENCE = "medium"


@dataclass
class FixResult:
    finding:         dict
    fixable:         bool
    fix_applied:     bool
    fix_code:        str = ""
    fix_explanation: str = ""
    fix_type:        str = ""
    confidence:      str = ""
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
        # (file, line) -> human-readable reason a person needs to apply this
        # one manually. Threaded out to app.py/github_loader so the PR
        # comment can say *why* instead of silently showing (or hiding) a
        # Commit-suggestion box for something that was never auto-applied.
        manual_reasons: dict[tuple, str] = {}

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
                reason = self._unfixable_reason(finding)
                results.append(FixResult(finding=finding, fixable=False, fix_applied=False,
                                          error=reason))
                manual_reasons[line_key] = reason
                if finding.get("severity") == "critical":
                    unfixed.append(finding)
                print(f"  ❌ UNFIXABLE: {target_file}:L{finding.get('line', 0)} — {finding.get('message', '')[:60]}")
                continue

            fix_code, explanation, confidence = self._generate_fix(finding, pf, fix_type)

            if not fix_code:
                reason = explanation or "No safe automatic fix could be generated for this issue."
                results.append(FixResult(finding=finding, fixable=True, fix_applied=False,
                                          fix_type=fix_type, confidence=confidence, error=reason))
                manual_reasons[line_key] = reason
                if finding.get("severity") == "critical":
                    unfixed.append(finding)
                continue

            if _CONFIDENCE_RANK.get(confidence, 0) < _CONFIDENCE_RANK[MIN_AUTO_APPLY_CONFIDENCE]:
                reason = (
                    f"A fix was generated ({explanation}) but confidence was too low "
                    f"to auto-suggest — please review and apply manually if correct."
                )
                results.append(FixResult(finding=finding, fixable=True, fix_applied=False,
                                          fix_code=fix_code, fix_explanation=explanation,
                                          fix_type=fix_type, confidence=confidence, error=reason))
                manual_reasons[line_key] = reason
                print(f"  ⚠️ LOW CONFIDENCE, routed to manual review: "
                      f"{target_file}:L{finding.get('line', 0)}")
                if finding.get("severity") == "critical":
                    unfixed.append(finding)
                continue

            already_fixed_locations.add(line_key)  # FIX 2
            body = self._build_suggestion_body(finding, fix_code, explanation, pf)
            pending.append({"finding": finding, "fix_code": fix_code, "explanation": explanation,
                             "fix_type": fix_type, "confidence": confidence, "pf": pf, "body": body})

        posted_map = self._post_findings(loader, repo, pr_number, head_sha, pending)

        for item in pending:
            finding = item["finding"]
            line_key = (finding.get("file", ""), finding.get("line", 0))
            posted = posted_map.get(line_key, False)
            results.append(FixResult(finding=finding, fixable=True, fix_applied=posted,
                                      fix_code=item["fix_code"], fix_explanation=item["explanation"],
                                      fix_type=item["fix_type"], confidence=item["confidence"]))
            if not posted:
                manual_reasons[line_key] = (
                    "A fix was generated but could not be posted to GitHub as a "
                    "suggestion — apply the change shown below manually."
                )
                if finding.get("severity") == "critical":
                    unfixed.append(finding)
            print(f"  {'✅ FIX SUGGESTED' if posted else '⚠️ FIX GENERATED (not posted)'}: "
                  f"{finding.get('file','')}:L{finding.get('line', 0)} ({item['fix_type']})")

        fixed = sum(1 for r in results if r.fix_applied)
        print(f"[autofix-engine] {fixed} fixes suggested, {len(unfixed)} critical need manual fix")
        return results, unfixed, manual_reasons

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

        # No regex pattern matched and there's no pre-existing fix to
        # reuse (including one that got discarded upstream for being
        # prose instead of code) — that doesn't mean this can't be
        # fixed, only that nothing generated a candidate yet. The
        # category/keyword checks above already ruled out the cases
        # that genuinely need human judgment; for everything else,
        # "attempt to auto-fix every issue first" means asking the LLM
        # fallback in _generate_fix() to write a real fix from scratch,
        # with full file context, rather than giving up immediately.
        line_num = finding.get("line", 0)
        if pf and 0 < line_num <= len((pf.full_content or "").splitlines()):
            return True, "llm_generate"

        return False, ""

    def _unfixable_reason(self, finding) -> str:
        """Human-readable explanation for why this finding was routed to
        manual review instead of an auto-applied fix — shown verbatim in
        the PR comment so a reviewer isn't just told "no" with no reason."""
        category = (finding.get("category") or "").lower()
        msg = (finding.get("message") or "").lower()

        if category in UNFIXABLE_CATEGORIES:
            return (
                f"This is a '{category}' finding — fixing it correctly requires "
                f"understanding the surrounding design or business intent, which "
                f"can't be safely inferred automatically."
            )

        hit = next((kw for kw in UNFIXABLE_KEYWORDS if kw in msg), None)
        if hit:
            return (
                f"This involves {hit} — an incorrect automatic change here risks "
                f"introducing a new security hole rather than closing one."
            )

        return "No automatic fix could be safely generated for this issue."

    # ── Fix generation ───────────────────────────────────

    def _generate_fix(self, finding, pf, fix_type):
        """Returns (fix_code, explanation, confidence) — confidence is one
        of "high"/"medium"/"low", used by process_findings to decide
        whether this is safe to auto-suggest or should go to manual
        review despite a fix having been generated."""
        if not pf:
            return "", "No file content", "low"
        lines = (pf.full_content or "").splitlines()
        line_num = finding.get("line", 0)
        if not (0 < line_num <= len(lines)):
            return "", "Line out of range", "low"
        target = lines[line_num - 1]
        indent = " " * (len(target) - len(target.lstrip()))

        # 1. Rule-based first — free, deterministic, no LLM call. A
        # regex-matched substitution for a known vulnerability shape is
        # as close to "certain" as this engine gets.
        rule_fix = self._rule_fix(target, fix_type, indent)
        if rule_fix and self._is_valid_fix(rule_fix, pf, line_num):
            rule_fix = self._ensure_imports(rule_fix, pf, indent)
            return rule_fix, self._explain(fix_type), "high"

        blocked_by_next = self._blocked_by_next_line(rule_fix, lines, line_num) if rule_fix else None
        blocked_by_prev = self._breaks_on_real_predecessor(rule_fix, lines, line_num) if rule_fix else None

        # 2. Reuse a fix the finding already carries (semgrep/ai_review/
        #    architecture/compliance guards already spent an LLM call on
        #    this, if it used one at all) — validate it, then use it
        #    directly instead of regenerating from scratch. A line-
        #    deletion from the unused-import checker is a verified AST
        #    fact (the name truly isn't referenced), not a guess, so it
        #    gets "high" too; any other reused fix is "medium" — it came
        #    from an earlier LLM/static pass we're trusting, not one we
        #    generated with full context ourselves.
        if fix_type == "existing_fix":
            existing = (finding.get("fix") or "").rstrip()
            if existing and self._is_valid_fix(existing, pf, line_num):
                explanation = finding.get("reason") or finding.get("message", "")
                confidence = "high" if existing == DELETE_LINE_SENTINEL else "medium"
                existing = self._ensure_imports(existing, pf, indent)
                return existing, explanation, confidence
            blocked_by_next = blocked_by_next or (
                self._blocked_by_next_line(existing, lines, line_num) if existing else None
            )
            blocked_by_prev = blocked_by_prev or (
                self._breaks_on_real_predecessor(existing, lines, line_num) if existing else None
            )
            print(f"[autofix] Existing fix failed validation for "
                  f"{finding.get('file')}:{line_num}, falling back to LLM")

        # This fix opens a block (ends with ':') but the very next line,
        # as it currently exists in the file, isn't indented enough to
        # be its body — meaning the REAL reason validation keeps failing
        # is that a second, separate bug on that next line hasn't been
        # fixed yet, not that this fix is wrong. No LLM retry can change
        # that fact (it'd hit the identical wall), so say so directly
        # instead of burning a call to end up back here with a vaguer
        # message. Same idea in the other direction: this fix needs to be
        # indented, but the real line right before it can't support that
        # (doesn't open a block, isn't itself deep enough) — meaning THAT
        # line needs fixing too before this one can actually apply.
        if blocked_by_next:
            return "", (
                f"This also requires line {blocked_by_next} to be corrected — its "
                f"indentation doesn't match what this change needs. Check for a "
                f"separate finding on that line and apply both suggestions together."
            ), "low"
        if blocked_by_prev:
            return "", (
                f"This depends on line {blocked_by_prev} being corrected first — as "
                f"that line currently stands, it can't support this one being indented "
                f"under it. Check for a separate finding on that line and apply both "
                f"suggestions together."
            ), "low"

        # 3. LLM fallback — last resort, and validated before use. Given
        # whole-file context (imports already present, the enclosing
        # function/class body, the file's own quote convention) instead
        # of just a same-line ±3-line window, so the model isn't guessing
        # at surrounding code it can't see. The model self-reports its
        # own confidence; process_findings decides whether that's enough
        # to auto-suggest or route to manual review instead.
        ctx = self._file_context(pf)
        fix_code, explanation, confidence = self._llm_fix(finding, pf, target, line_num, fix_type, ctx)
        if fix_code and not self._is_valid_fix(fix_code, pf, line_num):
            print(f"[autofix] Discarding invalid LLM fix for "
                  f"{finding.get('file')}:{line_num}: {fix_code!r}")
            return "", "Generated fix failed syntax validation", "low"
        if fix_code:
            fix_code = self._ensure_imports(fix_code, pf, indent)
        return fix_code, explanation, confidence

    # ── Whole-file context ────────────────────────────────

    def _file_context(self, pf) -> dict:
        """
        File-level facts a single-line/single-window view can't see:
        every import already present (so a fix doesn't ask for one that
        exists under a different alias, or duplicate one that's already
        there), and the file's dominant quote style (so a generated fix
        doesn't clash with the surrounding code's convention). `tree` is
        the parsed AST when the file parses cleanly, used to find the
        enclosing function/class for a given line — None otherwise, in
        which case callers fall back to a smaller line-window view.
        """
        content = pf.full_content or ""
        ctx = {"imports": [], "quote_style": "double", "tree": None}
        try:
            tree = ast.parse(content)
            ctx["tree"] = tree
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = ", ".join(
                        a.name if not a.asname else f"{a.name} as {a.asname}"
                        for a in node.names
                    )
                    ctx["imports"].append(f"import {names}")
                elif isinstance(node, ast.ImportFrom):
                    mod = "." * node.level + (node.module or "")
                    names = ", ".join(
                        a.name if not a.asname else f"{a.name} as {a.asname}"
                        for a in node.names
                    )
                    ctx["imports"].append(f"from {mod} import {names}")
        except SyntaxError:
            pass

        if content.count("'") > content.count('"'):
            ctx["quote_style"] = "single"
        return ctx

    def _enclosing_scope(self, tree, lineno: int):
        """Innermost function/class AST node whose span covers `lineno`, or None."""
        best = None
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                start = node.lineno
                end = getattr(node, "end_lineno", start)
                if start <= lineno <= end and (best is None or start > best.lineno):
                    best = node
        return best

    def _context_snippet(self, ctx: dict, line_num: int, lines: list[str]) -> str:
        """
        The enclosing function/class body when one exists and isn't
        unreasonably large (so the model sees the real surrounding logic
        it must stay consistent with) — falls back to a small ±3-line
        window when the file didn't parse, the line is at module level,
        or the enclosing scope is too big to usefully include whole.
        """
        tree = ctx.get("tree")
        if tree is not None:
            scope = self._enclosing_scope(tree, line_num)
            if scope is not None:
                start, end = scope.lineno, getattr(scope, "end_lineno", scope.lineno)
                if end - start <= 60:
                    return "\n".join(f"{i+1}: {lines[i]}" for i in range(start - 1, min(end, len(lines))))

        start = max(0, line_num - 3)
        end = min(len(lines), line_num + 3)
        return "\n".join(f"{i+1}: {lines[i]}" for i in range(start, end))

    # Modules our own rule-based fixes can introduce a reference to
    # (os.getenv, bcrypt.hashpw, subprocess.run, ast.literal_eval) — also
    # applied to LLM/reused fixes since those can just as easily reach for
    # one of these without the file having imported it.
    _KNOWN_MODULES = ("os", "subprocess", "bcrypt", "ast")

    def _ensure_imports(self, fix_code: str, pf, indent: str) -> str:
        """
        A fix can be syntactically valid and still be wrong the moment it
        runs, if it references a module (os.getenv, bcrypt.hashpw, ...)
        the file never imported — that's a NameError, not a fix. Prepend
        the missing import(s) at the same indentation as the fix itself
        (legal even inside a function body) rather than silently shipping
        a suggestion that can't actually run.
        """
        if not pf:
            return fix_code
        content = pf.full_content or ""
        used = {
            m for m in self._KNOWN_MODULES
            if re.search(rf'\b{m}\.', fix_code)
        }
        missing = [
            m for m in sorted(used)
            if not re.search(rf'^\s*(import\s+{m}\b|from\s+{m}\s+import\b)', content, re.MULTILINE)
        ]
        if not missing:
            return fix_code
        import_lines = "\n".join(f"{indent}import {m}" for m in missing)
        return f"{import_lines}\n{fix_code}"

    def _is_valid_fix(self, code: str, pf=None, line_num: int = 0) -> bool:
        """
        Best-effort syntax check: does this fix parse as valid Python?
        Wraps in `if True:` when the fix itself starts indented (its OWN
        leading whitespace — not the original line's, which is often
        zero precisely when zero indentation is the bug being fixed).
        Catches the failure mode where a fix jams multiple statements
        onto one physical line with semicolons (e.g. an invalid one-line
        try/except) instead of the multi-line block it actually needs.

        A fix that legitimately needs content from later, UNCHANGED lines
        can never parse checked in isolation — there's no way for a
        single replaced line to "know" about content that isn't part of
        it. Two shapes of this come up:
          - it opens a bracket closed further down (reindenting
            `task = {` where the dict's contents and `}` are untouched
            lines below it)
          - its last line opens a new block (`with open(x) as f:`) whose
            body is the following, untouched lines (e.g. a fix that
            inserts a guard *and* rewraps the guarded line into a `with`)
        When the isolated check fails and pf/line_num are given, this
        retries with the file's own subsequent lines appended — through
        wherever the bracket actually closes, or through the indented
        body that follows a block-opening last line.
        """
        if code == DELETE_LINE_SENTINEL:
            return True  # "delete this line" — trivially valid, nothing to parse
        if not code.strip():
            return False

        first_word = _first_word(code)
        if first_word in _CONTINUATION_OPENERS:
            import textwrap
            dedented = textwrap.dedent(code).rstrip()
            if not dedented.endswith(":"):
                return False  # malformed — a continuation keyword must open a block
            opener = _CONTINUATION_OPENERS[first_word]
            synthetic = f"{opener}\n    pass\n{dedented}\n    pass"
            try:
                ast.parse(synthetic)
                return True
            except SyntaxError:
                return False

        try:
            wrapped = ("if True:\n" + code) if _starts_indented(code) else code
            ast.parse(wrapped)
            isolated_ok = True
        except SyntaxError:
            isolated_ok = False

        if isolated_ok:
            # The synthetic `if True:` wrapper supplies a fake block
            # opener that the REAL file might not actually have right
            # before this line — so a fix can be well-formed in
            # isolation and still be unpostable, if the real preceding
            # line can't support this indent (doesn't end with ':' and
            # isn't itself at an equal-or-deeper level). Confirmed this
            # exact failure mode: a correctly re-indented `def method
            # (self):` validated fine standalone, but the real line
            # before it in the file was an unrelated statement sitting
            # at column 0 — inserting an indented line right after that
            # is "unexpected indent" no synthetic wrapper would catch.
            if pf is not None and line_num:
                real_lines = (pf.full_content or "").splitlines()
                if self._breaks_on_real_predecessor(code, real_lines, line_num):
                    isolated_ok = False
            if isolated_ok:
                return True

        if pf is None or not line_num:
            return False

        lines = (pf.full_content or "").splitlines()
        if not (0 < line_num <= len(lines)):
            return False
        tail = self._lookahead_context(code, lines, line_num)
        if tail is None:
            return False

        try:
            combined = code + "\n" + tail
            wrapped = ("if True:\n" + combined) if _starts_indented(combined) else combined
            ast.parse(wrapped)
        except SyntaxError:
            return False

        if self._breaks_on_real_predecessor(code, lines, line_num):
            return False
        return True

    def _breaks_on_real_predecessor(self, code: str, lines: list[str], line_num: int) -> int | None:
        """
        Returns the 1-indexed line number of the real preceding line
        that conflicts with inserting `code` here, or None if there's
        no conflict. `code` is indented (needs SOME enclosing block)
        but the real previous non-blank line neither opens one (ends
        with ':') nor sits at an equal-or-deeper indent (a plausible
        sibling or dedent target). That's exactly the shape of
        "unexpected indent": Python can only indent deeper right after
        a line that ends in ':' — a synthetic `if True:` wrapper
        supplies a fake one of those, so it can't catch a fix that's
        well-formed on its own but structurally impossible where it
        actually sits (e.g. because THAT line needs its own, separate
        fix applied too).
        """
        if not _starts_indented(code):
            return None
        from analyzers.syntax_checker import _strip_comment

        code_indent = len(code) - len(code.lstrip(" "))
        for i in range(line_num - 2, -1, -1):
            if i >= len(lines):
                continue
            text = _strip_comment(lines[i]).rstrip()
            if not text.strip():
                continue
            prev_indent = len(lines[i]) - len(lines[i].lstrip(" "))
            if text.endswith(":") or prev_indent >= code_indent:
                return None
            return i + 1  # shallower indent, no ':' — can't support this indent
        return None

    def _lookahead_context(self, code: str, lines: list[str], line_num: int) -> str | None:
        """
        Extra ORIGINAL lines (from immediately after the line being
        replaced) a fix needs alongside it to parse — see _is_valid_fix.
        Returns None when the fix doesn't need any, so the caller knows
        the isolated check's failure was real, not an artifact of
        checking one line without its surrounding file.
        """
        from analyzers.syntax_checker import _bracket_delta, _strip_comment

        depth = _bracket_delta(code)
        code_lines = code.splitlines()
        last_line = code_lines[-1] if code_lines else ""
        opens_block = _strip_comment(last_line).rstrip().endswith(":")
        if depth <= 0 and not opens_block:
            return None

        tail: list[str] = []
        if depth > 0:
            for i in range(line_num, min(len(lines), line_num + 200)):  # bounded lookahead
                tail.append(lines[i])
                depth += _bracket_delta(lines[i])
                if depth <= 0:
                    break
            if depth > 0:
                return None  # never closes within the lookahead — genuinely broken
        else:
            block_indent = len(last_line) - len(last_line.lstrip(" "))
            for i in range(line_num, min(len(lines), line_num + 200)):
                candidate = lines[i]
                if not candidate.strip():
                    tail.append(candidate)
                    continue
                cand_indent = len(candidate) - len(candidate.lstrip(" "))
                if cand_indent <= block_indent:
                    break
                tail.append(candidate)
            if not tail:
                return None

        return "\n".join(tail)

    def _blocked_by_next_line(self, code: str, lines: list[str], line_num: int) -> int | None:
        """
        If `code` opens a new block (ends with ':') and the physical line
        right after it — as it actually exists in the file right now —
        isn't indented deeper than this fix's own line, that next line
        can't serve as this block's body. Returns that line's 1-indexed
        number so the caller can say why validation is failing in terms
        a person can act on, instead of a generic "invalid fix". Two
        lines each needing the other to be valid first is a real
        (if unusual) shape: fixing either one in isolation, checked
        against the file as it stands today, can't be verified — both
        need to be applied together.
        """
        if not code:
            return None
        from analyzers.syntax_checker import _strip_comment

        code_lines = code.splitlines()
        last_line = code_lines[-1] if code_lines else ""
        if not _strip_comment(last_line).rstrip().endswith(":"):
            return None
        if not (0 < line_num < len(lines)):
            return None
        next_line = lines[line_num]  # lines[] is 0-indexed; this is line_num+1 in 1-indexed terms
        if not next_line.strip():
            return None
        block_indent = len(last_line) - len(last_line.lstrip(" "))
        next_indent = len(next_line) - len(next_line.lstrip(" "))
        if next_indent <= block_indent:
            return line_num + 1
        return None

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

    def _llm_fix(self, finding, pf, target_line, line_num, fix_type, ctx: dict | None = None):
        lines = (pf.full_content or "").splitlines()
        ctx = ctx or self._file_context(pf)
        snippet = self._context_snippet(ctx, line_num, lines)
        imports_block = "\n".join(ctx["imports"]) or "(none)"
        category = finding.get("category", "issue")
        prompt = f"""You are a senior software engineer fixing a real defect in production
code — not offering a suggestion in prose, an actual replacement for the
target line below. Return JSON only, no markdown.

Category: {category}
Issue: {finding.get('message', '')}
Fix type: {fix_type}

Imports already present in this file (don't suggest adding one of these
again, and don't introduce a new dependency that isn't already imported
unless the fix genuinely requires it):
{imports_block}

This file's dominant string-quote convention is {ctx['quote_style']} quotes —
match it in your replacement.

Surrounding code for context (line numbers shown, target line marked):
{snippet}

Target line {line_num}: {target_line}

Also rate your own confidence that this fix is *correct and safe to apply
automatically without a human reviewing it first*:
  "high"   — you're certain; the fix is mechanical and can't plausibly break anything
  "medium" — you're reasonably confident, but the fix required judgment calls
             a reviewer should still be able to spot-check
  "low"    — you're not confident this is fully correct, or the surrounding
             code context wasn't enough to be sure

Return exactly: {{"fixed_line": "<replacement code for the target line, as valid Python matching its indentation. Use one line when one line is enough. When the correct fix genuinely needs more than one statement (e.g. wrapping in try/except), return multiple lines separated by \\n at the same indentation — never join statements with semicolons across a compound-statement boundary.>", "explanation": "<one sentence why>", "confidence": "high|medium|low"}}"""
        try:
            from agents.llm_client import chat_completion
            text = chat_completion(
                system=(
                    "You are a senior software engineer with 20 years of production "
                    "experience. You fix real defects (security, runtime, logic, quality) "
                    "with concrete, working code — never a prose description of what "
                    "someone else should do. Return JSON only, no markdown fences."
                ),
                user=prompt,
                temperature=0,
                max_tokens=256,
            ).strip()
            text = re.sub(r'```[a-z]*\n?', '', text).strip('`').strip()
            data = json.loads(text)
            confidence = data.get("confidence", "medium")
            if confidence not in ("high", "medium", "low"):
                confidence = "medium"
            return data.get("fixed_line", ""), data.get("explanation", ""), confidence
        except Exception as e:
            return "", str(e), "low"

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

        if fix_code == DELETE_LINE_SENTINEL:
            # An empty ```suggestion``` block is GitHub's native "delete
            # this line" — no replacement text to show, so skip the
            # "apply manually" code block a real replacement would get.
            return (
                "---\n\n"
                f"## {sev_icon} {sev.capitalize()} — {category}\n\n"
                "### \U0001f50d Detected\n\n"
                f"```python\n{original_code}\n```\n\n"
                "### \U0001f4cb Issue\n\n"
                f"> {message}\n\n"
                "### ✅ Auto Fix — Remove this line\n\n"
                "```suggestion\n```\n\n"
                f"> {explanation}\n\n"
                "---\n"
                "*\U0001f916 AI Code Review \xb7 Click **Commit suggestion** above to remove it instantly*"
            )

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
        suggestion_text = "" if fix_code == DELETE_LINE_SENTINEL else fix_code
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
                            "body": f"```suggestion\n{suggestion_text}\n```",
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