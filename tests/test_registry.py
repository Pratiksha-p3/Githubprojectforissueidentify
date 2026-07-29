from src.analyzers import registry
from src.core.models import Finding, Severity


def test_run_all_checkers_aggregates_across_multiple_bug_shapes():
    code = (
        "class Order:\n"
        "    def __init__(self, customer, total):\n"
        "        self.customer = customer\n"
        "\n"
        "    def summary(self):\n"
        "        return f'{self.customer}: {self.total}'\n"
        "\n"
        "def divide(a, b):\n"
        "    return a / b\n"
    )
    findings = registry.run_all_checkers(code, "app.py")
    sources = {f.source for f in findings}
    assert "unstored_constructor_param_checker" in sources
    assert "division_guard_checker" in sources


def test_registry_drops_ungrounded_findings(monkeypatch):
    def fake_checker(code: str, filename: str) -> list[Finding]:
        return [
            Finding(
                file=filename,
                line=1,
                category="runtime",
                severity=Severity.WARNING,
                message="fabricated finding",
                bad_code="this text does not appear in the source at all",
            )
        ]

    monkeypatch.setattr(registry, "CHECKERS", (fake_checker,))
    findings = registry.run_all_checkers("x = 1\n", "app.py")
    assert findings == []


def test_registry_returns_empty_list_for_clean_code():
    code = "def add(a, b):\n    return a + b\n"
    assert registry.run_all_checkers(code, "app.py") == []


def test_registry_drops_findings_with_invalid_fix(monkeypatch):
    def fake_checker(code: str, filename: str) -> list[Finding]:
        return [
            Finding(
                file=filename,
                line=1,
                category="runtime",
                severity=Severity.WARNING,
                message="a real finding",
                bad_code="x = 1",
                fix="this is not : valid python (((",
            )
        ]

    monkeypatch.setattr(registry, "CHECKERS", (fake_checker,))
    assert registry.run_all_checkers("x = 1\n", "app.py") == []


def test_registry_keeps_findings_with_no_fix_and_no_bad_code(monkeypatch):
    def fake_checker(code: str, filename: str) -> list[Finding]:
        return [
            Finding(
                file=filename,
                line=1,
                category="runtime",
                severity=Severity.INFO,
                message="a finding with nothing to verify",
            )
        ]

    monkeypatch.setattr(registry, "CHECKERS", (fake_checker,))
    findings = registry.run_all_checkers("x = 1\n", "app.py")
    assert len(findings) == 1


def test_include_llm_false_by_default_never_imports_llm_supplement(monkeypatch):
    """Deterministic checkers must work with zero LLM dependency — this
    guards against a future change accidentally making the LLM pass a
    hard requirement for plain `run_all_checkers(code, filename)` calls."""
    import sys

    monkeypatch.delitem(sys.modules, "src.agents.llm_supplement", raising=False)
    registry.run_all_checkers("def add(a, b):\n    return a + b\n", "app.py")
    assert "src.agents.llm_supplement" not in sys.modules


def test_include_llm_true_merges_llm_findings(monkeypatch):
    from src.agents import llm_supplement as llm_supplement_module

    def fake_get_llm_findings(code: str, filename: str) -> list[Finding]:
        return [
            Finding(
                file=filename,
                line=1,
                category="logic",
                severity=Severity.INFO,
                message="llm-sourced finding",
                bad_code="x = 1",
                source="llm_supplement",
            )
        ]

    monkeypatch.setattr(
        llm_supplement_module, "get_llm_findings", fake_get_llm_findings
    )
    findings = registry.run_all_checkers("x = 1\n", "app.py", include_llm=True)
    assert any(f.source == "llm_supplement" for f in findings)
