"""
mcp_server.py

Tier 3 — Feature 10: MCP Tool Server

Exposes your entire AI Code Review system as an MCP
(Model Context Protocol) server.

Any AI assistant can then call your tools directly:
  - Claude Desktop
  - Cursor IDE
  - Continue.dev
  - Zed Editor
  - Any MCP-compatible client

Tools exposed:
  1. review_pr          — Full AI review of a GitHub PR
  2. review_file        — Review a single file's code
  3. scan_secrets       — Scan for exposed secrets
  4. check_complexity   — Get complexity metrics for code
  5. generate_tests     — Generate pytest tests for a function
  6. check_licenses     — Check dependency licenses
  7. explain_finding    — Explain a security finding in plain English
  8. fix_issue          — Generate a fix for a specific issue
  9. check_architecture — Check code against ADR decisions
  10. ask_copilot       — Ask anything about your codebase

Install:
  pip install mcp

Run:
  python mcp_server.py

Configure in Claude Desktop (~/.claude/claude_desktop_config.json):
  {
    "mcpServers": {
      "ai-code-review": {
        "command": "python",
        "args": ["/path/to/your/project/mcp_server.py"],
        "env": {
          "GROQ_API_KEY": "your-key",
          "GITHUB_TOKEN": "your-token"
        }
      }
    }
  }

Configure in Cursor (.cursor/mcp.json):
  {
    "mcpServers": {
      "ai-code-review": {
        "command": "python",
        "args": ["/path/to/your/project/mcp_server.py"]
      }
    }
  }
"""
from __future__ import annotations

import json
import sys
import os
import asyncio
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp import types
    HAS_MCP = True
except ImportError:
    HAS_MCP = False
    print("ERROR: MCP not installed. Run: pip install mcp", file=sys.stderr)
    sys.exit(1)

from config import cfg

# ─────────────────────────────────────────────────────────────────────────────
# MCP SERVER
# ─────────────────────────────────────────────────────────────────────────────

server = Server("ai-code-review")


# ─────────────────────────────────────────────────────────────────────────────
# TOOL DEFINITIONS
# ─────────────────────────────────────────────────────────────────────────────

