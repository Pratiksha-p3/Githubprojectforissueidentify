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


def test_expected_indented_block_gets_a_medium_confidence_block_reindent_fix():
    """"expected an indented block" points at the first line that should
    have been indented. The target indentation isn't a guess -- CPython's
    message names the enclosing header, so it's exactly one level deeper
    than that. How many FOLLOWING lines also belong in the block is what's
    genuinely uncertain, so the fix is derived by trying increasingly
    large reindents and keeping the smallest one the parser confirms
    fixes the file -- here that's both `def process` and its body
    `return 1`. Still stays MEDIUM, never HIGH, because "smallest parsing
    fix" is a reasonable tie-break, not a certainty about intent."""
    code = (
        "class Handler:\n"
        "\n"
        "def process(self):\n"  # missing indent
        "    return 1\n"
    )
    result = orchestrator.review_code(
        code, "app.py", repo="acme/widgets", commit_sha="abc123", include_llm=False,
    )

    assert result.status == ReviewStatus.FAILED
    assert len(result.findings) == 1
    finding = result.findings[0]
    assert finding.line == 3
    assert finding.end_line == 4
    assert finding.fix == "    def process(self):\n        return 1"
    assert finding.confidence == ConfidenceTier.MEDIUM
    assert "smallest change" in finding.message


def test_indent_fix_applied_produces_a_parseable_file_when_block_is_one_line():
    """When the block genuinely was meant to hold just the one line
    (the common case: a single-statement if/def/etc.), the single-line
    fix is sufficient on its own to produce a fully parseable file."""
    import ast

    code = "def check(x):\n    if x > 0:\n    print(x)\n"
    result = orchestrator.review_code(
        code, "app.py", repo="acme/widgets", commit_sha="abc123", include_llm=False,
    )
    finding = result.findings[0]

    lines = code.splitlines()
    lines[finding.line - 1] = finding.fix
    patched = "\n".join(lines)

    ast.parse(patched)  # must not raise


def test_indent_fix_applied_over_its_full_range_produces_a_parseable_file():
    """The real end-to-end guarantee the fix is built around: substituting
    it back over its full [line, end_line] range (not just `line`) must
    produce a file that actually parses -- this is what makes it
    trustworthy as "apply this and the issue is resolved", unlike a
    single-line guess that might leave the file still broken."""
    import ast

    code = (
        "class Handler:\n"
        "\n"
        "def process(self):\n"
        "    return 1\n"
    )
    result = orchestrator.review_code(
        code, "app.py", repo="acme/widgets", commit_sha="abc123", include_llm=False,
    )
    finding = result.findings[0]

    lines = code.splitlines()
    lines[finding.line - 1 : finding.end_line] = finding.fix.splitlines()
    patched = "\n".join(lines)

    ast.parse(patched)  # must not raise
    namespace: dict = {}
    exec(compile(patched, "app.py", "exec"), namespace)
    assert namespace["Handler"]().process() == 1


def test_indent_fix_declines_when_no_reindent_within_the_window_parses():
    """A second, independent syntax error inside the same block (an
    unterminated expression, not an indentation problem) means no amount
    of reindenting alone will ever make the file parse -- must decline
    to offer a fix rather than return one that's provably wrong."""
    code = "class Handler:\ndef process(self):\n    return 1 +\n"
    result = orchestrator.review_code(
        code, "app.py", repo="acme/widgets", commit_sha="abc123", include_llm=False,
    )
    finding = result.findings[0]
    assert finding.fix == ""
    assert finding.confidence == ConfidenceTier.MEDIUM


def test_indent_fix_does_not_chain_past_a_medium_confidence_guess():
    """Even though _missing_indent_fix() produced a fix, it's a MEDIUM
    guess, not a HIGH-confidence certainty like the colon case -- the
    iterative collector must still stop after reporting it rather than
    applying the guess and searching further into the file."""
    code = (
        "class Handler:\n"
        "\n"
        "def process(self):\n"
        "def other(self):\n"  # a second syntax issue, further down
        "    return 1\n"
    )
    result = orchestrator.review_code(
        code, "app.py", repo="acme/widgets", commit_sha="abc123", include_llm=False,
    )

    assert len(result.findings) == 1


def test_unexpected_indent_gets_a_medium_confidence_fix():
    """"unexpected indent" (a line over-indented with no preceding
    colon-header authorizing a new block) has no header line in the
    message to anchor to, unlike "expected an indented block" -- the fix
    instead tries indentation levels already used earlier in the file,
    closest first, and takes the one the parser confirms fixes it."""
    code = (
        "def total(numbers):\n"
        "\n"
        "    result = 0\n"
        "\n"
        "      for n in numbers:\n"
        "        result += n\n"
        "\n"
        "    return result\n"
    )
    result = orchestrator.review_code(
        code, "app.py", repo="acme/widgets", commit_sha="abc123", include_llm=False,
    )

    assert result.status == ReviewStatus.FAILED
    assert len(result.findings) == 1
    finding = result.findings[0]
    assert finding.line == 5
    assert finding.fix == "    for n in numbers:"
    assert finding.confidence == ConfidenceTier.MEDIUM


def test_unexpected_indent_fix_applied_produces_a_working_file():
    import ast

    code = (
        "def total(numbers):\n"
        "\n"
        "    result = 0\n"
        "\n"
        "      for n in numbers:\n"
        "        result += n\n"
        "\n"
        "    return result\n"
    )
    result = orchestrator.review_code(
        code, "app.py", repo="acme/widgets", commit_sha="abc123", include_llm=False,
    )
    finding = result.findings[0]

    lines = code.splitlines()
    end = finding.end_line or finding.line
    lines[finding.line - 1 : end] = finding.fix.splitlines()
    patched = "\n".join(lines)

    ast.parse(patched)  # must not raise
    namespace: dict = {}
    exec(compile(patched, "app.py", "exec"), namespace)
    assert namespace["total"]([1, 2, 3]) == 6


def test_unindent_mismatch_gets_a_medium_confidence_fix():
    """"unindent does not match any outer indentation level" -- a line
    dedents to a column that isn't any enclosing block's actual
    indentation. Same closest-existing-level search as unexpected
    indent, just approached from the other direction (too shallow
    relative to a level that exists, rather than too deep)."""
    code = (
        "def f(x):\n"
        "    if x > 0:\n"
        "        return 1\n"
        "      return 0\n"
    )
    result = orchestrator.review_code(
        code, "app.py", repo="acme/widgets", commit_sha="abc123", include_llm=False,
    )

    assert result.status == ReviewStatus.FAILED
    finding = result.findings[0]
    assert finding.confidence == ConfidenceTier.MEDIUM
    assert finding.fix != ""

    import ast

    lines = code.splitlines()
    end = finding.end_line or finding.line
    lines[finding.line - 1 : end] = finding.fix.splitlines()
    ast.parse("\n".join(lines))  # must not raise


def test_stops_at_a_syntax_error_shape_with_no_recognized_fix():
    """A syntax error that matches neither the missing-colon nor the
    missing-indent shape must not be guessed at -- only that one error
    is reported, with no fix offered."""
    code = "def f(a, b:\n    return a + b\n"
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
