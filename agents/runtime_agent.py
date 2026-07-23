# agents/runtime_agent.py
#
# Thin wrapper — all detection logic lives in analyzers/runtime_checker.py.
# The old version of this file defined a PATTERNS list with 4 checks but
# never actually used it, only checking "/ 0" as a hardcoded substring.
# That gap is fixed by delegating to the analyzer, which implements all
# 4 patterns correctly.

from analyzers.runtime_checker import detect_runtime_errors


class RuntimeAgent:

    def scan(self, file):
        return detect_runtime_errors(file.full_content, file.filename)