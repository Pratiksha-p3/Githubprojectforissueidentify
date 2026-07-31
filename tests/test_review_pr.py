from src.cli import review_pr


class _FakeGitHubClient:
    def __init__(self, files, contents, head_sha="abc123"):
        self._files = files
        self._contents = contents
        self._head_sha = head_sha
        self.published = []
        self.review_comments = []

    def get_pull_request(self, repo, pr_number):
        return {"number": pr_number, "head": {"sha": self._head_sha}}

    def list_pr_files(self, repo, pr_number):
        return self._files

    def get_file_content(self, repo, path, ref):
        return self._contents[path]

    def create_review_comment(self, repo, pr_number, *, commit_id, path, line, body):
        comment = {
            "repo": repo, "pr_number": pr_number, "commit_id": commit_id,
            "path": path, "line": line, "body": body,
        }
        self.review_comments.append(comment)
        return {"id": len(self.review_comments)}


def test_reviews_only_py_files_and_skips_removed_ones():
    client = _FakeGitHubClient(
        files=[
            {"filename": "app.py", "status": "modified"},
            {"filename": "README.md", "status": "modified"},
            {"filename": "old.py", "status": "removed"},
        ],
        contents={"app.py": "x = 1\n"},
    )

    exit_code = review_pr.review_pr("acme/widgets", 4, github_client=client)

    assert exit_code == 0


def test_no_py_files_short_circuits_cleanly():
    client = _FakeGitHubClient(files=[{"filename": "README.md", "status": "modified"}], contents={})

    exit_code = review_pr.review_pr("acme/widgets", 4, github_client=client)

    assert exit_code == 0


def test_combines_findings_across_multiple_files():
    client = _FakeGitHubClient(
        files=[
            {"filename": "a.py", "status": "modified"},
            {"filename": "b.py", "status": "added"},
        ],
        contents={
            "a.py": "def divide(a, b):\n    return a / b\n",
            "b.py": "x = 1\n",
        },
    )

    exit_code = review_pr.review_pr("acme/widgets", 4, include_llm=False, github_client=client)

    # a.py's unguarded division is a WARNING, not CRITICAL -- doesn't block.
    assert exit_code == 0


def test_a_critical_finding_in_any_file_blocks_the_whole_pr():
    client = _FakeGitHubClient(
        files=[{"filename": "a.py", "status": "modified"}],
        contents={
            "a.py": (
                "class Order:\n"
                "    def __init__(self, total):\n"
                "        pass\n"
                "\n"
                "    def show(self):\n"
                "        return self.total\n"
            )
        },
    )

    exit_code = review_pr.review_pr("acme/widgets", 4, include_llm=False, github_client=client)

    assert exit_code == 1


def test_dry_run_by_default_never_calls_publish(monkeypatch):
    published = []
    monkeypatch.setattr(
        review_pr, "publish_review", lambda *a, **k: published.append(1) or {}
    )
    client = _FakeGitHubClient(
        files=[{"filename": "a.py", "status": "modified"}], contents={"a.py": "x = 1\n"}
    )

    review_pr.review_pr("acme/widgets", 4, include_llm=False, github_client=client)

    assert published == []


def test_post_flag_calls_publish_review_exactly_once(monkeypatch):
    published = []

    def fake_publish(result, pr_number, **kwargs):
        published.append((result, pr_number))
        return {"comment_action": "created", "comment_id": 1, "check_run_id": 2}

    monkeypatch.setattr(review_pr, "publish_review", fake_publish)
    client = _FakeGitHubClient(
        files=[
            {"filename": "a.py", "status": "modified"},
            {"filename": "b.py", "status": "modified"},
        ],
        contents={"a.py": "x = 1\n", "b.py": "y = 2\n"},
    )

    review_pr.review_pr("acme/widgets", 4, include_llm=False, post=True, github_client=client)

    # Exactly one combined publish call for the whole PR, not one per file.
    assert len(published) == 1
    assert published[0][1] == 4


