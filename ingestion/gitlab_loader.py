"""
ingestion/gitlab_loader.py

GitLab counterpart to ingestion/github_loader.py — same PRFile/PRContext
shape (reused, not duplicated) so every downstream agent (reviewer,
security, autofix, ...) works against either provider without caring
which one loaded the merge request.

Auth: a personal/project access token via GITLAB_TOKEN (simpler than
GitHub's App+JWT flow — GitLab's REST API just takes a bearer token).
Works against self-hosted GitLab too via GITLAB_URL.

Inert until GITLAB_TOKEN is set — GitLabLoader() raises clearly if it's
missing rather than failing deep in an API call.

Not implemented yet: inline diff comments (GitLab's discussions API needs
a position object with base/start/head SHAs per file) — post_review_comments
posts a single summary note instead. Swap in a real Discussions-API call
once this is exercised against a live GitLab project.
"""
from __future__ import annotations

import urllib.parse
import requests

from config import cfg
from ingestion.github_loader import PRFile, PRContext, detect_language


class GitLabLoader:
    def __init__(self):
        if not cfg.gitlab_token:
            raise RuntimeError(
                "GITLAB_TOKEN is not set — GitLabLoader needs a personal or "
                "project access token (Settings > Access Tokens in GitLab)."
            )
        self.base = f"{cfg.gitlab_url.rstrip('/')}/api/v4"

    def load_pr(self, repo: str, pr_number: int) -> PRContext:
        """`repo` is the GitLab project path, e.g. 'group/subgroup/project'.
        `pr_number` is the merge request IID (the number shown in the UI)."""
        project_id = self._encode_project(repo)
        mr = self._get(f"/projects/{project_id}/merge_requests/{pr_number}")

        ctx = PRContext(
            repo=repo, pr_number=pr_number,
            head_sha=mr["sha"] or mr.get("diff_refs", {}).get("head_sha", ""),
            title=mr.get("title", ""),
            description=mr.get("description", "") or "",
            author=mr.get("author", {}).get("username", ""),
            base_branch=mr.get("target_branch", ""),
            head_branch=mr.get("source_branch", ""),
        )

        changes = self._get(f"/projects/{project_id}/merge_requests/{pr_number}/changes")
        for change in changes.get("changes", []):
            fname = change.get("new_path", "")
            if not fname or self._should_skip(fname) or change.get("deleted_file"):
                continue

            content = self._get_file_content(project_id, fname, ctx.head_sha)
            patch = change.get("diff", "")
            if not content:
                content = patch

            additions = sum(
                1 for line in patch.splitlines()
                if line.startswith("+") and not line.startswith("+++")
            )
            deletions = sum(
                1 for line in patch.splitlines()
                if line.startswith("-") and not line.startswith("---")
            )
            if additions + deletions > 500:
                print(f"  [gitlab-loader] Skipping large file: {fname}")
                continue

            ctx.files.append(PRFile(
                filename=fname,
                status="added" if change.get("new_file") else "modified",
                additions=additions,
                deletions=deletions,
                patch=patch,
                full_content=content,
                language=detect_language(fname),
            ))

        print(f"  [gitlab-loader] MR !{pr_number}: {len(ctx.files)} files loaded")
        return ctx

    def post_review_comments(
        self,
        repo: str,
        pr_number: int,
        head_sha: str,
        findings: list[dict],
        summary: str = "",
        approved: bool = True,
    ) -> dict:
        """Posts the AI review as a single note on the merge request."""
        return self.post_summary_comment(repo, pr_number, summary, findings)

    def post_summary_comment(
        self,
        repo: str,
        pr_number: int,
        summary: str,
        findings: list[dict],
    ) -> dict:
        project_id = self._encode_project(repo)
        lines = [f"## AI Code Review\n\n{summary}\n"]
        for f in findings:
            sev = f.get("severity", "info").upper()
            icon = {"CRITICAL": "\U0001f534", "WARNING": "\U0001f7e1", "INFO": "\U0001f535"}.get(sev, "\U0001f535")
            lines.append(
                f"\n{icon} **{sev}** — `{f.get('file','')}:{f.get('line',0)}`\n"
                f"{f.get('message','')}\n\n"
                f"Fix: {f.get('fix','')}\n"
            )
        body = "\n".join(lines)

        resp = requests.post(
            f"{self.base}/projects/{project_id}/merge_requests/{pr_number}/notes",
            headers=self._headers(),
            json={"body": body},
            timeout=20,
        )
        resp.raise_for_status()
        return resp.json()

    # ── Internal ──────────────────────────────────────────

    def _get_file_content(self, project_id: str, path: str, ref: str) -> str:
        try:
            encoded_path = urllib.parse.quote(path, safe="")
            resp = requests.get(
                f"{self.base}/projects/{project_id}/repository/files/{encoded_path}/raw",
                headers=self._headers(),
                params={"ref": ref},
                timeout=20,
            )
            resp.raise_for_status()
            return resp.text
        except Exception as e:
            print(f"  [gitlab-loader] Could not fetch {path}: {e}")
            return ""

    def _get(self, path: str, params: dict | None = None):
        resp = requests.get(f"{self.base}{path}", headers=self._headers(),
                             params=params or {}, timeout=20)
        if resp.status_code >= 400:
            print(f"[gitlab] URL: {resp.url}")
            print(f"[gitlab] Status: {resp.status_code}")
            print(f"[gitlab] Body: {resp.text[:500]}")
        resp.raise_for_status()
        return resp.json()

    def _headers(self) -> dict:
        return {"PRIVATE-TOKEN": cfg.gitlab_token}

    @staticmethod
    def _encode_project(repo: str) -> str:
        return urllib.parse.quote(repo, safe="")

    @staticmethod
    def _should_skip(filename: str) -> bool:
        return any(p in filename for p in cfg.skip_patterns)


class MockGitLabLoader:
    """Offline mock — mirrors MockGitHubLoader for testing without a token."""
    def load_pr(self, repo: str, pr_number: int) -> PRContext:
        files = [
            PRFile(
                filename="src/auth/login.py", status="modified",
                additions=6, deletions=0,
                patch=(
                    "@@ -1,2 +1,8 @@\n"
                    " import sqlite3\n"
                    "+SECRET_KEY = 'hardcoded-secret-abc123'\n"
                ),
                full_content=(
                    "import sqlite3\n\nSECRET_KEY = 'hardcoded-secret-abc123'\n"
                ),
                language="python",
            ),
        ]
        return PRContext(
            repo=repo, pr_number=pr_number, head_sha="mock-gitlab-sha",
            title="Add authentication", description="Login logic",
            author="dev", base_branch="main", head_branch="feature/auth",
            files=files,
        )

    def post_review_comments(self, **kwargs) -> dict:
        print("  [mock-gitlab-loader] Skipping post (mock mode)")
        return {}
