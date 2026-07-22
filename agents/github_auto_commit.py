# agents/github_auto_commit.py

import base64
import requests

from ingestion.github_loader import GitHubAuth


class GitHubAutoCommit:

    BASE = "https://api.github.com"

    def __init__(self):
        self.auth = GitHubAuth()

    def update_file(
        self,
        repo: str,
        branch: str,
        file_path: str,
        new_content: str,
        message: str,
    ):

        headers = self.auth.headers()

        r = requests.get(
            f"{self.BASE}/repos/{repo}/contents/{file_path}",
            headers=headers,
            params={"ref": branch},
            timeout=20,
        )

        r.raise_for_status()

        sha = r.json()["sha"]

        payload = {
            "message": message,
            "content": base64.b64encode(
                new_content.encode()
            ).decode(),
            "sha": sha,
            "branch": branch,
        }

        r = requests.put(
            f"{self.BASE}/repos/{repo}/contents/{file_path}",
            headers=headers,
            json=payload,
            timeout=20,
        )

        r.raise_for_status()

        return r.json()