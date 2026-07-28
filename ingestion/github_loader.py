"""
ingestion/github_loader.py
Fetches PR files and content from GitHub API.
Data flow: GitHub API -> PRContext(files) -> data/repo_files/
"""
from __future__ import annotations
import base64, os, time, json
from dataclasses import dataclass, field
from pathlib import Path

import jwt, requests
from config import cfg
from analyzers.unused_imports import DELETE_LINE_SENTINEL


@dataclass
class PRFile:
    filename: str
    status: str
    additions: int
    deletions: int
    patch: str
    full_content: str
    language: str = ""

    @property
    def changed_lines(self) -> list[str]:
        return [l[1:] for l in self.patch.splitlines()
                if l.startswith("+") and not l.startswith("+++")]

    @property
    def total_changes(self) -> int:
        return self.additions + self.deletions


@dataclass
class PRContext:
    repo: str
    pr_number: int
    head_sha: str
    title: str
    description: str
    author: str
    base_branch: str
    head_branch: str
    files: list[PRFile] = field(default_factory=list)


EXTENSION_MAP = {
    ".py":"python",".js":"javascript",".ts":"typescript",
    ".tsx":"typescript",".jsx":"javascript",".go":"go",
    ".java":"java",".rs":"rust",".rb":"ruby",".php":"php",
    ".cs":"csharp",".cpp":"cpp",".c":"c",".swift":"swift",
    ".kt":"kotlin",".sh":"bash",".yaml":"yaml",".yml":"yaml",
    ".json":"json",".sql":"sql",".tf":"terraform",".md":"markdown",
}

def detect_language(filename: str) -> str:
    return EXTENSION_MAP.get(Path(filename).suffix.lower(), "unknown")


def _reconstruct_new_file_from_patch(patch: str) -> str:
    """
    For a brand-new file, the diff patch's hunk *is* the complete file —
    every line is a '+' addition against nothing. Strips the hunk
    header(s) and '+' prefixes to recover the real source text. Only
    valid when every content line in the patch is prefixed '+' (true for
    a new file, false for a modified one — a modified file's patch is a
    partial diff with unchanged context lines and possibly '-' removals,
    which this can't and shouldn't try to turn into "the file").
    """
    lines = []
    for line in patch.splitlines():
        if line.startswith("@@"):
            continue
        if line.startswith("+"):
            lines.append(line[1:])
        elif line.startswith("-"):
            # A '-' line means this wasn't actually a clean new-file
            # patch (or GitHub truncated it) — bail rather than guess.
            return ""
    return "\n".join(lines)


class GitHubAuth:
    def __init__(self):
        self._token = ""
        self._expires = 0.0

    def get_token(self) -> str:
        if self._token and time.time() < self._expires - 60:
            return self._token

        pem_path = cfg.github_pem_path
        app_configured = bool(cfg.github_app_id and cfg.github_install_id and os.path.exists(pem_path))

        # A plain token (a PAT, or the GITHUB_TOKEN Actions injects into
        # every workflow run automatically) is the fallback for contexts
        # like CI that don't have App credentials configured at all —
        # only used when the App flow isn't set up, so it never overrides
        # a working App configuration.
        if not app_configured:
            if cfg.github_token:
                return cfg.github_token
            raise FileNotFoundError(
                f"No GitHub auth configured: PEM key not found at {pem_path} and "
                "GITHUB_TOKEN is not set. Set GITHUB_TOKEN (simplest — in Actions this "
                "is secrets.GITHUB_TOKEN, no setup needed) or configure the GitHub App "
                "(GITHUB_APP_ID/GITHUB_INSTALLATION_ID/GITHUB_APP_PRIVATE_KEY_PATH)."
            )

        with open(pem_path) as f:
            pem_key = f.read()
        now = int(time.time())
        jwt_tok = jwt.encode({"iat":now-60,"exp":now+600,"iss":cfg.github_app_id},
                             pem_key, algorithm="RS256")
        resp = requests.post(
            f"https://api.github.com/app/installations/{cfg.github_install_id}/access_tokens",
            headers={"Authorization":f"Bearer {jwt_tok}",
                     "Accept":"application/vnd.github+json"}, timeout=15)
        resp.raise_for_status()
        self._token = resp.json()["token"]
        self._expires = time.time() + 3500
        return self._token

    def headers(self) -> dict:
        return {"Authorization":f"Bearer {self.get_token()}",
                "Accept":"application/vnd.github+json"}


