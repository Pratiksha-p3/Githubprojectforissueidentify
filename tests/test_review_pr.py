from src.cli import review_pr


class _FakeGitHubClient:
    def __init__(self, files, contents, head_sha="abc123", branch="main"):
        self._files = files
        self._contents = dict(contents)
        self._head_sha = head_sha
        self._branch = branch
        self.published = []
        self.review_comments = []
        self.pushed_files: list[dict] = []

    def get_pull_request(self, repo, pr_number):
        return {"number": pr_number, "head": {"sha": self._head_sha, "ref": self._branch}}

    def list_pr_files(self, repo, pr_number):
        return self._files

    def get_file_content(self, repo, path, ref):
        return self._contents[path]

    def _get_file_metadata(self, repo, path, ref):
        return self._contents[path], f"blobsha-{path}"

    def update_file_content(self, repo, path, *, message, content, sha=None, branch):
        self._contents[path] = content
        self._head_sha = f"{self._head_sha}-fix{len(self.pushed_files) + 1}"
        self.pushed_files.append(
            {"path": path, "message": message, "content": content, "sha": sha, "branch": branch}
        )
        return {"commit": {"sha": self._head_sha}}

    def create_review_comment(
        self, repo, pr_number, *, commit_id, path, line, body, start_line=None
    ):
        comment = {
            "repo": repo, "pr_number": pr_number, "commit_id": commit_id,
            "path": path, "line": line, "body": body, "start_line": start_line,
        }
        self.review_comments.append(comment)
        return {"id": len(self.review_comments)}

    def list_review_comments(self, repo, pr_number):
        return list(self.review_comments)


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


def test_dry_run_prints_auto_fix_summary(capsys):
    client = _FakeGitHubClient(
        files=[{"filename": "a.py", "status": "modified"}],
        contents={
            "a.py": (
                "def get_user(cursor, user_id):\n"
                '    query = f"SELECT * FROM users WHERE id = {user_id}"\n'
                "    cursor.execute(query)\n"
            )
        },
    )

    review_pr.review_pr("acme/widgets", 4, include_llm=False, github_client=client)

    out = capsys.readouterr().out
    assert "Auto-fixed:              0" in out
    assert "Needs manual review:     1" in out
    assert "sql_injection_checker" in out
    assert "needs manual investigation" in out


def test_high_confidence_finding_without_auto_apply_still_counts_as_manual_review(capsys):
    """Regression test for a real gap: a HIGH-confidence finding (e.g.
    the missing-colon syntax-error fix) that ISN'T actually applied this
    run (because --auto-apply wasn't passed) used to be counted in
    NEITHER bucket -- not "auto-fixed" (nothing was applied) and not
    "needs manual review" (confidence-based logic excluded HIGH). Every
    finding still present after the run must land in exactly one of the
    two counts, with no gap."""
    client = _FakeGitHubClient(
        files=[{"filename": "a.py", "status": "modified"}],
        contents={"a.py": "def f(rating):\n    if rating >= 4\n        return 1\n    return 0\n"},
    )

    review_pr.review_pr("acme/widgets", 4, include_llm=False, github_client=client)

    out = capsys.readouterr().out
    assert "Auto-fixed:              0" in out
    assert "Needs manual review:     1" in out  # not 0 -- this is the bug being tested
    assert "--auto-apply" in out


def test_dry_run_prints_the_suggested_fix_for_findings_that_have_one(capsys):
    client = _FakeGitHubClient(
        files=[{"filename": "a.py", "status": "modified"}],
        contents={"a.py": "def divide(a, b):\n    return a / b\n"},
    )

    review_pr.review_pr("acme/widgets", 4, include_llm=False, github_client=client)

    out = capsys.readouterr().out
    assert "Suggested fix" in out
    assert "raise ZeroDivisionError" in out


def test_dry_run_notes_when_a_finding_has_no_fix(capsys):
    client = _FakeGitHubClient(
        files=[{"filename": "a.py", "status": "modified"}],
        contents={
            "a.py": (
                "def get_user(cursor, user_id):\n"
                '    query = f"SELECT * FROM users WHERE id = {user_id}"\n'
                "    cursor.execute(query)\n"
            )
        },
    )

    review_pr.review_pr("acme/widgets", 4, include_llm=False, github_client=client)

    out = capsys.readouterr().out
    assert "no auto-generated fix" in out


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


