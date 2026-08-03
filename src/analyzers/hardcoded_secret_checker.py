"""
src/analyzers/hardcoded_secret_checker.py

Detects a hardcoded secret: a variable whose name looks like it holds a
credential (API_KEY, PASSWORD, SECRET, TOKEN, ...) assigned directly to a
non-empty string literal. Scoped tightly to keep false positives low:
only fires when the right-hand side is a literal ast.Constant string,
never a call or another variable -- `token = os.environ["TOKEN"]` or
`token = generate_token()` never match, only a genuinely hardcoded value
does.

The fix suggests reading the value from an environment variable instead
(os.environ[...]) -- MEDIUM confidence, since which secrets backend a
project actually uses (env var, Vault, AWS Secrets Manager, ...) is a
judgment call this checker can't make; matches src/core/secrets.py's own
env-first default used elsewhere in this project.

The fix is only generated when the file already has `import os` --
confirmed live: a fix applied without checking this (via review-cli
review-pr's --auto-apply, which commits a finding's fix without a human
reading the finding's message first) produced a file that raised
NameError: name 'os' is not defined the moment it was imported, since
nothing added the missing import. Rather than splice an import
statement into an arbitrary line of the file (fragile, and a second
finding in the same file would splice a second, duplicate import), this
checker now simply declines to offer a fix at all when `os` isn't
already imported -- leaving it for manual review, the same "no fix is
better than a wrong fix" precedent src/analyzers/sql_injection_checker.py
already sets.
"""
from __future__ import annotations

import ast

from src.core.models import ConfidenceTier, Finding, Severity

_SECRET_NAME_MARKERS = (
    "password",
    "passwd",
    "pwd",
    "secret",
    "apikey",
    "accesskey",
    "privatekey",
    "authtoken",
    "token",
    "credential",
)


def _looks_like_secret_name(name: str) -> bool:
    normalized = name.lower().replace("_", "")
    return any(marker in normalized for marker in _SECRET_NAME_MARKERS)


def _imports_os(tree: ast.AST) -> bool:
    """True only if the bare name `os` is actually bound -- `import os`
    qualifies, but `import os as _os` does NOT: this checker's fix
    always writes `os.environ[...]`, and `ast.alias.name` is the
    original module name ("os") even when aliased, so checking only
    `a.name == "os"` would (and, before this check existed, briefly
    did) generate a fix referencing a name the file never actually
    binds."""
    return any(
        isinstance(node, ast.Import)
        and any(a.name == "os" and a.asname is None for a in node.names)
        for node in ast.walk(tree)
    )


def detect_hardcoded_secrets(code: str, filename: str) -> list[Finding]:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []
    lines = code.splitlines()
    os_imported = _imports_os(tree)

    findings: list[Finding] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not (isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)):
            continue
        if not node.value.value:
            continue  # empty string -- nothing to leak
        if not (0 < node.lineno <= len(lines)):
            continue

        for target in node.targets:
            if not (isinstance(target, ast.Name) and _looks_like_secret_name(target.id)):
                continue

            original_line = lines[node.lineno - 1]
            if os_imported:
                indent = " " * (len(original_line) - len(original_line.lstrip()))
                fix_code = f'{indent}{target.id} = os.environ["{target.id}"]'
                message = (
                    f"'{target.id}' looks like a credential but is hardcoded as "
                    f"a string literal — read it from an environment variable instead."
                )
            else:
                fix_code = ""
                message = (
                    f"'{target.id}' looks like a credential but is hardcoded as a "
                    f"string literal — read it from an environment variable instead "
                    f"(no fix generated: the file doesn't `import os` yet)."
                )

            findings.append(
                Finding(
                    file=filename,
                    line=node.lineno,
                    category="security",
                    severity=Severity.CRITICAL,
                    message=message,
                    bad_code=original_line.strip(),
                    fix=fix_code,
                    confidence=ConfidenceTier.MEDIUM,
                    source="hardcoded_secret_checker",
                )
            )

    return findings
