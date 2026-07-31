from src.analyzers.sql_injection_checker import detect_sql_injection


def test_flags_fstring_passed_directly_to_execute():
    code = (
        "def get_user(user_id):\n"
        "    cursor.execute(f\"SELECT * FROM users WHERE id = {user_id}\")\n"
    )
    findings = detect_sql_injection(code, "app.py")
    assert len(findings) == 1
    assert findings[0].severity.value == "critical"
    assert findings[0].source == "sql_injection_checker"


def test_flags_fstring_assigned_to_a_variable_then_executed():
    """The more common real-world shape: the query string is built on its
    own line, then passed by name -- covers this via a same-scope scan
    for an unsafe assignment to that name, not just the direct argument."""
    code = (
        "def get_user(user_id):\n"
        "    query = f\"SELECT * FROM users WHERE id = {user_id}\"\n"
        "    cursor.execute(query)\n"
    )
    findings = detect_sql_injection(code, "app.py")
    assert len(findings) == 1
    assert findings[0].line == 3  # anchored at the execute() call, not the assignment


def test_flags_percent_formatted_query():
    code = 'cursor.execute("SELECT * FROM users WHERE id = %s" % user_id)\n'
    findings = detect_sql_injection(code, "app.py")
    assert len(findings) == 1


def test_flags_string_concatenation_query():
    code = 'cursor.execute("SELECT * FROM users WHERE id = " + user_id)\n'
    findings = detect_sql_injection(code, "app.py")
    assert len(findings) == 1


def test_does_not_flag_a_parameterized_query():
    code = 'cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))\n'
    assert detect_sql_injection(code, "app.py") == []


def test_does_not_flag_a_plain_literal_query():
    code = 'cursor.execute("SELECT * FROM users")\n'
    assert detect_sql_injection(code, "app.py") == []


def test_does_not_flag_unrelated_execute_calls_with_safe_args():
    code = 'worker.execute(task_id)\n'
    assert detect_sql_injection(code, "app.py") == []


def test_variable_from_a_different_function_does_not_leak_across_scopes():
    """The same-scope assignment scan must not treat an unsafely-built
    query in one function as evidence for a same-named but unrelated
    variable in a different function."""
    code = (
        "def build_unsafe():\n"
        "    query = f\"SELECT * FROM users WHERE id = {x}\"\n"
        "    return query\n"
        "\n"
        "def run_safe(query):\n"
        "    cursor.execute(query)\n"
    )
    findings = detect_sql_injection(code, "app.py")
    assert findings == []


def test_no_fix_is_generated():
    code = (
        "def get_user(user_id):\n"
        "    cursor.execute(f\"SELECT * FROM users WHERE id = {user_id}\")\n"
    )
    findings = detect_sql_injection(code, "app.py")
    assert findings[0].fix == ""


def test_syntax_error_returns_empty_list():
    assert detect_sql_injection("def broken(:\n", "app.py") == []