def test_auto_apply_pushes_the_fix_and_reports_it_as_auto_fixed(monkeypatch, capsys):
    monkeypatch.setattr(
        review_pr,
        "publish_review",
        lambda *a, **k: {"comment_action": "created", "comment_id": 1, "check_run_id": 2},
    )
    client = _FakeGitHubClient(
        files=[{"filename": "a.py", "status": "modified"}],
        contents={"a.py": "def divide(a, b):\n    return a / b\n"},
    )

    exit_code = review_pr.review_pr(
        "acme/widgets", 4, include_llm=False, post=True, auto_apply=True, github_client=client
    )

    assert exit_code == 0  # the WARNING is now fixed -- nothing left to block on
    assert len(client.pushed_files) == 1
    assert "if b == 0" in client.pushed_files[0]["content"]

    out = capsys.readouterr().out
    assert "Auto-fixed:              1" in out
    assert "Needs manual review:     0" in out


def test_auto_apply_re_review_uses_the_pushs_own_sha_not_a_fresh_pr_fetch(monkeypatch):
    """Regression, confirmed live on a real PR: GitHub's PR-metadata
    endpoint (get_pull_request) can still return the PRE-push sha for a
    brief window right after pushing (eventual consistency), even though
    the branch's actual content is already updated. Re-fetching PR
    metadata to find the new head_sha for the re-review re-fetched the
    STALE file and reported the finding --auto-apply had just fixed as
    still present. The push's own response already IS the new head --
    must be used directly, never re-queried a second time."""
    monkeypatch.setattr(
        review_pr,
        "publish_review",
        lambda *a, **k: {"comment_action": "created", "comment_id": 1, "check_run_id": 2},
    )

    class _CountingClient(_FakeGitHubClient):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self.get_pull_request_calls = 0

        def get_pull_request(self, repo, pr_number):
            self.get_pull_request_calls += 1
            return super().get_pull_request(repo, pr_number)

    client = _CountingClient(
        files=[{"filename": "a.py", "status": "modified"}],
        contents={"a.py": "def divide(a, b):\n    return a / b\n"},
    )

    review_pr.review_pr(
        "acme/widgets", 4, include_llm=False, post=True, auto_apply=True, github_client=client
    )

    # Exactly once -- the initial fetch to find head_sha at the very
    # start. A second call after auto-apply is the actual regression:
    # re-querying PR metadata that can still be stale, instead of using
    # the push response's own (guaranteed fresh) sha.
    assert client.get_pull_request_calls == 1


def test_auto_apply_deletes_an_unused_import_end_to_end(monkeypatch, capsys):
    """The whole point of fix_is_deletion: --auto-apply must be able to
    actually remove a real unused import, not just report it. Runs the
    real unused_import_checker (a real ruff subprocess), not a mock."""
    monkeypatch.setattr(
        review_pr,
        "publish_review",
        lambda *a, **k: {"comment_action": "created", "comment_id": 1, "check_run_id": 2},
    )
    client = _FakeGitHubClient(
        files=[{"filename": "a.py", "status": "modified"}],
        contents={"a.py": "import os\nimport sys\n\ndef f():\n    return sys.argv\n"},
    )

    review_pr.review_pr(
        "acme/widgets", 4, include_llm=False, post=True, auto_apply=True, github_client=client
    )

    assert len(client.pushed_files) == 1
    pushed_content = client.pushed_files[0]["content"]
    assert "import os" not in pushed_content
    assert "import sys" in pushed_content

    out = capsys.readouterr().out
    assert "Auto-fixed:              1" in out


def test_auto_applied_finding_gets_no_suggestion_comment_in_the_same_run(monkeypatch):
    """A line that --auto-apply already fixed and pushed must never ALSO
    get a redundant "Apply suggestion" comment in the same run -- that
    would be posting a suggestion for a change that's already been made.
    Guaranteed structurally: after auto-apply, review_pr() re-reviews
    the NEW commit before calling post_fix_suggestions(), so combined
    .findings (what gets posted) only ever contains what's still
    actually present, never what auto-apply already resolved."""
    monkeypatch.setattr(
        review_pr,
        "publish_review",
        lambda *a, **k: {"comment_action": "created", "comment_id": 1, "check_run_id": 2},
    )
    client = _FakeGitHubClient(
        files=[{"filename": "a.py", "status": "modified"}],
        contents={"a.py": "def divide(a, b):\n    return a / b\n"},
    )

    review_pr.review_pr(
        "acme/widgets", 4, include_llm=False, post=True, auto_apply=True, github_client=client
    )

    assert client.review_comments == []


