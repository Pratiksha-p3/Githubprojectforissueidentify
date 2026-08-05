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


def test_reports_unbound_local_error_when_the_name_is_assigned_later_in_the_same_function():
    """A name read before its assignment, but assigned SOMEWHERE later in
    the same function, is a local variable for the function's entire
    body per Python's own scoping rules -- referencing it before that
    assignment runs raises UnboundLocalError, not NameError. Confirmed
    against real Python execution, not just this checker's own claim."""
    import ast

    code = 'def greet():\n    print(message)\n\n    message = "Hello"\n'
    findings = detect_undefined_names(code, "app.py")
    assert len(findings) == 1
    assert "UnboundLocalError" in findings[0].message
    assert "NameError" not in findings[0].message

    ast.parse(code)  # sanity: this is valid syntax, the bug is purely at runtime
    namespace: dict = {}
    exec(compile(code, "app.py", "exec"), namespace)
    try:
        namespace["greet"]()
        raise AssertionError("expected UnboundLocalError")
    except UnboundLocalError:
        pass


def test_reports_plain_name_error_when_never_assigned_anywhere_in_the_function():
    code = "def process_order(order):\n    total = 0\n    return amount\n"
    findings = detect_undefined_names(code, "app.py")
    assert "NameError" in findings[0].message
    assert "UnboundLocalError" not in findings[0].message


def test_a_nested_functions_own_assignment_does_not_make_the_outer_name_local():
    """A name assigned inside a NESTED function/lambda/class introduces
    its own separate scope -- it must not be treated as making the name
    local in the OUTER function too. Confirmed against real Python
    execution: this actually raises NameError, not UnboundLocalError."""
    code = (
        "def outer():\n"
        "    print(x)\n"
        "    def inner():\n"
        "        x = 1\n"
        "        return x\n"
        "    return inner()\n"
    )
    findings = detect_undefined_names(code, "app.py")
    assert len(findings) == 1
    assert "NameError" in findings[0].message
    assert "UnboundLocalError" not in findings[0].message

    namespace: dict = {}
    exec(compile(code, "app.py", "exec"), namespace)
    try:
        namespace["outer"]()
        raise AssertionError("expected NameError")
    except NameError:
        pass
