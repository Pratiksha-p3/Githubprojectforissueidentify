from agents.syntax_agent import SyntaxAgent
from agents.runtime_agent import RuntimeAgent
from agents.logic_agent import LogicAgent


class CodeIntelMCP:

    def __init__(self):
        self.syntax = SyntaxAgent()
        self.runtime = RuntimeAgent()
        self.logic = LogicAgent()

    def scan(self, pr_file):

        syntax = self.syntax.scan(pr_file)
        runtime = self.runtime.scan(pr_file)
        logic = self.logic.scan(pr_file)

        return {
            "syntax": syntax,
            "runtime": runtime,
            "logic": logic,
            "all": syntax + runtime + logic,
        }