def test_auto_apply_re_reviews_after_pushing_so_the_posted_comment_is_accurate(monkeypatch):
    monkeypatch.setattr(
        review_pr,
        "publish_review",
        lambda *a, **k: {"comment_action": "created", "comment_id": 1, "check_run_id": 2},
    )
    client = _FakeGitHubClient(
        files=[{"filename": "a.py", "status": "modified"}],
        contents={"a.py": "def divide(a, b):\n    return a / b\n"},
    )

    review_pr.review_pr(
        "acme/widgets", 4, include_llm=False, post=True, auto_apply=True, github_client=client
    )

    # After auto-apply, the file's own content in the fake client is the
    # patched version -- re-reviewing it must find nothing left.
    assert "if b == 0" in client._contents["a.py"]


def test_auto_apply_leaves_findings_with_no_fix_for_manual_review(monkeypatch, capsys):
    monkeypatch.setattr(
        review_pr,
        "publish_review",
        lambda *a, **k: {"comment_action": "created", "comment_id": 1, "check_run_id": 2},
    )
    client = _FakeGitHubClient(
        files=[{"filename": "a.py", "status": "modified"}],
        contents={
            "a.py": (
                "def get_user(cursor, user_id):\n"
                '    query = f"SELECT * FROM users WHERE id = {user_id}"\n'
                "    cursor.execute(query)\n"
            )
        },
    )

    review_pr.review_pr(
        "acme/widgets", 4, include_llm=False, post=True, auto_apply=True, github_client=client
    )

    # sql_injection_checker never generates a fix -- nothing to push.
    assert client.pushed_files == []
    out = capsys.readouterr().out
    assert "Auto-fixed:              0" in out
    assert "Needs manual review:     1" in out


def test_auto_apply_without_post_is_rejected_by_the_cli(capsys):
    exit_code = review_pr.main(["acme/widgets", "4", "--auto-apply"])
    assert exit_code == 1
    out = capsys.readouterr().out
    assert "requires --post" in out


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


def test_multi_agent_flag_is_passed_through_to_review_code():
    client = _FakeGitHubClient(
        files=[{"filename": "a.py", "status": "modified"}], contents={"a.py": "x = 1\n"}
    )
    captured = {}

    def fake_review_code(code, filename, **kwargs):
        captured["use_multi_agent"] = kwargs["use_multi_agent"]
        from src.core.models import ReviewResult, ReviewStatus

        return ReviewResult(
            repo="acme/widgets", commit_sha=kwargs["commit_sha"],
            status=ReviewStatus.COMPLETED, findings=[],
        )

    import src.cli.review_pr as module

    orig = module.review_code
    module.review_code = fake_review_code
    try:
        module.review_pr(
            "acme/widgets", 4, include_llm=False, use_multi_agent=True, github_client=client
        )
    finally:
        module.review_code = orig

    assert captured["use_multi_agent"] is True


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


def test_suggestion_comment_body_explains_why_manual_review_is_needed(monkeypatch):
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

    body = client.review_comments[0]["body"]
    assert "Why this needs a human" in body
    assert "judgment call" in body  # division_guard_checker is MEDIUM confidence
    assert "medium confidence" in body


def test_suggestion_comment_body_for_a_high_confidence_fix_does_not_ask_why_it_needs_a_human(
    monkeypatch,
):
    """A HIGH-confidence fix (e.g. the missing-colon syntax-error fix)
    genuinely doesn't need a human to look at it first -- the comment
    must not ask "why does this need a human" and then answer "it
    doesn't", which is what the wording used to do before this test."""
    monkeypatch.setattr(
        review_pr,
        "publish_review",
        lambda *a, **k: {"comment_action": "created", "comment_id": 1, "check_run_id": 2},
    )
    client = _FakeGitHubClient(
        files=[{"filename": "a.py", "status": "modified"}],
        contents={"a.py": "def f(rating):\n    if rating >= 4\n        return 1\n    return 0\n"},
    )

    review_pr.review_pr("acme/widgets", 4, include_llm=False, post=True, github_client=client)

    body = client.review_comments[0]["body"]
    assert "Why this needs a human" not in body
    assert "high confidence" in body.lower()
    assert "--auto-apply" in body


