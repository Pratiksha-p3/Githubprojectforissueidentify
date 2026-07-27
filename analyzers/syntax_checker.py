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
                "fix": _suggest_fix(lines, lineno, str(e.msg)),
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

_DEDENT_KEYWORDS = ("elif", "else", "except", "finally")
_DEFAULT_INDENT_UNIT = 4


# ── Indentation-depth analysis ────────────────────────────────────────
#
# The old behaviour for "unexpected indent" / "unindent does not match"
# was to lstrip() the line back to column 0 — syntactically "valid" but
# nearly always the wrong depth (it just moves the line out of every
# enclosing block instead of the one it actually belongs in). These
# helpers instead look at the surrounding block structure — bracket
# depth (so multi-line calls/collections aren't mistaken for separate
# statements) and whether the previous logical line opens a block (ends
# in ':') — to work out the indentation the line should actually have.

def _strip_comment(line: str) -> str:
    """Remove a trailing '#...' comment, respecting (best-effort) string
    literals so a '#' inside a string isn't mistaken for one."""
    in_str = None
    i = 0
    n = len(line)
    while i < n:
        ch = line[i]
        if in_str:
            if ch == "\\":
                i += 2
                continue
            if line[i:i + len(in_str)] == in_str:
                i += len(in_str)
                in_str = None
                continue
        else:
            if line[i:i + 3] in ('"""', "'''"):
                in_str = line[i:i + 3]
                i += 3
                continue
            if ch in ("'", '"'):
                in_str = ch
                i += 1
                continue
            if ch == "#":
                return line[:i]
        i += 1
    return line


def _bracket_delta(line: str) -> int:
    """Net change in open-bracket depth this line contributes, ignoring
    brackets inside strings/comments."""
    stripped = _strip_comment(line)
    delta = 0
    in_str = None
    i = 0
    n = len(stripped)
    while i < n:
        ch = stripped[i]
        if in_str:
            if ch == "\\":
                i += 2
                continue
            if stripped[i:i + len(in_str)] == in_str:
                i += len(in_str)
                in_str = None
                continue
        else:
            if stripped[i:i + 3] in ('"""', "'''"):
                in_str = stripped[i:i + 3]
                i += 3
                continue
            if ch in ("'", '"'):
                in_str = ch
                i += 1
                continue
            if ch in "([{":
                delta += 1
            elif ch in ")]}":
                delta -= 1
        i += 1
    return delta


def _line_start_depths(lines: list[str]) -> list[int]:
    """Bracket depth in effect at the *start* of each line — depth > 0
    means that line is a continuation of a still-open (, [ or { opened
    on an earlier line, not a fresh logical statement."""
    depths = []
    depth = 0
    for line in lines:
        depths.append(depth)
        depth += _bracket_delta(line)
    return depths


def _detect_indent_unit(lines: list[str]) -> int:
    """Infer the file's indent width from the first indented line found."""
    for line in lines:
        stripped = line.lstrip(" ")
        width = len(line) - len(stripped)
        if width > 0 and stripped:
            return width
    return _DEFAULT_INDENT_UNIT


def _logical_line_end_idx(depths: list[int], start_idx: int, limit_idx: int) -> int:
    """Last physical line (0-indexed, < limit_idx) belonging to the
    logical statement that starts at start_idx — i.e. its bracket
    continuation lines, stopping at the next line that starts fresh."""
    end_idx = start_idx
    for j in range(start_idx + 1, limit_idx):
        if depths[j] > 0:
            end_idx = j
        else:
            break
    return end_idx


def _expected_indent(lines: list[str], lineno: int) -> int:
    """
    Best-effort correct indentation (in spaces) for lines[lineno - 1]:
      - one indent unit deeper than the previous logical line, if that
        line opens a block (ends with ':')
      - the enclosing block's indent, if this line is a dedent keyword
        (else/elif/except/finally)
      - otherwise, the same indent as the previous logical line
    "Logical line" skips blanks, comment-only lines, and continuation
    lines still inside an open bracket from an earlier line.
    """
    unit = _detect_indent_unit(lines)
    depths = _line_start_depths(lines)
    target_idx = lineno - 1

    prev_idx = None
    for i in range(target_idx - 1, -1, -1):
        if depths[i] == 0 and _strip_comment(lines[i]).strip():
            prev_idx = i
            break

    if prev_idx is None:
        return 0

    prev_indent = len(lines[prev_idx]) - len(lines[prev_idx].lstrip(" "))
    current_stripped = lines[target_idx].strip()
    first_word_match = re.match(r"[A-Za-z_]+", current_stripped)
    first_word = first_word_match.group(0) if first_word_match else ""

    if first_word in _DEDENT_KEYWORDS:
        # Must align with the block opener it attaches to — walk
        # backward through logical lines for the nearest one shallower
        # than the body it's closing.
        for i in range(prev_idx, -1, -1):
            if depths[i] != 0:
                continue
            text = _strip_comment(lines[i]).strip()
            if not text:
                continue
            i_indent = len(lines[i]) - len(lines[i].lstrip(" "))
            if i_indent < prev_indent:
                return i_indent
        return 0

    end_idx = _logical_line_end_idx(depths, prev_idx, target_idx)
    prev_text = _strip_comment(lines[end_idx]).rstrip()

    if prev_text.endswith(":"):
        return prev_indent + unit

    return prev_indent


def _reindent(line: str, spaces: int) -> str:
    return " " * spaces + line.strip()


def _suggest_fix(lines: list[str], lineno: int, msg: str) -> str:
    """
    Best-effort exact-line fix for common SyntaxError shapes. Falls back to
    a plain-English instruction (still specific to the reported error) when
    the fix can't be generated with confidence — never a bare "fix syntax".
    """
    bad_line = lines[lineno - 1]
    stripped = bad_line.rstrip()
    lower_msg = msg.lower()

    if not stripped.rstrip().endswith(":") and _COMPOUND_STMT.match(stripped):
        return stripped + ":"

    if "was never closed" in lower_msg or "unexpected eof" in lower_msg:
        for open_ch, close_ch in (("(", ")"), ("[", "]"), ("{", "}")):
            if stripped.count(open_ch) > stripped.count(close_ch):
                return stripped + close_ch * (stripped.count(open_ch) - stripped.count(close_ch))
        return stripped + "  # add the missing closing bracket/quote"

    if ("expected an indented block" in lower_msg
            or "unindent" in lower_msg
            or "unexpected indent" in lower_msg):
        expected = _expected_indent(lines, lineno)
        return _reindent(bad_line, expected)

    if "invalid syntax" in lower_msg and re.search(r"=[^=]", stripped) and "==" not in stripped:
        m = re.match(r"^(\s*)(if|elif|while)\s+(.+[^=])=([^=].*):?\s*$", stripped)
        if m:
            indent, kw, cond, rest = m.groups()
            return f"{indent}{kw} {cond}=={rest}:"

    if "eol while scanning" in lower_msg or "unterminated string" in lower_msg:
        quote = '"' if stripped.count('"') % 2 else "'"
        return stripped + quote

    return f"{stripped}  # SyntaxError: {msg} — needs manual review"
