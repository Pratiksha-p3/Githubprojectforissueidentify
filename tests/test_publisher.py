from src.core.models import ConfidenceTier, Finding, ReviewResult, ReviewStatus, Severity
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


def test_comment_body_includes_the_fix_suggestion_and_reason_right_after_the_finding():
    """The whole point of this: someone reading only the PR's Conversation
    tab (not the Files Changed tab's inline suggestions) must be able to
    see, right after each finding, what the suggested fix is and why they
    still have to apply it themselves -- not just the bare message."""
    client = _FakeGitHubClient()
    store = _FakeCommentStore()
    result = make_result(
        findings=[
            Finding(
                file="app.py", line=3, category="runtime", severity=Severity.WARNING,
                message="Division by parameter 'b' with no zero-check",
                fix="if b == 0:\n    raise ZeroDivisionError",
                confidence=ConfidenceTier.MEDIUM, source="division_guard_checker",
            )
        ]
    )

    publish_review(result, pr_number=7, github_client=client, comment_store=store)

    body = client.posted_comments[0][2]
    finding_idx = body.index("app.py:3")
    fix_idx = body.index("if b == 0:")
    reason_idx = body.index("Why this is still here")
    reason_text_idx = body.index("judgment call")
    assert finding_idx < fix_idx < reason_idx < reason_text_idx


def test_comment_body_includes_remediation_guidance_for_a_detection_only_finding():
    """A detection-only finding has no fix by design -- without
    remediation guidance, "why this is still here" is the only
    actionable text a human reading the summary comment has at all."""
    client = _FakeGitHubClient()
    store = _FakeCommentStore()
    result = make_result(
        findings=[
            Finding(
                file="app.py", line=12, category="security", severity=Severity.CRITICAL,
                message="SQL query is built via string interpolation", fix="",
                confidence=ConfidenceTier.MEDIUM, source="sql_injection_checker",
            )
        ]
    )

    publish_review(result, pr_number=7, github_client=client, comment_store=store)

    body = client.posted_comments[0][2]
    assert "How to resolve it" in body
    assert "parameterized" in body.lower()


def test_comment_body_omits_fix_block_when_finding_has_no_fix():
    client = _FakeGitHubClient()
    store = _FakeCommentStore()
    result = make_result(
        findings=[
            Finding(
                file="app.py", line=3, category="syntax", severity=Severity.CRITICAL,
                message="File does not parse", fix="", confidence=ConfidenceTier.MEDIUM,
            )
        ]
    )

    publish_review(result, pr_number=7, github_client=client, comment_store=store)

    body = client.posted_comments[0][2]
    assert "Suggested fix" not in body
    assert "No fix was generated" in body


def test_comment_body_reason_for_high_confidence_reflects_auto_apply_flag():
    client = _FakeGitHubClient()
    store = _FakeCommentStore()
    result = make_result(
        findings=[
            Finding(
                file="app.py", line=2, category="syntax", severity=Severity.CRITICAL,
                message="missing colon", fix="if x:", confidence=ConfidenceTier.HIGH,
            )
        ]
    )

    publish_review(result, pr_number=7, github_client=client, comment_store=store, auto_apply=False)
    body_without = client.posted_comments[0][2]
    assert "--auto-apply" in body_without

    client2 = _FakeGitHubClient()
    store2 = _FakeCommentStore()
    publish_review(
        result, pr_number=8, github_client=client2, comment_store=store2, auto_apply=True
    )
    body_with = client2.posted_comments[0][2]
    assert "conflicted" in body_with


def test_degraded_review_never_produces_a_success_check_run():
    client = _FakeGitHubClient()
    store = _FakeCommentStore()
    result = make_result(status=ReviewStatus.DEGRADED)

    publish_review(result, pr_number=7, github_client=client, comment_store=store)

    decision_used = client.check_runs[0][2]
    assert decision_used.value != "approve"
