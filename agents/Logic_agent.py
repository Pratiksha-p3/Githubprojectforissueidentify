# agents/logic_agent.py
#
# Thin wrapper — all detection logic lives in analyzers/logic_checker.py.
# NOTE: filename is now lowercase "logic_agent.py" (was "Logic_agent.py")
# to match the snake_case convention used by every other agent file
# (security_agent.py, syntax_agent.py, etc).

from analyzers.logic_checker import detect_logic_errors


class LogicAgent:

    def scan(self, file):
        return detect_logic_errors(file.full_content, file.filename)