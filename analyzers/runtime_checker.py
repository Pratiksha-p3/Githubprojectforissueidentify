# analyzers/runtime_checker.py

from analyzers.ai_review import get_ai_findings
from analyzers.index_bounds_checker import detect_index_bounds_issues
from analyzers.division_guard_checker import detect_unguarded_division
from analyzers.file_exists_checker import detect_unguarded_file_open

# The deterministic checks below used to be four bare regexes with
# hardcoded, non-contextual fix templates ("if b == 0: ...; return a / b",
# "items[index]", etc. — placeholder names that don't exist in the code
# being "fixed"). Three have been replaced with AST-based versions that
# generate a fix referencing the actual variable/expression involved and
# don't re-flag something already guarded:
#   - `\[[0-9]+\]`      -> detect_index_bounds_issues (indexing)
#   - `/(?!/)\s*\w+\b`  -> detect_unguarded_division (division by zero)
#   - `open\s*\(`       -> detect_unguarded_file_open (missing file)
# The fourth, `print\s*\(\s*\w+\s*\)` ("Possible undefined variable"), had
# no fixable version to move to — flagging *every* single-argument print
# call as a possibly-undefined variable is close to 100% false positives
# regardless of representation (a bare print of a real, defined variable
# is completely ordinary code) and was removed rather than "fixed", since
# there's no reliable signal in it worth keeping.


def detect_runtime_errors(code, filename):
    findings = []
    seen_lines = set()

    for detector in (detect_index_bounds_issues, detect_unguarded_division, detect_unguarded_file_open):
        for f in detector(code, filename):
            findings.append(f)
            seen_lines.add(f["line"])

    # ── LLM pass — senior-engineer review for anything the patterns
    #    above don't shape-match (not limited to a fixed checklist) ────
    for f in get_ai_findings(code, filename):
        if f["category"] != "runtime" or f["line"] in seen_lines:
            continue
        findings.append({
            "category": "runtime",
            "severity": f["severity"],
            "file": filename,
            "line": f["line"],
            "message": f["message"],
            "fix_type": "ai_suggested",
            "fix_code": f["fix"],
            "fix": f["fix"],
            "bad_code": f["bad_code"],
            "reason": f["reason"],
            "source": "llm",
        })
        seen_lines.add(f["line"])

    return findings
