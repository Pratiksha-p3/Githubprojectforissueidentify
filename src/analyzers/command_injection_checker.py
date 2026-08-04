"""
src/analyzers/command_injection_checker.py

Detects `subprocess.run/call/check_call/check_output/Popen(...)` called
with `shell=True` where the command argument is NOT a plain string
literal (an f-string, a `+`/`%` built string, a bare variable, ...) --
`shell=True` hands the command to the system shell verbatim, so any
attacker-influenced content in a dynamically-built command can inject
arbitrary shell syntax (`; rm -rf /`, `$(...)`, `` ` ``, pipes, ...).

A hardcoded literal command with shell=True (`subprocess.run("ls -la",
shell=True)`) isn't this bug -- there's no attacker-controlled input to
inject through, so it's deliberately not flagged, matching the "keep
false positives low" scoping every other checker in this package uses.

No fix is generated: removing shell=True and passing the command as an
argument list is the standard remediation, but correctly splitting an
arbitrary dynamically-built command string into that list (handling
quoting, variable interpolation, etc.) isn't something derivable from
the AST alone -- same "detection, not auto-fix" stance
src/analyzers/sql_injection_checker.py already takes for a bug class
whose safe rewrite depends on context this project doesn't have.
"""
from __future__ import annotations

import ast

from src.core.models import ConfidenceTier, Finding, Severity

_SUBPROCESS_FUNCS = {"run", "call", "check_call", "check_output", "Popen"}


def _imports_subprocess(tree: ast.AST) -> bool:
    return any(
        isinstance(node, ast.Import)
        and any(a.name == "subprocess" and a.asname is None for a in node.names)
        for node in ast.walk(tree)
    )


def _has_shell_true(call: ast.Call) -> bool:
    return any(
        kw.arg == "shell" and isinstance(kw.value, ast.Constant) and kw.value.value is True
        for kw in call.keywords
    )


def _is_dynamic_command(arg: ast.expr) -> bool:
    """True unless the command is a plain, fully-literal string (or a
    list of them) -- anything else (a Name, an f-string, a %/+ built
    string, a list containing a non-literal element) could carry
    attacker-influenced content."""
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        return False
    if isinstance(arg, (ast.List, ast.Tuple)):
        return not all(
            isinstance(elt, ast.Constant) and isinstance(elt.value, str) for elt in arg.elts
        )
    return True


def detect_command_injection(code: str, filename: str) -> list[Finding]:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []
    if not _imports_subprocess(tree):
        return []
    lines = code.splitlines()

    findings: list[Finding] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (
            isinstance(func, ast.Attribute)
            and func.attr in _SUBPROCESS_FUNCS
            and isinstance(func.value, ast.Name)
            and func.value.id == "subprocess"
        ):
            continue
        if not _has_shell_true(node) or not node.args:
            continue
        if not _is_dynamic_command(node.args[0]):
            continue
        if not (0 < node.lineno <= len(lines)):
            continue

        original_line = lines[node.lineno - 1]
        findings.append(
            Finding(
                file=filename,
                line=node.lineno,
                category="security",
                severity=Severity.CRITICAL,
                message=(
                    f"subprocess.{func.attr}(...) runs a dynamically-built "
                    f"command with shell=True — vulnerable to shell command "
                    f"injection if any part of the command is influenced by "
                    f"external input. Drop shell=True and pass the command as "
                    f"a list instead (e.g. subprocess.run([cmd, arg1, arg2]))."
                ),
                bad_code=original_line.strip(),
                fix="",
                confidence=ConfidenceTier.MEDIUM,
                source="command_injection_checker",
            )
        )

    return findings
