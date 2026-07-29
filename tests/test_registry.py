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