def test_combined_result_uses_the_pr_head_sha():
    client = _FakeGitHubClient(
        files=[{"filename": "a.py", "status": "modified"}],
        contents={"a.py": "x = 1\n"},
        head_sha="deadbeef",
    )
    captured = {}

    def fake_review_code(code, filename, **kwargs):
        captured["commit_sha"] = kwargs["commit_sha"]
        from src.core.models import ReviewResult, ReviewStatus

        return ReviewResult(
            repo="acme/widgets", commit_sha=kwargs["commit_sha"],
            status=ReviewStatus.COMPLETED, findings=[],
        )

    import src.cli.review_pr as module

    orig = module.review_code
    module.review_code = fake_review_code
    try:
        module.review_pr("acme/widgets", 4, include_llm=False, github_client=client)
    finally:
        module.review_code = orig

    assert captured["commit_sha"] == "deadbeef"


def test_worst_status_wins_across_files(monkeypatch):
    """One file failing to parse (FAILED) must make the whole PR's
    combined status FAILED, even if every other file is clean -- the
    same "never silently hide a partial failure" principle
    src/core/orchestrator.py applies within a single file. FAILED maps
    to GateDecision.REVIEW_REQUIRED, not BLOCK (src/core/pr_gate.py) --
    asserted here via the actual combined ReviewResult rather than the
    exit code, since exit code 0 vs 1 only distinguishes BLOCK from
    everything else."""
    published = []

    def fake_publish(result, pr_number, **kwargs):
        published.append(result)
        return {"comment_action": "created", "comment_id": 1, "check_run_id": 2}

    monkeypatch.setattr(review_pr, "publish_review", fake_publish)
    client = _FakeGitHubClient(
        files=[
            {"filename": "good.py", "status": "modified"},
            {"filename": "broken.py", "status": "modified"},
        ],
        contents={"good.py": "x = 1\n", "broken.py": "def broken(:\n    pass\n"},
    )

    review_pr.review_pr("acme/widgets", 4, include_llm=False, post=True, github_client=client)

    assert published[0].status.value == "failed"


def test_post_flag_posts_a_suggestion_comment_for_each_finding_with_a_fix(monkeypatch):
    monkeypatch.setattr(
        review_pr,
        "publish_review",
        lambda *a, **k: {"comment_action": "created", "comment_id": 1, "check_run_id": 2},
    )
    client = _FakeGitHubClient(
        files=[{"filename": "a.py", "status": "modified"}],
        contents={"a.py": "def divide(a, b):\n    return a / b\n"},
    )

    review_pr.review_pr("acme/widgets", 4, include_llm=False, post=True, github_client=client)

    assert len(client.review_comments) == 1
    comment = client.review_comments[0]
    assert comment["path"] == "a.py"
    assert comment["commit_id"] == "abc123"
    assert "```suggestion" in comment["body"]


def test_findings_with_no_fix_get_no_suggestion_comment(monkeypatch):
    monkeypatch.setattr(
        review_pr,
        "publish_review",
        lambda *a, **k: {"comment_action": "created", "comment_id": 1, "check_run_id": 2},
    )
    client = _FakeGitHubClient(
        files=[{"filename": "broken.py", "status": "modified"}],
        contents={"broken.py": "def broken(:\n    pass\n"},
    )

    review_pr.review_pr("acme/widgets", 4, include_llm=False, post=True, github_client=client)

    # The syntax-error Finding has no fix -- nothing to suggest.
    assert client.review_comments == []


def test_dry_run_never_posts_suggestion_comments(monkeypatch):
    client = _FakeGitHubClient(
        files=[{"filename": "a.py", "status": "modified"}],
        contents={"a.py": "def divide(a, b):\n    return a / b\n"},
    )

    review_pr.review_pr("acme/widgets", 4, include_llm=False, github_client=client)

    assert client.review_comments == []


def test_post_fix_suggestions_returns_the_count_posted():
    from src.core.models import ConfidenceTier, Finding, Severity

    client = _FakeGitHubClient(files=[], contents={})
    findings = [
        Finding(
            file="a.py", line=5, category="runtime", severity=Severity.WARNING,
            message="msg", fix="fixed code", confidence=ConfidenceTier.MEDIUM, source="x",
        ),
        Finding(
            file="a.py", line=9, category="syntax", severity=Severity.CRITICAL,
            message="no fix here", fix="", confidence=ConfidenceTier.MEDIUM, source="x",
        ),
    ]

    count = review_pr.post_fix_suggestions(client, "acme/widgets", 4, "abc123", findings)

    assert count == 1
    assert len(client.review_comments) == 1
