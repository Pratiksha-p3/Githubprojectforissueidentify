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


_COMPOUND_TYPES = (
    ast.If, ast.For, ast.AsyncFor, ast.While, ast.Try, ast.With, ast.AsyncWith,
    ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef,
)
if hasattr(ast, "TryStar"):  # Python 3.11+ except*
    _COMPOUND_TYPES = _COMPOUND_TYPES + (ast.TryStar,)

_BODY_FIELDS = ("body", "orelse", "finalbody")


def _is_simple_stmt(node: ast.AST) -> bool:
    """True for leaf statements (Assign, Return, Expr, Pass, ...) that
    can't themselves contain a nested block — i.e. the actual innermost
    thing a file position can point into."""
    return not any(getattr(node, f, None) for f in _BODY_FIELDS) and not getattr(node, "handlers", None)


def _ast_expected_indent(lines: list[str], lineno: int) -> int | None:
    """
    Real structure-aware version of the indent calculation: parses
    everything before the broken line and uses the actual AST — parent/
    child block relationships, not text patterns — to find which block
    the line belongs to. This is immune to the false positives a text
    scan for a trailing ':' can hit (a lambda, a dict literal, a slice, a
    variable annotation all end a line in ':' without opening a block).

    Returns None when the prefix doesn't parse on its own — most notably
    when the previous line is itself an unterminated block opener (e.g.
    "if x:" with no body yet), which is invalid Python in isolation. The
    caller falls back to the bracket/colon text heuristic in that case.
    """
    prefix = "\n".join(lines[:lineno - 1])
    if not prefix.strip():
        return 0
    try:
        tree = ast.parse(prefix)
    except SyntaxError:
        return None

    unit = _detect_indent_unit(lines)
    target_line_no = lineno - 1  # last real line number covered by prefix

    parent_map: dict[int, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parent_map[id(child)] = node

    candidates = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.stmt) and _is_simple_stmt(n)
        and getattr(n, "end_lineno", n.lineno) <= target_line_no
    ]
    if not candidates:
        return 0
    last_stmt = max(candidates, key=lambda n: (getattr(n, "end_lineno", n.lineno), n.col_offset))

    current_stripped = lines[lineno - 1].strip()
    first_word_match = re.match(r"[A-Za-z_]+", current_stripped)
    first_word = first_word_match.group(0) if first_word_match else ""

    if first_word in _DEDENT_KEYWORDS:
        enclosing = parent_map.get(id(last_stmt))
        indent = getattr(enclosing, "col_offset", None)
        return indent if indent is not None else 0

    return last_stmt.col_offset


def _expected_indent(lines: list[str], lineno: int) -> int:
    """
    Best-effort correct indentation (in spaces) for lines[lineno - 1].
    Tries the AST-based structural analysis first (_ast_expected_indent);
    falls back to a bracket/colon text heuristic only when the prefix
    can't be parsed at all — one indent unit deeper than the previous
    logical line if that line opens a block (ends with ':'), the
    enclosing block's indent for a dedent keyword (else/elif/except/
    finally), otherwise the same indent as the previous logical line.
    "Logical line" skips blanks, comment-only lines, and continuation
    lines still inside an open bracket from an earlier line.
    """
    ast_result = _ast_expected_indent(lines, lineno)
    if ast_result is not None:
        return ast_result

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

    first_word_match = re.match(r"\s*([A-Za-z_]+)", bad_line)
    first_word = first_word_match.group(1) if first_word_match else ""

    if ("expected an indented block" in lower_msg
            or "unindent" in lower_msg
            or "unexpected indent" in lower_msg
            # A dedent keyword sitting at the *same* depth as the block
            # body it should be closing (rather than genuinely dedented
            # to some other level) doesn't trigger an INDENT/DEDENT
            # token at all — ast.parse reports it as plain "invalid
            # syntax", not an indentation-specific message, even though
            # it's exactly the same class of bug.
            or (first_word in _DEDENT_KEYWORDS and "invalid syntax" in lower_msg)):
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
