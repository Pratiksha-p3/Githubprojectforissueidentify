# mcp_tools/fix_mcp.py
"""
Backs the "fix_issue" MCP tool (mcp_server.py) — given a code snippet and
a plain-English description of what's wrong with it, returns a corrected
version of the whole snippet.

This is a different shape from agents/autofix_engine.py's fix
generation: AutoFixEngine replaces exactly ONE line for a GitHub
suggestion box, given a specific file + line number from a real PR
review. Here there's no file, no PR, no line number — an MCP client
(Claude Desktop, Cursor, ...) is asking about an arbitrary snippet ad
hoc, so the model rewrites the whole snippet instead of one line.

Previously depended on prompts.fix_prompt and llm.client, neither of
which exist in this codebase — every call fell through to the
ImportError stub and failed. Uses agents/llm_client.py's chat_completion
now, which is the same pluggable Groq/OpenAI/Anthropic dispatch every
other LLM call in this project goes through.
"""
from __future__ import annotations

import ast
import json
import re

from agents.llm_client import chat_completion

_SYSTEM_PROMPT = (
    "You are a senior software engineer with 20 years of production "
    "experience. You fix real defects with concrete, complete, working "
    "code — never a prose description of what someone else should do. "
    "Return JSON only, no markdown fences."
)


class FixMCP:

    def generate_fix(self, code: str, issue: str, language: str = "python") -> dict:
        """
        Returns {"fixed_code": str, "explanation": str, "confidence": float}.
        fixed_code is "" (with the reason in explanation, confidence 0.0)
        if no fix could be generated, or the model's response wasn't
        valid code for the given language.
        """
        if not code.strip() or not issue.strip():
            return {"fixed_code": "", "explanation": "Missing code or issue description.", "confidence": 0.0}

        prompt = f"""Fix the issue below in this {language} code. Return the COMPLETE
corrected code, not just the changed lines, and never a prose
description in place of code.

Issue: {issue}

Code:
{code}

Also rate your own confidence that this fix is correct and safe to apply
without further review, from 0.0 (not confident) to 1.0 (certain).

Return exactly:
{{"fixed_code": "<complete corrected code>", "explanation": "<what changed and why, one or two sentences>", "confidence": <0.0-1.0>}}"""

        try:
            text = chat_completion(
                system=_SYSTEM_PROMPT,
                user=prompt,
                temperature=0,
                max_tokens=1024,
            ).strip()
            text = re.sub(r'```[a-zA-Z]*\n?', '', text).strip('`').strip()
            data = json.loads(text)
        except Exception as e:
            return {"fixed_code": "", "explanation": f"Fix generation failed: {e}", "confidence": 0.0}

        fixed_code = str(data.get("fixed_code", "")).strip()
        explanation = str(data.get("explanation", ""))
        try:
            confidence = max(0.0, min(1.0, float(data.get("confidence", 0.0))))
        except (TypeError, ValueError):
            confidence = 0.0

        if fixed_code and language.lower() == "python" and not self._is_valid_python(fixed_code):
            return {
                "fixed_code": "",
                "explanation": f"Generated fix failed syntax validation (model said: {explanation})",
                "confidence": 0.0,
            }

        return {"fixed_code": fixed_code, "explanation": explanation, "confidence": confidence}

    def _is_valid_python(self, code: str) -> bool:
        try:
            ast.parse(code)
            return True
        except SyntaxError:
            return False