@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [

        types.Tool(
            name        = "review_pr",
            description = (
                "Run a full AI code review on a GitHub Pull Request. "
                "Finds security issues, bugs, code quality problems, "
                "and posts inline comments. Returns a structured report."
            ),
            inputSchema = {
                "type": "object",
                "properties": {
                    "repo": {
                        "type":        "string",
                        "description": "GitHub repository in format owner/reponame",
                    },
                    "pr_number": {
                        "type":        "integer",
                        "description": "Pull request number to review",
                    },
                    "post_comments": {
                        "type":        "boolean",
                        "description": "Whether to post review comments to GitHub (default: true)",
                        "default":     True,
                    },
                },
                "required": ["repo", "pr_number"],
            },
        ),

        types.Tool(
            name        = "review_code",
            description = (
                "Review a code snippet directly without needing a GitHub PR. "
                "Paste any code and get a structured security + quality review."
            ),
            inputSchema = {
                "type": "object",
                "properties": {
                    "code": {
                        "type":        "string",
                        "description": "The code to review",
                    },
                    "language": {
                        "type":        "string",
                        "description": "Programming language (python, javascript, etc.)",
                        "default":     "python",
                    },
                    "filename": {
                        "type":        "string",
                        "description": "Filename for context",
                        "default":     "code.py",
                    },
                },
                "required": ["code"],
            },
        ),

        types.Tool(
            name        = "scan_secrets",
            description = (
                "Scan a code snippet or file path for exposed secrets, "
                "API keys, passwords, tokens, and credentials."
            ),
            inputSchema = {
                "type": "object",
                "properties": {
                    "code": {
                        "type":        "string",
                        "description": "Code to scan for secrets",
                    },
                    "scan_history": {
                        "type":        "boolean",
                        "description": "Also scan git history (requires git repo)",
                        "default":     False,
                    },
                },
                "required": ["code"],
            },
        ),

        types.Tool(
            name        = "check_complexity",
            description = (
                "Analyze code complexity using cyclomatic complexity and "
                "maintainability index. Flags overly complex functions."
            ),
            inputSchema = {
                "type": "object",
                "properties": {
                    "code": {
                        "type":        "string",
                        "description": "Python code to analyze",
                    },
                    "filename": {
                        "type":        "string",
                        "description": "Filename",
                        "default":     "code.py",
                    },
                },
                "required": ["code"],
            },
        ),

        types.Tool(
            name        = "generate_tests",
            description = (
                "Generate comprehensive pytest tests for a Python function. "
                "Includes happy path, edge cases, error cases, and security tests."
            ),
            inputSchema = {
                "type": "object",
                "properties": {
                    "code": {
                        "type":        "string",
                        "description": "The function code to generate tests for",
                    },
                    "function_name": {
                        "type":        "string",
                        "description": "Name of the function to test",
                    },
                    "language": {
                        "type":        "string",
                        "description": "Language (python, javascript)",
                        "default":     "python",
                    },
                },
                "required": ["code", "function_name"],
            },
        ),

        types.Tool(
            name        = "check_licenses",
            description = (
                "Check a requirements.txt or package.json for license compliance. "
                "Flags GPL, AGPL, and other copyleft licenses."
            ),
            inputSchema = {
                "type": "object",
                "properties": {
                    "requirements": {
                        "type":        "string",
                        "description": "Contents of requirements.txt or package.json",
                    },
                    "ecosystem": {
                        "type":        "string",
                        "description": "pip or npm",
                        "default":     "pip",
                    },
                },
                "required": ["requirements"],
            },
        ),

        types.Tool(
            name        = "explain_finding",
            description = (
                "Explain a security finding in plain English. "
                "Given a finding message, explains what it means, why it's dangerous, "
                "and how to fix it with a code example."
            ),
            inputSchema = {
                "type": "object",
                "properties": {
                    "finding": {
                        "type":        "string",
                        "description": "The security finding message to explain",
                    },
                    "code": {
                        "type":        "string",
                        "description": "The code that triggered the finding (optional)",
                        "default":     "",
                    },
                },
                "required": ["finding"],
            },
        ),

        types.Tool(
            name        = "fix_issue",
            description = (
                "Generate a concrete code fix for a specific issue. "
                "Returns the corrected code with explanation."
            ),
            inputSchema = {
                "type": "object",
                "properties": {
                    "code": {
                        "type":        "string",
                        "description": "The problematic code to fix",
                    },
                    "issue": {
                        "type":        "string",
                        "description": "Description of the issue to fix",
                    },
                    "language": {
                        "type":        "string",
                        "description": "Programming language",
                        "default":     "python",
                    },
                },
                "required": ["code", "issue"],
            },
        ),

        types.Tool(
            name        = "ask_copilot",
            description = (
                "Ask anything about your codebase. Searches the RAG index "
                "and review history to answer questions about your code, "
                "architecture, security patterns, and past findings."
            ),
            inputSchema = {
                "type": "object",
                "properties": {
                    "question": {
                        "type":        "string",
                        "description": "Your question about the codebase",
                    },
                },
                "required": ["question"],
            },
        ),

        types.Tool(
            name        = "get_pr_report",
            description = (
                "Get the latest review report for a specific PR. "
                "Returns findings, scores, and agent summaries."
            ),
            inputSchema = {
                "type": "object",
                "properties": {
                    "repo": {
                        "type":        "string",
                        "description": "GitHub repository (owner/repo)",
                    },
                    "pr_number": {
                        "type":        "integer",
                        "description": "PR number",
                    },
                },
                "required": ["repo", "pr_number"],
            },
        ),

    ]


# ─────────────────────────────────────────────────────────────────────────────
# TOOL IMPLEMENTATIONS
# ─────────────────────────────────────────────────────────────────────────────

@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:

    try:
        result = await _dispatch(name, arguments)
        return [types.TextContent(type="text", text=result)]
    except Exception as e:
        return [types.TextContent(
            type = "text",
            text = json.dumps({"error": str(e), "tool": name}, indent=2)
        )]


