import json

try:
    from prompts.fix_prompt import FIX_PROMPT  # type: ignore[import-not-found]
except ImportError:
    FIX_PROMPT = """
    You are fixing a code issue.

    Issue Type: {issue_type}
    Message: {message}
    Filename: {filename}

    Code Context:
    {code}
    """

try:
    from llm.client import llm  # type: ignore[import-not-found]
except ImportError:
    class llm:
        @staticmethod
        def invoke(prompt):
            raise ImportError("Import 'llm.client' could not be resolved")


class FixMCP:

    def generate_fix(
        self,
        finding,
        code_context,
    ):

        prompt = FIX_PROMPT.format(
            issue_type=finding.get("type", ""),
            message=finding.get("message", ""),
            filename=finding.get("file", ""),
            code=code_context,
        )

        response = llm.invoke(prompt)

        try:
            return json.loads(response)

        except Exception:

            return {
                "root_cause": "",
                "fixed_code": "",
                "confidence": 0.0,
            }