class GitHubLoader:
    BASE = "https://api.github.com"

    def __init__(self):
        self.auth = GitHubAuth()

    def load_pr(self, repo: str, pr_number: int) -> PRContext:
        pr_meta = self._get(f"/repos/{repo}/pulls/{pr_number}")
        ctx = PRContext(
            repo=repo, pr_number=pr_number,
            head_sha=pr_meta["head"]["sha"],
            title=pr_meta.get("title",""),
            description=pr_meta.get("body","") or "",
            author=pr_meta["user"]["login"],
            base_branch=pr_meta["base"]["ref"],
            head_branch=pr_meta["head"]["ref"],
        )
        raw_files = self._get(f"/repos/{repo}/pulls/{pr_number}/files")
        for rf in raw_files:
            fname = rf["filename"]
            if self._should_skip(fname): continue
            if rf.get("status") == "deleted": continue
            adds, dels = rf.get("additions",0), rf.get("deletions",0)
            if adds + dels > 500:
                print(f"  [loader] Skipping large file: {fname}")
                continue
            content = self._get_file_content(
                    repo,
                    fname,
                    ctx.head_sha
                )

            patch = rf.get("patch", "")

            if not content:
                # A raw diff patch is NOT source code — it has hunk
                # headers ("@@ -0,0 +1,6 @@") and +/- line prefixes mixed
                # into what would otherwise be real lines, so handing it
                # to a syntax/AST-based checker produces exactly what
                # you'd expect from parsing diff syntax as Python:
                # nonsense findings anchored to the wrong lines ("Detected:
                # @@ -0,0 +1,6 @@ ... invalid syntax"). For a brand-new
                # file, the patch happens to BE the complete file (every
                # line is a '+' addition, nothing to reconstruct around),
                # so it can be safely recovered by stripping the diff
                # formatting. For anything else, the patch is only a
                # partial hunk of a larger file — there's no reliable way
                # to recover the real content from it, so leave it empty
                # and let each analyzer's own "no content" guard skip the
                # file cleanly instead of analyzing garbage.
                if rf.get("status") == "added":
                    print(f"  [loader] Content fetch failed for {fname} — "
                          f"reconstructing from patch (new file)")
                    content = _reconstruct_new_file_from_patch(patch)
                else:
                    print(f"  [loader] Content fetch failed for {fname} — "
                          f"leaving empty (patch is a partial diff, not the full file)")
                    content = ""

            ctx.files.append(
                PRFile(
                    filename=fname,
                    status=rf.get("status", "modified"),
                    additions=adds,
                    deletions=dels,
                    patch=patch,
                    full_content=content,
                    language=detect_language(fname),
                )
            )
            print(f"  [loader] Loaded: {fname} (content length: {len(content)})")

        print(f"  [loader] PR #{pr_number}: {len(ctx.files)} files loaded")
        return ctx

    def save_to_disk(self, pr: PRContext) -> Path:
        out = Path("data/repo_files") / pr.repo.replace("/","_") / str(pr.pr_number)
        out.mkdir(parents=True, exist_ok=True)
        for pf in pr.files:
            fp = out / pf.filename
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(pf.full_content, encoding="utf-8")
        meta = {"repo":pr.repo,"pr_number":pr.pr_number,"head_sha":pr.head_sha,
                "title":pr.title,"files":[{"filename":f.filename,"language":f.language,
                "additions":f.additions,"patch":f.patch} for f in pr.files]}
        (out / "_pr_meta.json").write_text(json.dumps(meta, indent=2))
        print(f"  [loader] Saved to {out}")
        return out

    def post_review_comments(
        self,
        repo: str,
        pr_number: int,
        head_sha: str,
        findings: list[dict],
        summary: str = "",
        approved: bool = True,
    ) -> dict:
        """
        Posts the AI review as a GitHub PR review with inline comments.
        Critical/warning findings become inline comments on the exact line.
        A top-level summary comment is always posted.

        Docs: POST /repos/{owner}/{repo}/pulls/{pull_number}/reviews
        """
        comments = []
        for f in findings:
            line = f.get("line", 0)
            if not line or not f.get("file"):
                continue
            severity = f.get("severity", "info").upper()
            icon = {"CRITICAL": "🔴", "WARNING": "🟡", "INFO": "🔵"}.get(severity, "🔵")
            fix = f.get("fix", "").strip()
            needs_manual = bool(f.get("needs_manual_review"))
            manual_reason = (f.get("manual_review_reason") or "").strip()

            if needs_manual:
                # AutoFix looked at this and decided a human has to apply
                # the change - never show a ```suggestion``` block (a
                # one-click "Commit suggestion" button) for something that
                # was never cleared for auto-apply, even if an earlier
                # stage attached a candidate `fix` to the finding.
                cat      = f.get("category", "general")
                msg      = f.get("message", "")
                bad_code = f.get("bad_code", "").strip()

                sections = [f"{icon} **{severity}** \u2014 {cat}", ""]

                if bad_code:
                    sections += [
                        "### \U0001f50d Detected", "",
                        "```python", bad_code, "```", "",
                    ]

                sections += [
                    "### \U0001f4cb Issue", "",
                    f"> {msg}", "",
                    "### \u270b Manual Review Required", "",
                    "No automatic fix was applied for this issue.", "",
                ]

                if manual_reason:
                    sections += [f"**Why:** {manual_reason}", ""]

                if fix == DELETE_LINE_SENTINEL:
                    sections += ["### \U0001f4a1 Suggested change", "",
                                 "Remove this line manually.", ""]
                elif fix:
                    sections += [
                        "### \U0001f4a1 Suggested change (review and apply manually)", "",
                        "```python", fix, "```", "",
                    ]

                sections += [
                    "---",
                    "*\U0001f916 AI Code Review \xb7 Requires human review \u2014 please apply a fix manually*",
                ]
                body = "\n".join(sections)
            elif fix:
                cat      = f.get("category", "general")
                msg      = f.get("message", "")
                bad_code = f.get("bad_code", "").strip()
                reason   = f.get("reason", "").strip()
                is_delete = fix == DELETE_LINE_SENTINEL

                sections = [f"{icon} **{severity}** \u2014 {cat}", ""]

                if bad_code:
                    sections += [
                        "### \U0001f50d Detected", "",
                        "```python", bad_code, "```", "",
                    ]

                if is_delete:
                    sections += [
                        "### \U0001f4cb Issue", "",
                        f"> {msg}", "",
                        "### \u2705 Auto Fix \u2014 Remove this line", "",
                        "```suggestion", "```", "",
                    ]
                else:
                    sections += [
                        "### \U0001f4cb Issue", "",
                        f"> {msg}", "",
                        "### \u2705 Auto Fix", "",
                        "```suggestion", fix, "```", "",
                        "### \U0001f4a1 Or apply manually", "",
                        "```python", fix, "```", "",
                    ]

                if reason:
                    sections += [f"> {reason}", ""]

                sections += [
                    "---",
                    ("*\U0001f916 AI Code Review \xb7 Click **Commit suggestion** above to remove it instantly*"
                     if is_delete else
                     "*\U0001f916 AI Code Review \xb7 Click **Commit suggestion** above to apply instantly*"),
                ]
                body = "\n".join(sections)
            else:
                body = (
                    f"{icon} **{severity}** — {f.get('category', 'general')}\n\n"
                    f"{f.get('message', '')}"
                )
            comments.append({
                "path": f["file"],
                "line": line,
                "side": "RIGHT",
                "body": body,
            })

        event = "REQUEST_CHANGES" if not approved else "COMMENT"

        payload = {
            "commit_id": head_sha,
            "body": summary or "AI Code Review completed.",
            "event": event,
            "comments": comments,
        }

        try:
            resp = requests.post(
                f"{self.BASE}/repos/{repo}/pulls/{pr_number}/reviews",
                headers=self.auth.headers(),
                json=payload,
                timeout=20,
            )
            resp.raise_for_status()
            result = resp.json()
            print(f"  [loader] Posted review with {len(comments)} inline comments")
            return result
        except requests.HTTPError as e:
            # Fallback: if line-anchored comments fail (e.g. line not in diff),
            # post a single summary comment instead so the review isn't lost.
            print(f"  [loader] Inline review failed ({e}), posting summary comment instead")
            return self.post_summary_comment(repo, pr_number, summary, findings)

    def post_summary_comment(
        self,
        repo: str,
        pr_number: int,
        summary: str,
        findings: list[dict],
    ) -> dict:
        """Fallback: posts a single issue-style comment listing all findings."""
        lines = [f"## AI Code Review\n\n{summary}\n"]
        for f in findings:
            sev = f.get("severity", "info").upper()
            icon = {"CRITICAL": "🔴", "WARNING": "🟡", "INFO": "🔵"}.get(sev, "🔵")
            lines.append(
                f"\n{icon} **{sev}** — `{f.get('file','')}:{f.get('line',0)}`\n"
                f"{f.get('message','')}\n\n"
                f"Fix: {f.get('fix','')}\n"
            )
        body = "\n".join(lines)

        resp = requests.post(
            f"{self.BASE}/repos/{repo}/issues/{pr_number}/comments",
            headers=self.auth.headers(),
            json={"body": body},
            timeout=20,
        )
        resp.raise_for_status()
        return resp.json()

    def _get_file_content(self, repo: str, path: str, ref: str) -> str:
        try:
            data = self._get(f"/repos/{repo}/contents/{path}", params={"ref":ref})
            return base64.b64decode(data.get("content","")).decode("utf-8", errors="replace")
        except Exception as e:
            print(
        f"  [loader] Could not fetch "
        f"{path}: {e}"
        )

            print(
        f"  [loader] ref={ref}"
        )

            return ""

    def _get(self, path: str, params: dict = None):
        r = requests.get(f"{self.BASE}{path}", headers=self.auth.headers(),
                         params=params or {}, timeout=20)
        if r.status_code >= 400:
            print(f"[github] URL: {r.url}")
            print(f"[github] Status: {r.status_code}")
            print(f"[github] Body: {r.text[:500]}")
        r.raise_for_status()
        return r.json()

    @staticmethod
    def _should_skip(filename: str) -> bool:
        return any(p in filename for p in cfg.skip_patterns)