async def _dispatch(name: str, args: dict) -> str:
    """Route tool calls to implementations."""

    if name == "review_pr":
        return await _tool_review_pr(
            repo          = args["repo"],
            pr_number     = int(args["pr_number"]),
            post_comments = args.get("post_comments", True),
        )

    elif name == "review_code":
        return await _tool_review_code(
            code     = args["code"],
            language = args.get("language", "python"),
            filename = args.get("filename", "code.py"),
        )

    elif name == "scan_secrets":
        return await _tool_scan_secrets(
            code          = args["code"],
            scan_history  = args.get("scan_history", False),
        )

    elif name == "check_complexity":
        return await _tool_check_complexity(
            code     = args["code"],
            filename = args.get("filename", "code.py"),
        )

    elif name == "generate_tests":
        return await _tool_generate_tests(
            code          = args["code"],
            function_name = args["function_name"],
            language      = args.get("language", "python"),
        )

    elif name == "check_licenses":
        return await _tool_check_licenses(
            requirements = args["requirements"],
            ecosystem    = args.get("ecosystem", "pip"),
        )

    elif name == "explain_finding":
        return await _tool_explain_finding(
            finding = args["finding"],
            code    = args.get("code", ""),
        )

    elif name == "fix_issue":
        return await _tool_fix_issue(
            code     = args["code"],
            issue    = args["issue"],
            language = args.get("language", "python"),
        )

    elif name == "ask_copilot":
        return await _tool_ask_copilot(question=args["question"])

    elif name == "get_pr_report":
        return await _tool_get_pr_report(
            repo      = args["repo"],
            pr_number = int(args["pr_number"]),
        )

    else:
        return json.dumps({"error": f"Unknown tool: {name}"})


# ─────────────────────────────────────────────────────────────────────────────
# TOOL FUNCTIONS
# ─────────────────────────────────────────────────────────────────────────────

async def _tool_review_pr(repo: str, pr_number: int, post_comments: bool) -> str:
    """Full PR review via the existing pipeline."""
    from ingestion.github_loader import GitHubLoader
    from ingestion.parser import Parser
    from ingestion.chunker import Chunker
    from embeddings.embed import Embedder
    from vectordb.chroma_store import ChromaStore
    from agents.reviewer_agent import ReviewerAgent

    loader   = GitHubLoader()
    pr_ctx   = loader.load_pr(repo, pr_number)
    parser   = Parser()
    chunker  = Chunker()
    embedder = Embedder()
    store    = ChromaStore()

    sections = parser.parse_many(pr_ctx.files)
    chunks   = chunker.chunk_sections(sections)
    embedded = embedder.embed_chunks(chunks)
    store.upsert(embedded)

    reviewer = ReviewerAgent()
    report   = reviewer.review_pr(
        files          = pr_ctx.files,
        pr_title       = pr_ctx.title,
        pr_description = pr_ctx.description,
    )

    if post_comments:
        try:
            exec_summary = (
                report.get("executive_summary", {}).get("executive_summary", "")
                or f"Reviewed {len(pr_ctx.files)} files."
            )
            loader.post_review_comments(
                repo      = repo,
                pr_number = pr_number,
                head_sha  = pr_ctx.head_sha,
                findings  = report["findings"],
                summary   = exec_summary,
                approved  = report["approved"],
            )
        except Exception as e:
            report["post_error"] = str(e)

    return json.dumps({
        "overall_score":  report["overall_score"],
        "approved":       report["approved"],
        "total_findings": report["total_findings"],
        "critical_count": report["critical_count"],
        "findings":       report["findings"][:10],
        "summary":        report.get("executive_summary", {}).get("executive_summary", ""),
    }, indent=2)


async def _tool_review_code(code: str, language: str, filename: str) -> str:
    """Review a code snippet directly."""
    from groq import Groq
    from prompts.prompts import SYSTEM_PROMPT,build_prompt, build_security_prompt, build_summary_prompt

    client = Groq(api_key=cfg.groq_api_key)
    prompt = (
        f"File: {filename}\nLanguage: {language}\n\n"
        f"=== CODE TO REVIEW ===\n{code}"
    )
    resp = client.chat.completions.create(
        model    = cfg.review_model,
        temperature = 0,
        max_tokens  = 2048,
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": prompt},
        ],
    )
    return resp.choices[0].message.content.strip()


