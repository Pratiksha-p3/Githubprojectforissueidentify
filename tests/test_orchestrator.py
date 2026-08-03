from src.core import orchestrator
from src.core.models import ConfidenceTier, ReviewStatus


def test_completed_status_when_llm_succeeds_with_no_findings(monkeypatch):
    monkeypatch.setattr(
        orchestrator, "get_llm_findings_with_status", lambda code, filename, **_kw: ([], True)
    )
    result = orchestrator.review_code(
        "def add(a, b):\n    return a + b\n",
        "app.py",
        repo="acme/widgets",
        commit_sha="abc123",
    )
    assert result.status == ReviewStatus.COMPLETED
    assert result.is_approvable is True


def test_degraded_status_when_llm_fails(monkeypatch):
    monkeypatch.setattr(
        orchestrator, "get_llm_findings_with_status", lambda code, filename, **_kw: ([], False)
    )
    result = orchestrator.review_code(
        "def add(a, b):\n    return a + b\n",
        "app.py",
        repo="acme/widgets",
        commit_sha="abc123",
    )
    assert result.status == ReviewStatus.DEGRADED
    assert result.is_approvable is False


def test_failed_status_on_syntax_error():
    result = orchestrator.review_code(
        "def broken(:\n    pass\n",
        "app.py",
        repo="acme/widgets",
        commit_sha="abc123",
        include_llm=False,
    )
    assert result.status == ReviewStatus.FAILED
    assert result.is_approvable is False
    assert result.critical_count == 1


def test_missing_colon_syntax_error_gets_a_high_confidence_fix():
    """CPython's own parser already says exactly where the colon
    belongs -- no judgment call about intent is involved, unlike every
    other fix in this project (see src/core/confidence.py), so this is
    the one case that legitimately crosses into HIGH confidence."""
    result = orchestrator.review_code(
        "def f(rating):\n    if rating >= 4\n        return 1\n    return 0\n",
        "app.py",
        repo="acme/widgets",
        commit_sha="abc123",
        include_llm=False,
    )
    assert result.status == ReviewStatus.FAILED
    finding = result.findings[0]
    assert finding.confidence == ConfidenceTier.HIGH
    assert finding.fix == "    if rating >= 4:"


def test_other_syntax_errors_get_no_fix_and_stay_medium_confidence():
    """Only the "expected ':'" shape is unambiguous enough for a fix --
    every other syntax error (mismatched parens, unexpected indent, ...)
    can have more than one valid resolution, so this must not guess."""
    result = orchestrator.review_code(
        "def broken(:\n    pass\n",
        "app.py",
        repo="acme/widgets",
        commit_sha="abc123",
        include_llm=False,
    )
    finding = result.findings[0]
    assert finding.fix == ""
    assert finding.confidence == ConfidenceTier.MEDIUM


def test_missing_colon_fix_is_skipped_when_line_has_a_trailing_comment():
    """Appending a colon after a `#` would land inside the comment and
    not actually fix the syntax error -- safer to offer no fix at all
    than one that looks plausible but doesn't work."""
    result = orchestrator.review_code(
        "def f(rating):\n    if rating >= 4  # check threshold\n        return 1\n    return 0\n",
        "app.py",
        repo="acme/widgets",
        commit_sha="abc123",
        include_llm=False,
    )
    finding = result.findings[0]
    assert finding.fix == ""
    assert finding.confidence == ConfidenceTier.MEDIUM


def test_multiple_missing_colons_are_all_found_in_one_pass():
    """Python's parser can only ever report the FIRST syntax error --
    _collect_syntax_error_findings() gets past each auto-fixable colon
    error by applying the fix to an in-memory working copy and
    re-parsing, so a file with several missing colons in a row gets all
    of them reported at once instead of needing one review per error."""
    code = (
        "def check_status(code)\n"
        "    if code == 200\n"
        '        return "ok"\n'
        "    elif code == 404\n"
        '        return "not found"\n'
        "    else:\n"
        '        return "unknown"\n'
    )
    result = orchestrator.review_code(
        code, "app.py", repo="acme/widgets", commit_sha="abc123", include_llm=False,
    )

    assert result.status == ReviewStatus.FAILED
    assert len(result.findings) == 3
    assert [f.line for f in result.findings] == [1, 2, 4]
    assert all(f.confidence == ConfidenceTier.HIGH for f in result.findings)


def test_applying_all_multi_colon_fixes_produces_a_fully_working_file():
    import ast

    code = (
        "def check_status(code)\n"
        "    if code == 200\n"
        '        return "ok"\n'
        "    elif code == 404\n"
        '        return "not found"\n'
        "    else:\n"
        '        return "unknown"\n'
    )
    result = orchestrator.review_code(
        code, "app.py", repo="acme/widgets", commit_sha="abc123", include_llm=False,
    )

    lines = code.splitlines()
    for finding in result.findings:
        lines[finding.line - 1] = finding.fix
    patched = "\n".join(lines)

    ast.parse(patched)  # must not raise
    namespace: dict = {}
    exec(compile(patched, "app.py", "exec"), namespace)
    assert namespace["check_status"](200) == "ok"
    assert namespace["check_status"](404) == "not found"
    assert namespace["check_status"](500) == "unknown"


