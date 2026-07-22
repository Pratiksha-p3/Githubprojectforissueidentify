# analyzers/syntax_checker.py
import ast


def detect_syntax_errors(code, filename, max_errors=25):
    """
    Repeatedly parses `code`, and on each SyntaxError:
      1. records the error
      2. neutralizes just that line (so it can't crash the parser again)
      3. re-parses to look for further, independent errors

    This trades exact fidelity (line contents get mangled) for coverage:
    without it, ast.parse() stops at the first syntax error and every
    later error in the file goes undetected.
    """
    findings = []
    lines = code.splitlines()
    seen_lines = set()

    for _ in range(max_errors):
        try:
            ast.parse("\n".join(lines))
            break  # clean parse — no more syntax errors
        except SyntaxError as e:
            lineno = e.lineno or 0

            # Guard against infinite loops if the same line keeps
            # erroring out after neutralization (shouldn't normally
            # happen, but be defensive).
            if lineno in seen_lines or lineno < 1 or lineno > len(lines):
                break
            seen_lines.add(lineno)

            findings.append({
                "file": filename,
                "line": lineno,
                "severity": "critical",
                "category": "syntax",
                "message": e.msg,
                "fix": "Fix Python syntax",
            })

            # Neutralize the offending line so re-parsing can surface
            # other, independent errors elsewhere in the file. Using
            # "pass" preserves line numbers for anything below it.
            indent = len(lines[lineno - 1]) - len(lines[lineno - 1].lstrip())
            lines[lineno - 1] = " " * indent + "pass  # [syntax error stubbed]"

    return findings