async def _tool_scan_secrets(code: str, scan_history: bool) -> str:
    """Scan code for secrets using regex patterns."""
    import re
    PATTERNS = [
        (r'(?i)(password|passwd|pwd)\s*=\s*["\'][^"\']{4,}["\']', "Hardcoded Password", "critical"),
        (r'(?i)(api[_-]?key|apikey)\s*=\s*["\'][^"\']{10,}["\']', "API Key", "critical"),
        (r'AKIA[0-9A-Z]{16}',                                       "AWS Access Key", "critical"),
        (r'ghp_[a-zA-Z0-9]{36}',                                    "GitHub Token", "critical"),
        (r'sk-[a-zA-Z0-9]{48}',                                     "OpenAI Key", "critical"),
        (r'(?i)secret[_-]?key\s*=\s*["\'][^"\']{8,}["\']',         "Secret Key", "critical"),
        (r'-----BEGIN (?:RSA )?PRIVATE KEY-----',                    "Private Key", "critical"),
    ]

    findings = []
    for pattern, secret_type, severity in PATTERNS:
        for m in re.finditer(pattern, code):
            line = code[:m.start()].count("\n") + 1
            findings.append({
                "type":     secret_type,
                "line":     line,
                "severity": severity,
                "value":    m.group(0)[:8] + "****",
                "fix":      f"Move to environment variable: os.getenv('{secret_type.upper().replace(' ','_')}')",
            })

    return json.dumps({
        "secrets_found": len(findings),
        "findings":      findings,
        "safe":          len(findings) == 0,
    }, indent=2)


async def _tool_check_complexity(code: str, filename: str) -> str:
    """Check code complexity using AST analysis."""
    import ast, re

    try:
        tree  = ast.parse(code)
        funcs = []

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # Count branches for cyclomatic complexity
                cc = 1
                for child in ast.walk(node):
                    if isinstance(child, (ast.If, ast.For, ast.While,
                                          ast.ExceptHandler, ast.With,
                                          ast.Assert, ast.comprehension)):
                        cc += 1
                    elif isinstance(child, ast.BoolOp):
                        cc += len(child.values) - 1

                loc = node.end_lineno - node.lineno + 1 if hasattr(node, 'end_lineno') else 0
                grade = "A" if cc <= 5 else "B" if cc <= 10 else "C" if cc <= 15 else "D" if cc <= 20 else "F"

                funcs.append({
                    "name":     node.name,
                    "line":     node.lineno,
                    "cc":       cc,
                    "grade":    grade,
                    "loc":      loc,
                    "complex":  cc > 10,
                    "refactor": cc > 15,
                })

        overall = max((f["cc"] for f in funcs), default=0)
        return json.dumps({
            "file":          filename,
            "functions":     funcs,
            "max_cc":        overall,
            "needs_refactor": any(f["refactor"] for f in funcs),
        }, indent=2)

    except SyntaxError as e:
        return json.dumps({"error": f"Syntax error: {e}"})


async def _tool_generate_tests(code: str, function_name: str, language: str) -> str:
    """Generate tests for a function."""
    from groq import Groq

    client = Groq(api_key=cfg.groq_api_key)
    prompt = f"""Generate comprehensive pytest tests for this {language} function.

Function name: {function_name}

Code:
```{language}
{code}
```

Generate tests covering:
1. Happy path
2. Edge cases (empty, None, boundary values)
3. Error cases
4. Security cases (if applicable)

Return ONLY the test code, no explanation."""

    resp = client.chat.completions.create(
        model       = cfg.review_model,
        temperature = 0.1,
        max_tokens  = 2048,
        messages    = [
            {"role": "system", "content": "You write pytest tests. Return ONLY test code."},
            {"role": "user",   "content": prompt},
        ],
    )
    return resp.choices[0].message.content.strip()


