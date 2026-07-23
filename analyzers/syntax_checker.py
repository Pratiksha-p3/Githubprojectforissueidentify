# analyzers/syntax_checker.py
import ast
import re


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

            bad_line = lines[lineno - 1]
            findings.append({
                "file": filename,
                "line": lineno,
                "severity": "critical",
                "category": "syntax",
                "message": e.msg,
                "bad_code": bad_line.strip(),
                "fix": _suggest_fix(bad_line, str(e.msg)),
            })

            # Neutralize the offending line so re-parsing can surface
            # other, independent errors elsewhere in the file. Using
            # "pass" preserves line numbers for anything below it.
            indent = len(lines[lineno - 1]) - len(lines[lineno - 1].lstrip())
            lines[lineno - 1] = " " * indent + "pass  # [syntax error stubbed]"

    return findings


_COMPOUND_STMT = re.compile(
    r"^\s*(def\s+\w+\(.*\)|class\s+\w+(\(.*\))?|if\s+.+|elif\s+.+|else|"
    r"for\s+.+|while\s+.+|try|except(\s+.+)?|finally|with\s+.+)\s*$"
)


def _suggest_fix(bad_line: str, msg: str) -> str:
    """
    Best-effort exact-line fix for common SyntaxError shapes. Falls back to
    a plain-English instruction (still specific to the reported error) when
    the fix can't be generated with confidence — never a bare "fix syntax".
    """
    stripped = bad_line.rstrip()
    lower_msg = msg.lower()

    if not stripped.rstrip().endswith(":") and _COMPOUND_STMT.match(stripped):
        return stripped + ":"

    if "was never closed" in lower_msg or "unexpected eof" in lower_msg:
        for open_ch, close_ch in (("(", ")"), ("[", "]"), ("{", "}")):
            if stripped.count(open_ch) > stripped.count(close_ch):
                return stripped + close_ch * (stripped.count(open_ch) - stripped.count(close_ch))
        return stripped + "  # add the missing closing bracket/quote"

    if "unindent" in lower_msg or "unexpected indent" in lower_msg:
        return stripped.lstrip() + "  # fix indentation to match the surrounding block"

    if "invalid syntax" in lower_msg and re.search(r"=[^=]", stripped) and "==" not in stripped:
        m = re.match(r"^(\s*)(if|elif|while)\s+(.+[^=])=([^=].*):?\s*$", stripped)
        if m:
            indent, kw, cond, rest = m.groups()
            return f"{indent}{kw} {cond}=={rest}:"

    if "eol while scanning" in lower_msg or "unterminated string" in lower_msg:
        quote = '"' if stripped.count('"') % 2 else "'"
        return stripped + quote

    return f"{stripped}  # SyntaxError: {msg} — needs manual review"