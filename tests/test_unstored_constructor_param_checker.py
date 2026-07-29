from src.analyzers.unstored_constructor_param_checker import (
    detect_unstored_constructor_params,
)


def test_flags_param_read_elsewhere_but_never_stored():
    code = (
        "class Student:\n"
        "    def __init__(self, name, age):\n"
        "        self.name = name\n"
        "\n"
        "    def display(self):\n"
        "        print(self.name)\n"
        "        print(self.age)\n"
    )
    findings = detect_unstored_constructor_params(code, "app.py")
    assert len(findings) == 1
    assert "age" in findings[0].message
    assert "self.age = age" in findings[0].fix


def test_skips_when_param_is_stored():
    code = (
        "class Student:\n"
        "    def __init__(self, name, age):\n"
        "        self.name = name\n"
        "        self.age = age\n"
        "\n"
        "    def display(self):\n"
        "        print(self.age)\n"
    )
    assert detect_unstored_constructor_params(code, "app.py") == []


def test_skips_when_param_is_never_read_elsewhere():
    """An unstored param that's also never read back isn't this bug —
    it doesn't matter to anything downstream."""
    code = (
        "class Student:\n"
        "    def __init__(self, name, age):\n"
        "        self.name = name\n"
        "\n"
        "    def display(self):\n"
        "        print(self.name)\n"
    )
    assert detect_unstored_constructor_params(code, "app.py") == []


def test_flags_multiple_missing_params_in_one_consolidated_finding():
    code = (
        "class Order:\n"
        "    def __init__(self, customer, total, currency):\n"
        "        self.customer = customer\n"
        "\n"
        "    def summary(self):\n"
        "        return f'{self.customer}: {self.total} {self.currency}'\n"
    )
    findings = detect_unstored_constructor_params(code, "app.py")
    assert len(findings) == 1
    assert "total" in findings[0].message and "currency" in findings[0].message