def test_post_summary_reports_the_suggestion_count_distinctly_from_auto_fixed(
    monkeypatch, capsys
):
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

    out = capsys.readouterr().out
    assert "Auto-fixed:              0" in out
    assert "Fix suggestions posted to GitHub: 1" in out


def test_findings_with_no_fix_get_a_plain_comment_not_a_suggestion(monkeypatch):
    """A finding with no fix (e.g. this syntax error shape, or a
    detection-only checker like sql_injection_checker) can't get a
    ```suggestion``` block -- there's nothing to suggest -- but it must
    still get pointed at directly on the PR, not just buried in the
    summary comment."""
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

    assert len(client.review_comments) == 1
    comment = client.review_comments[0]
    assert "```suggestion" not in comment["body"]
    assert "does not parse" in comment["body"]


def test_dry_run_never_posts_suggestion_comments(monkeypatch):
    client = _FakeGitHubClient(
        files=[{"filename": "a.py", "status": "modified"}],
        contents={"a.py": "def divide(a, b):\n    return a / b\n"},
    )

    review_pr.review_pr("acme/widgets", 4, include_llm=False, github_client=client)

    assert client.review_comments == []


def test_check_run_403_does_not_prevent_fix_suggestions_from_posting(monkeypatch):
    """A PAT (this project's only supported auth mode) can't create
    Check Runs -- GitHub returns 403 even with full repo permissions,
    since that endpoint requires GitHub App auth. publish_review()
    raises in that case, but the summary comment it posts beforehand
    already succeeded, and the fix-suggestion comments below don't
    depend on the check run at all -- a real gap, not a reason to also
    lose those."""
    import requests

    def fake_publish(*a, **k):
        raise requests.exceptions.HTTPError("403 Client Error: Forbidden")

    monkeypatch.setattr(review_pr, "publish_review", fake_publish)
    client = _FakeGitHubClient(
        files=[{"filename": "a.py", "status": "modified"}],
        contents={"a.py": "def divide(a, b):\n    return a / b\n"},
    )

    exit_code = review_pr.review_pr(
        "acme/widgets", 4, include_llm=False, post=True, github_client=client
    )

    assert exit_code == 0  # still reflects the review decision, not the publish failure
    assert len(client.review_comments) == 1  # suggestion still posted despite the 403


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


def test_post_fix_suggestions_sends_an_empty_suggestion_block_for_a_deletion():
    """A deletion fix (fix_is_deletion=True, fix="") must still get
    posted as a real suggestion comment -- with an EMPTY suggestion
    block (```suggestion\\n```` with no content line), which is how
    GitHub represents "replace these lines with nothing". Inserting the
    empty fix string as its own line would leave one blank line behind
    instead of a true deletion."""
    from src.core.models import ConfidenceTier, Finding, Severity

    client = _FakeGitHubClient(files=[], contents={})
    finding = Finding(
        file="a.py", line=1, category="style", severity=Severity.INFO,
        message="'os' imported but never used", fix="", fix_is_deletion=True,
        confidence=ConfidenceTier.MEDIUM, source="unused_import_checker",
    )

    count = review_pr.post_fix_suggestions(client, "acme/widgets", 4, "abc123", [finding])

    assert count == 1
    body = client.review_comments[0]["body"]
    assert "```suggestion\n```" in body


def test_post_fix_suggestions_skips_a_line_that_already_has_a_comment():
    """Regression: re-running --post on a PR whose findings hadn't
    changed used to post a FRESH duplicate suggestion every time. If a
    human clicked "Apply suggestion" on more than one of the duplicates
    for the same finding, each click independently applied the same
    patch -- confirmed live as the actual cause of real file corruption
    (a guard block duplicated, an assignment line duplicated)."""
    from src.core.models import ConfidenceTier, Finding, Severity

    client = _FakeGitHubClient(files=[], contents={})
    client.review_comments.append(
        {"path": "a.py", "line": 5, "body": "already posted from a prior run"}
    )
    finding = Finding(
        file="a.py", line=5, category="runtime", severity=Severity.WARNING,
        message="msg", fix="fixed code", confidence=ConfidenceTier.MEDIUM, source="x",
    )

    count = review_pr.post_fix_suggestions(client, "acme/widgets", 4, "abc123", [finding])

    assert count == 0
    assert len(client.review_comments) == 1  # still just the pre-existing one