async def _tool_check_licenses(requirements: str, ecosystem: str) -> str:
    """Check licenses for dependencies."""
    import re, urllib.request

    lines    = [l.strip() for l in requirements.splitlines()
                if l.strip() and not l.startswith("#")]
    packages = []

    for line in lines[:20]:  # limit to 20 for speed
        m = re.match(r'^([A-Za-z0-9_\-\.]+)', line)
        if not m:
            continue
        pkg = m.group(1)
        try:
            url  = f"https://pypi.org/pypi/{pkg}/json"
            with urllib.request.urlopen(url, timeout=3) as r:
                data    = json.loads(r.read())
                license = data.get("info", {}).get("license", "UNKNOWN") or "UNKNOWN"
                banned  = any(
                    bad in license.upper()
                    for bad in ["GPL", "AGPL", "SSPL"]
                )
                packages.append({
                    "name":     pkg,
                    "license":  license[:50],
                    "blocked":  banned,
                    "reason":   "Copyleft license — requires your code to be open-source" if banned else "",
                })
        except Exception:
            packages.append({"name": pkg, "license": "UNKNOWN", "blocked": False})

    blocked = [p for p in packages if p["blocked"]]
    return json.dumps({
        "total":    len(packages),
        "blocked":  len(blocked),
        "compliant": len(packages) - len(blocked),
        "packages": packages,
        "safe":     len(blocked) == 0,
    }, indent=2)


async def _tool_explain_finding(finding: str, code: str) -> str:
    """Explain a security finding in plain English."""
    from groq import Groq

    client = Groq(api_key=cfg.groq_api_key)
    prompt = f"""Explain this security finding in plain English for a developer.

Finding: {finding}
{f'Code: {code}' if code else ''}

Explain:
1. What this vulnerability is
2. Why it is dangerous (real attack scenario)
3. How to fix it with a concrete code example
4. How to prevent it in future

Be clear and practical."""

    resp = client.chat.completions.create(
        model       = cfg.review_model,
        temperature = 0.2,
        max_tokens  = 1024,
        messages    = [
            {"role": "system", "content": "You are a security educator. Be clear and practical."},
            {"role": "user",   "content": prompt},
        ],
    )
    return resp.choices[0].message.content.strip()


async def _tool_fix_issue(code: str, issue: str, language: str) -> str:
    """Generate a fix for a specific issue."""
    from groq import Groq

    client = Groq(api_key=cfg.groq_api_key)
    prompt = f"""Fix this issue in the code. Return JSON only.

Language: {language}
Issue: {issue}

Code:
{code}

Return:
{{
  "fixed_code": "<complete corrected code>",
  "explanation": "<what you changed and why>",
  "confidence": <0.0-1.0>
}}"""

    resp = client.chat.completions.create(
        model       = cfg.review_model,
        temperature = 0,
        max_tokens  = 1024,
        messages    = [
            {"role": "system", "content": "Fix code issues. Return JSON only."},
            {"role": "user",   "content": prompt},
        ],
    )
    text = resp.choices[0].message.content.strip()
    import re as _re
    text = _re.sub(r'```[a-z]*\n?', '', text).strip('`').strip()
    return text


async def _tool_ask_copilot(question: str) -> str:
    """Ask the AI copilot about the codebase."""
    try:
        from agents.copilot_agent import GitHubCopilotAgent
        copilot = GitHubCopilotAgent()
        return copilot.ask(question)
    except Exception as e:
        return f"Copilot unavailable: {e}"


async def _tool_get_pr_report(repo: str, pr_number: int) -> str:
    """Get the latest saved review report for a PR."""
    pattern = f"review_{repo.replace('/','_')}_pr{pr_number}_*.json"
    files   = sorted(Path("reports").glob(pattern), reverse=True)

    if not files:
        return json.dumps({"error": f"No report found for {repo} PR #{pr_number}"})

    report = json.loads(files[0].read_text(encoding="utf-8"))
    return json.dumps({
        "overall_score":  report.get("overall_score"),
        "approved":       report.get("approved"),
        "total_findings": report.get("total_findings"),
        "critical_count": report.get("critical_count"),
        "findings":       report.get("findings", [])[:10],
        "reviewed_at":    report.get("reviewed_at"),
        "pipeline":       report.get("pipeline"),
    }, indent=2)


# ─────────────────────────────────────────────────────────────────────────────
# RESOURCES (read-only data the AI can access)
# ─────────────────────────────────────────────────────────────────────────────

