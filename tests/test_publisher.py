from src.core.models import Finding, ReviewResult, ReviewStatus, Severity
from src.integrations.publisher import publish_review


class _FakeGitHubClient:
    def __init__(self):
        self.posted_comments = []
        self.updated_comments = []
        self.check_runs = []

    def post_issue_comment(self, repo, issue_number, body):
        self.posted_comments.append((repo, issue_number, body))
        return {"id": 111}

    def update_issue_comment(self, repo, comment_id, body):
        self.updated_comments.append((repo, comment_id, body))
        return {"id": comment_id}

    def create_check_run(self, repo, commit_sha, *, decision, summary, title="AI Code Review"):
        self.check_runs.append((repo, commit_sha, decision, summary))
        return {"id": 222}


class _FakeCommentStore:
    def __init__(self, existing_comment_id=None):
        self._existing = existing_comment_id
        self.saved = []

    def get_comment_id(self, repo, commit_sha):
        return self._existing

    def set_comment_id(self, repo, commit_sha, comment_id):
        self.saved.append((repo, commit_sha, comment_id))


def make_result(status=ReviewStatus.COMPLETED, findings=None) -> ReviewResult:
    return ReviewResult(
        repo="acme/widgets", commit_sha="abc123", status=status, findings=findings or []
    )


def test_posts_new_comment_when_none_exists():
    client = _FakeGitHubClient()
    store = _FakeCommentStore(existing_comment_id=None)

    outcome = publish_review(
        make_result(), pr_number=7, github_client=client, comment_store=store
    )

    assert outcome["comment_action"] == "created"
    assert len(client.posted_comments) == 1
    assert client.posted_comments[0][1] == 7
    assert store.saved == [("acme/widgets", "abc123", 111)]


def test_updates_existing_comment_instead_of_duplicating():
    client = _FakeGitHubClient()
    store = _FakeCommentStore(existing_comment_id=555)

    outcome = publish_review(
        make_result(), pr_number=7, github_client=client, comment_store=store
    )

    assert outcome["comment_action"] == "updated"
    assert client.posted_comments == []  # never posted a new one
    assert client.updated_comments[0][1] == 555


def test_always_creates_a_check_run():
    client = _FakeGitHubClient()
    store = _FakeCommentStore()

    publish_review(make_result(), pr_number=7, github_client=client, comment_store=store)

    assert len(client.check_runs) == 1


def test_comment_body_includes_findings():
    client = _FakeGitHubClient()
    store = _FakeCommentStore()
    result = make_result(
        findings=[
            Finding(
                file="app.py", line=3, category="runtime",
                severity=Severity.CRITICAL, message="boom",
            )
        ]
    )

    publish_review(result, pr_number=7, github_client=client, comment_store=store)

    body = client.posted_comments[0][2]
    assert "app.py:3" in body
    assert "boom" in body


def test_degraded_review_never_produces_a_success_check_run():
    client = _FakeGitHubClient()
    store = _FakeCommentStore()
    result = make_result(status=ReviewStatus.DEGRADED)

    publish_review(result, pr_number=7, github_client=client, comment_store=store)

    decision_used = client.check_runs[0][2]
    assert decision_used.value != "approve"
