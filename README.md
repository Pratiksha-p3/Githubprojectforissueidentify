# AI Code Review & DevSecOps Automation

An AI-assisted PR review pipeline: deterministic AST checkers plus an LLM
supplement (grounded against real file content before anything is
surfaced), RAG-backed repo context, CVE/SBOM enrichment, and async
GitHub/GitLab webhook delivery with a durable queue and dead-letter
handling.

**Current stage: Stage 12 — auto-fix accept/reject UX + decision logging.**
See the assistant's staged roadmap for what's next.

## Running tests locally

```
pip install -e ".[dev]"
pytest
ruff check .
mypy src
```

## Local infra

```
docker compose up          # redis + postgres + chroma
```

## Running the app processes

With `docker compose up` running in another terminal:

```
# API (webhook receiver)
uvicorn src.api.webhook:app --reload

# Worker (processes queued reviews)
celery -A src.worker.celery_app worker --loglevel=info

# Dashboard (risk score, history, JSON/PDF export) -- needs postgres running
uvicorn src.dashboard.app:app --reload --port 8001

# Fix accept/reject actions (learning-loop decision log) -- needs postgres running
uvicorn src.api.fix_actions:app --reload --port 8002
```

## CLI

```
review-cli analyze <file>            # deterministic checkers only, no LLM/network
review-cli analyze <file> --llm      # + LLM supplement pass
review-cli review <file>             # full orchestrator + PR-gate decision
review-cli review <file> --no-llm    # deterministic-only, no API key needed
review-cli index <directory>         # index a local directory into the RAG vector store
```

## Multi-agent review (opt-in, src/agents/coordinator.py)

`review_code(..., use_multi_agent=True)` swaps the single runtime/logic
LLM pass for four specialized agents (runtime/logic, security, style,
test_coverage) — `False` by default, since it multiplies LLM calls per
file 4x (a real cost/rate-limit concern, not a decision to make silently
on every review's behalf). `src/agents/guard_agent.py` is a separate,
also-opt-in secondary LLM pass that checks findings themselves for signs
of prompt-injection manipulation before they're posted anywhere — wire
it in wherever that extra scrutiny is worth the added LLM call (e.g.
before publishing to GitHub for a high-security repo).

## Semgrep (optional, src/tools/semgrep_runner.py)

`pip install semgrep` **into this project's own venv is not recommended** —
confirmed during development that it pulls in an incompatible
`opentelemetry` version and breaks chromadb's import entirely. Instead:

```
pipx install semgrep       # isolated, recommended
```

or install it into a separate venv/container and just make sure the
`semgrep` binary ends up on PATH — `semgrep_runner.py` only shells out to
it, it doesn't need to share Python dependencies with this project.

Also note: `semgrep --config auto` run without `semgrep login` first
returns the literal string `"requires login"` for some registry rules'
matched-code snippet instead of the real code — handled in
`semgrep_runner.py`, but worth knowing if you're inspecting raw semgrep
output yourself.

## Secrets backend (src/core/secrets.py)

Defaults to reading `.env`/the process environment (`SECRETS_BACKEND=env`).
Setting `SECRETS_BACKEND=azure_keyvault` + `AZURE_KEYVAULT_URL` switches to
a real Azure Key Vault lookup (via `azure-identity`/`azure-keyvault-secrets`,
an optional extra: `pip install -e ".[azure]"`) — coded against the same
interface, but not exercised against a live vault in development (no Azure
subscription available). `resolve_secrets()` is called once at
`review-cli`'s startup; a deployment running the FastAPI apps directly
(`uvicorn src.api.webhook:app`, ...) instead of through the CLI would need
the same call added at its own startup to actually pick up Key Vault
values.