@server.list_resources()
async def list_resources() -> list[types.Resource]:
    resources = []

    # Expose latest review reports as resources
    reports_dir = Path("reports")
    if reports_dir.exists():
        for f in sorted(reports_dir.glob("*.json"), reverse=True)[:10]:
            resources.append(types.Resource(
                uri         = f"file://reports/{f.name}",
                name        = f.name,
                description = f"PR review report: {f.stem}",
                mimeType    = "application/json",
            ))

    # Expose ADR docs
    adr_dir = Path("docs/adr")
    if adr_dir.exists():
        for f in adr_dir.glob("*.md"):
            resources.append(types.Resource(
                uri         = f"file://docs/adr/{f.name}",
                name        = f.name,
                description = f"Architecture Decision Record: {f.stem}",
                mimeType    = "text/markdown",
            ))

    return resources


@server.read_resource()
async def read_resource(uri: str) -> str:
    path = Path(uri.replace("file://", ""))
    if path.exists():
        return path.read_text(encoding="utf-8")
    return f"Resource not found: {uri}"


# ─────────────────────────────────────────────────────────────────────────────
# PROMPTS (reusable prompt templates)
# ─────────────────────────────────────────────────────────────────────────────

@server.list_prompts()
async def list_prompts() -> list[types.Prompt]:
    return [
        types.Prompt(
            name        = "review_my_code",
            description = "Review code I paste and give me a security + quality report",
            arguments   = [
                types.PromptArgument(
                    name        = "code",
                    description = "The code to review",
                    required    = True,
                )
            ],
        ),
        types.Prompt(
            name        = "explain_security_issue",
            description = "Explain a security issue and how to fix it",
            arguments   = [
                types.PromptArgument(
                    name        = "issue",
                    description = "The security issue to explain",
                    required    = True,
                )
            ],
        ),
        types.Prompt(
            name        = "generate_tests_for",
            description = "Generate pytest tests for a function",
            arguments   = [
                types.PromptArgument(
                    name        = "function",
                    description = "The function code",
                    required    = True,
                )
            ],
        ),
    ]


@server.get_prompt()
async def get_prompt(name: str, arguments: dict) -> types.GetPromptResult:
    if name == "review_my_code":
        return types.GetPromptResult(
            description = "Code review prompt",
            messages    = [
                types.PromptMessage(
                    role    = "user",
                    content = types.TextContent(
                        type = "text",
                        text = f"Please review this code for security issues, bugs, and quality:\n\n```\n{arguments.get('code','')}\n```",
                    ),
                )
            ],
        )
    elif name == "explain_security_issue":
        return types.GetPromptResult(
            description = "Security explanation prompt",
            messages    = [
                types.PromptMessage(
                    role    = "user",
                    content = types.TextContent(
                        type = "text",
                        text = f"Explain this security issue in plain English and show me how to fix it:\n\n{arguments.get('issue','')}",
                    ),
                )
            ],
        )
    elif name == "generate_tests_for":
        return types.GetPromptResult(
            description = "Test generation prompt",
            messages    = [
                types.PromptMessage(
                    role    = "user",
                    content = types.TextContent(
                        type = "text",
                        text = f"Generate comprehensive pytest tests for this function:\n\n```python\n{arguments.get('function','')}\n```",
                    ),
                )
            ],
        )
    raise ValueError(f"Unknown prompt: {name}")


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

async def main():
    print("🤖 AI Code Review MCP Server starting...", file=sys.stderr)
    print("Tools available:", file=sys.stderr)
    print("  • review_pr       — Review a GitHub PR", file=sys.stderr)
    print("  • review_code     — Review code snippet", file=sys.stderr)
    print("  • scan_secrets    — Scan for exposed secrets", file=sys.stderr)
    print("  • check_complexity — Analyze code complexity", file=sys.stderr)
    print("  • generate_tests  — Generate pytest tests", file=sys.stderr)
    print("  • check_licenses  — Check dependency licenses", file=sys.stderr)
    print("  • explain_finding — Explain a security finding", file=sys.stderr)
    print("  • fix_issue       — Generate code fix", file=sys.stderr)
    print("  • ask_copilot     — Ask about your codebase", file=sys.stderr)
    print("  • get_pr_report   — Get saved PR report", file=sys.stderr)
    print("Ready! Connect via Claude Desktop or Cursor.", file=sys.stderr)

    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


if __name__ == "__main__":
    asyncio.run(main())