def test_post_fix_suggestions_still_posts_for_a_different_line():
    from src.core.models import ConfidenceTier, Finding, Severity

    client = _FakeGitHubClient(files=[], contents={})
    client.review_comments.append({"path": "a.py", "line": 5, "body": "unrelated"})
    finding = Finding(
        file="a.py", line=9, category="runtime", severity=Severity.WARNING,
        message="msg", fix="fixed code", confidence=ConfidenceTier.MEDIUM, source="x",
    )

    count = review_pr.post_fix_suggestions(client, "acme/widgets", 4, "abc123", [finding])

    assert count == 1
    assert len(client.review_comments) == 2


def test_post_no_fix_comments_returns_the_count_posted():
    from src.core.models import ConfidenceTier, Finding, Severity

    client = _FakeGitHubClient(files=[], contents={})
    findings = [
        Finding(
            file="a.py", line=5, category="runtime", severity=Severity.WARNING,
            message="has a fix", fix="fixed code", confidence=ConfidenceTier.MEDIUM,
            source="x",
        ),
        Finding(
            file="a.py", line=9, category="security", severity=Severity.CRITICAL,
            message="SQL injection risk", fix="", confidence=ConfidenceTier.MEDIUM,
            source="sql_injection_checker",
        ),
    ]

    count = review_pr.post_no_fix_comments(client, "acme/widgets", 4, "abc123", findings)

    assert count == 1  # only the no-fix finding -- the other one is post_fix_suggestions()'s job
    assert len(client.review_comments) == 1
    assert client.review_comments[0]["line"] == 9
    assert "```suggestion" not in client.review_comments[0]["body"]
    assert "SQL injection risk" in client.review_comments[0]["body"]


def test_post_no_fix_comments_skips_a_line_that_already_has_a_comment():
    from src.core.models import ConfidenceTier, Finding, Severity

    client = _FakeGitHubClient(files=[], contents={})
    client.review_comments.append(
        {"path": "a.py", "line": 9, "body": "already posted from a prior run"}
    )
    finding = Finding(
        file="a.py", line=9, category="security", severity=Severity.CRITICAL,
        message="SQL injection risk", fix="", confidence=ConfidenceTier.MEDIUM,
        source="sql_injection_checker",
    )

    count = review_pr.post_no_fix_comments(client, "acme/widgets", 4, "abc123", [finding])

    assert count == 0
    assert len(client.review_comments) == 1  # still just the pre-existing one


def test_post_no_fix_comments_includes_the_reason():
    from src.core.models import ConfidenceTier, Finding, Severity

    client = _FakeGitHubClient(files=[], contents={})
    finding = Finding(
        file="a.py", line=9, category="runtime", severity=Severity.CRITICAL,
        message="undefined name 'amount'", fix="", confidence=ConfidenceTier.MEDIUM,
        source="undefined_name_checker",
    )

    review_pr.post_no_fix_comments(client, "acme/widgets", 4, "abc123", [finding])

    body = client.review_comments[0]["body"]
    assert "undefined name 'amount'" in body
    assert "manual investigation" in body


def test_post_fix_suggestions_sends_start_line_for_a_multi_line_fix():
    from src.core.models import ConfidenceTier, Finding, Severity

    client = _FakeGitHubClient(files=[], contents={})
    finding = Finding(
        file="a.py", line=3, end_line=4, category="syntax", severity=Severity.CRITICAL,
        message="msg", fix="    def process(self):\n        return 1",
        confidence=ConfidenceTier.MEDIUM, source="orchestrator",
    )

    review_pr.post_fix_suggestions(client, "acme/widgets", 4, "abc123", [finding])

    comment = client.review_comments[0]
    assert comment["line"] == 4
    assert comment["start_line"] == 3
