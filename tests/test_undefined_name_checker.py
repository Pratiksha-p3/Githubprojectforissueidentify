from src.analyzers.undefined_name_checker import detect_undefined_names


def test_flags_an_undefined_name():
    code = "def process_order(order):\n    total = 0\n    return amount\n"
    findings = detect_undefined_names(code, "app.py")
    assert len(findings) == 1
    assert "amount" in findings[0].message
    assert findings[0].fix == ""  # detection only, by design


def test_flags_a_typo_in_a_locally_assigned_name():
    code = "def f():\n    resutl = 1\n    return result\n"
    findings = detect_undefined_names(code, "app.py")
    assert len(findings) == 1
    assert "result" in findings[0].message


def test_ignores_a_correctly_defined_local():
    code = "def f():\n    total = 0\n    return total\n"
    assert detect_undefined_names(code, "app.py") == []


def test_ignores_imports_and_builtins():
    code = (
        "import os\n\n"
        "def f(x):\n"
        "    print(len(str(x)))\n"
        "    return os.getpid()\n"
    )
    assert detect_undefined_names(code, "app.py") == []


def test_ignores_self_attribute_access():
    code = (
        "class Account:\n"
        "    def __init__(self, owner):\n"
        "        self.owner = owner\n\n"
        "    def describe(self):\n"
        "        return self.owner\n"
    )
    assert detect_undefined_names(code, "app.py") == []


def test_ignores_closures_and_outer_scope_names():
    code = (
        "def outer():\n"
        "    total = 0\n\n"
        "    def inner():\n"
        "        return total\n\n"
        "    return inner()\n"
    )
    assert detect_undefined_names(code, "app.py") == []


def test_ignores_comprehension_variables():
    code = "def f(items):\n    return [i * 2 for i in items]\n"
    assert detect_undefined_names(code, "app.py") == []


def test_ignores_files_with_syntax_errors():
    assert detect_undefined_names("def broken(:\n    pass\n", "app.py") == []


def test_flags_multiple_undefined_names_separately():
    code = "def f():\n    a = first\n    b = second\n    return a + b\n"
    findings = detect_undefined_names(code, "app.py")
    assert len(findings) == 2
    messages = " ".join(f.message for f in findings)
    assert "first" in messages
    assert "second" in messages