class MockGitHubLoader:
    """Offline mock — use when GitHub App is not set up yet."""
    def load_pr(self, repo: str, pr_number: int) -> PRContext:
        files = [
            PRFile(
                filename="src/auth/login.py", status="modified",
                additions=14, deletions=2,
                patch=(
                    "@@ -10,8 +10,22 @@\n"
                    " import sqlite3\n"
                    "+SECRET_KEY = 'hardcoded-secret-abc123'\n"
                    "+\n"
                    "+def login(username, password):\n"
                    "+    conn = sqlite3.connect('users.db')\n"
                    "+    query = f\"SELECT * FROM users WHERE name='{username}'\"\n"
                    "+    result = conn.execute(query)\n"
                    "+    return result.fetchone()\n"
                ),
                full_content=(
                    "import sqlite3\n\nSECRET_KEY = 'hardcoded-secret-abc123'\n\n"
                    "def login(username, password):\n"
                    "    conn = sqlite3.connect('users.db')\n"
                    "    query = f\"SELECT * FROM users WHERE name='{username}'\"\n"
                    "    result = conn.execute(query)\n"
                    "    return result.fetchone()\n\ndef logout(user_id): pass\n"
                ),
                language="python",
            ),
            PRFile(
                filename="src/utils/hash.py", status="added",
                additions=4, deletions=0,
                patch=(
                    "@@ -0,0 +1,4 @@\n"
                    "+import hashlib\n"
                    "+def hash_password(password: str) -> str:\n"
                    "+    return hashlib.md5(password.encode()).hexdigest()\n"
                ),
                full_content=(
                    "import hashlib\n"
                    "def hash_password(password: str) -> str:\n"
                    "    return hashlib.md5(password.encode()).hexdigest()\n"
                ),
                language="python",
            ),
        ]
        return PRContext(
            repo=repo, pr_number=pr_number, head_sha="mock-sha-abc123",
            title="Add authentication", description="Login + password hashing",
            author="dev", base_branch="main", head_branch="feature/auth",
            files=files,
        )