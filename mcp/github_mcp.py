from ingestion.github_loader import GitHubLoader


class GitHubMCP:

    def __init__(self):
        self.loader = GitHubLoader()

    def get_pr(self, repo: str, pr_number: int):
        return self.loader.load_pr(repo, pr_number)

    def post_review(
        self,
        repo: str,
        pr_number: int,
        head_sha: str,
        findings: list,
        summary: str,
        approved: bool = False,
    ):
        return self.loader.post_review_comments(
            repo=repo,
            pr_number=pr_number,
            head_sha=head_sha,
            findings=findings,
            summary=summary,
            approved=approved,
        )