from src.analyzers.command_injection_checker import detect_command_injection


def test_flags_shell_true_with_a_dynamic_command():
    code = (
        "import subprocess\n\n"
        "def run(cmd):\n"
        "    subprocess.run(cmd, shell=True)\n"
    )
    findings = detect_command_injection(code, "app.py")
    assert len(findings) == 1
    assert findings[0].fix == ""  # detection only, by design
    assert "shell" in findings[0].message.lower()


def test_flags_shell_true_with_an_fstring_command():
    code = (
        "import subprocess\n\n"
        "def run(user_input):\n"
        '    subprocess.run(f"echo {user_input}", shell=True)\n'
    )
    findings = detect_command_injection(code, "app.py")
    assert len(findings) == 1


def test_skips_shell_true_with_a_hardcoded_literal_command():
    """No attacker-controlled input can reach a fully hardcoded string --
    this isn't the command-injection shape, just an (arguably) unrelated
    style choice, so it's deliberately not flagged."""
    code = (
        "import subprocess\n\n"
        "def run():\n"
        '    subprocess.run("ls -la", shell=True)\n'
    )
    assert detect_command_injection(code, "app.py") == []


def test_skips_when_shell_is_not_true():
    code = (
        "import subprocess\n\n"
        "def run(cmd):\n"
        "    subprocess.run(cmd)\n"
    )
    assert detect_command_injection(code, "app.py") == []


def test_ignores_file_without_subprocess_import():
    code = "def run(cmd):\n    subprocess.run(cmd, shell=True)\n"
    assert detect_command_injection(code, "app.py") == []


def test_flags_popen_too():
    code = (
        "import subprocess\n\n"
        "def run(cmd):\n"
        "    subprocess.Popen(cmd, shell=True)\n"
    )
    findings = detect_command_injection(code, "app.py")
    assert len(findings) == 1
    assert "Popen" in findings[0].message
