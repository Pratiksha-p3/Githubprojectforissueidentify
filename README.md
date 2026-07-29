# AI Code Review & DevSecOps Automation

An AI-assisted PR review pipeline: deterministic AST checkers plus an LLM
supplement (grounded against real file content before anything is
surfaced), RAG-backed repo context, CVE/SBOM enrichment, and async
GitHub/GitLab webhook delivery with a durable queue and dead-letter
handling.

**Current stage: Stage 0 — repo scaffolding & domain model baseline.**
No checkers, LLM calls, or GitHub integration exist yet — see
`.claude/plans` (or ask the assistant) for the full staged roadmap.

## Running tests locally

```
pip install -e ".[dev]"
pytest
ruff check .
mypy src
```

## Local infra (from Stage 4 onward)

```
docker compose up
```