def test_stops_at_the_first_non_colon_syntax_error_without_guessing_past_it():
    """A genuinely ambiguous syntax error (not the missing-colon shape)
    must not be guessed past -- there's no way to know how many lines
    were meant to belong to the block, so only that one error is
    reported, even if more errors exist further down the file."""
    code = (
        "class Handler:\n"
        "\n"
        "def process(self):\n"  # missing indent -- ambiguous, not a colon issue
        "    return 1\n"
    )
    result = orchestrator.review_code(
        code, "app.py", repo="acme/widgets", commit_sha="abc123", include_llm=False,
    )

    assert result.status == ReviewStatus.FAILED
    assert len(result.findings) == 1
    assert result.findings[0].fix == ""
    assert result.findings[0].confidence == ConfidenceTier.MEDIUM


def test_missing_colon_fix_applied_produces_a_fully_parseable_file():
    """The real end-to-end guarantee: substituting the fix back into the
    original file (not just validating the fix snippet in isolation)
    must produce a file that actually parses."""
    import ast

    code = "def f(rating):\n    if rating >= 4\n        return 1\n    return 0\n"
    result = orchestrator.review_code(
        code, "app.py", repo="acme/widgets", commit_sha="abc123", include_llm=False,
    )
    finding = result.findings[0]

    lines = code.splitlines()
    lines[finding.line - 1] = finding.fix
    patched = "\n".join(lines)

    ast.parse(patched)  # must not raise


def test_deterministic_findings_included_regardless_of_llm_status(monkeypatch):
    monkeypatch.setattr(
        orchestrator, "get_llm_findings_with_status", lambda code, filename, **_kw: ([], False)
    )
    code = "def divide(a, b):\n    return a / b\n"
    result = orchestrator.review_code(
        code, "app.py", repo="acme/widgets", commit_sha="abc123"
    )
    assert any(f.source == "division_guard_checker" for f in result.findings)
    assert result.status == ReviewStatus.DEGRADED


def test_include_llm_false_never_calls_llm(monkeypatch):
    calls = []
    monkeypatch.setattr(
        orchestrator,
        "get_llm_findings_with_status",
        lambda code, filename, **_kw: (calls.append(1), ([], True))[1],
    )
    result = orchestrator.review_code(
        "def add(a, b):\n    return a + b\n",
        "app.py",
        repo="acme/widgets",
        commit_sha="abc123",
        include_llm=False,
    )
    assert calls == []
    assert result.status == ReviewStatus.COMPLETED


def test_use_multi_agent_calls_coordinator_instead_of_single_agent(monkeypatch):
    single_agent_calls = []
    multi_agent_calls = []

    monkeypatch.setattr(
        orchestrator,
        "get_llm_findings_with_status",
        lambda code, filename, **_kw: (single_agent_calls.append(1), ([], True))[1],
    )
    monkeypatch.setattr(
        orchestrator,
        "run_all_agents",
        lambda code, filename, **_kw: (multi_agent_calls.append(1), ([], True))[1],
    )

    orchestrator.review_code(
        "def add(a, b):\n    return a + b\n",
        "app.py",
        repo="acme/widgets",
        commit_sha="abc123",
        use_multi_agent=True,
    )

    assert multi_agent_calls == [1]
    assert single_agent_calls == []


def test_use_multi_agent_false_still_uses_single_agent_by_default(monkeypatch):
    single_agent_calls = []
    multi_agent_calls = []

    monkeypatch.setattr(
        orchestrator,
        "get_llm_findings_with_status",
        lambda code, filename, **_kw: (single_agent_calls.append(1), ([], True))[1],
    )
    monkeypatch.setattr(
        orchestrator,
        "run_all_agents",
        lambda code, filename, **_kw: (multi_agent_calls.append(1), ([], True))[1],
    )

    orchestrator.review_code(
        "def add(a, b):\n    return a + b\n",
        "app.py",
        repo="acme/widgets",
        commit_sha="abc123",
    )

    assert single_agent_calls == [1]
    assert multi_agent_calls == []


def test_use_multi_agent_degraded_when_coordinator_reports_failure(monkeypatch):
    monkeypatch.setattr(orchestrator, "run_all_agents", lambda code, filename, **_kw: ([], False))

    result = orchestrator.review_code(
        "def add(a, b):\n    return a + b\n",
        "app.py",
        repo="acme/widgets",
        commit_sha="abc123",
        use_multi_agent=True,
    )

    assert result.status == ReviewStatus.DEGRADED


def test_repo_and_commit_sha_are_passed_as_the_canary_key(monkeypatch):
    captured = {}

    def fake_get_llm_findings_with_status(code, filename, **kwargs):
        captured.update(kwargs)
        return [], True

    monkeypatch.setattr(
        orchestrator, "get_llm_findings_with_status", fake_get_llm_findings_with_status
    )

    orchestrator.review_code(
        "def add(a, b):\n    return a + b\n",
        "app.py",
        repo="acme/widgets",
        commit_sha="abc123",
    )

    assert captured["canary_key"] == "acme/widgets:abc123"


def test_review_code_is_idempotent_for_identical_input(monkeypatch):
    monkeypatch.setattr(
        orchestrator, "get_llm_findings_with_status", lambda code, filename, **_kw: ([], True)
    )
    code = (
        "class Order:\n"
        "    def __init__(self, total):\n"
        "        pass\n"
        "\n"
        "    def show(self):\n"
        "        return self.total\n"
    )

    first = orchestrator.review_code(code, "app.py", repo="acme/widgets", commit_sha="abc123")
    second = orchestrator.review_code(code, "app.py", repo="acme/widgets", commit_sha="abc123")

    assert first.status == second.status
    assert first.findings == second.findings
    assert first.summary == second.summary
