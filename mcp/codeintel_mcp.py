from agents.syntax_agent import SyntaxAgent
from agents.Runtime_agent import RuntimeAgent
from agents.Logic_agent import LogicAgent


class CodeIntelMCP:

    def __init__(self):
        self.syntax = SyntaxAgent()
        self.runtime = RuntimeAgent()
        self.logic = LogicAgent()

    def scan(self, pr_file):

        findings = []

        findings.extend(
            self.syntax.scan(pr_file)
        )

        findings.extend(
            self.runtime.scan(pr_file)
        )

        findings.extend(
            self.logic.scan(pr_file)
        )

        return findings