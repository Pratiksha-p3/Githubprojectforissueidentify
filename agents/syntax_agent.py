# agents/syntax_agent.py
#
# Thin wrapper — all detection logic lives in analyzers/syntax_checker.py.
# This class exists only to expose a .scan(file) interface for the agent
# pipeline; it does not duplicate the checking logic itself.

from analyzers.syntax_checker import detect_syntax_errors


class SyntaxAgent:

    def scan(self, file):
        return detect_syntax_errors(file.full_content, file.filename)