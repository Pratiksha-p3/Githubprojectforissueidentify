# AI Code Review & DevSecOps Automation

An AI-assisted PR review pipeline: deterministic AST checkers plus an LLM
supplement (grounded against real file content before anything is
surfaced), RAG-backed repo context, CVE/SBOM enrichment, and async
GitHub/GitLab webhook delivery with a durable queue and dead-letter
handling.

**Current stage: Stage 8 — dashboard, per-repo risk scoring, PDF/JSON
export.** See the assistant's staged roadmap for what's next.

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
```

## CLI

```
review-cli analyze <file>            # deterministic checkers only, no LLM/network
review-cli analyze <file> --llm      # + LLM supplement pass
review-cli review <file>             # full orchestrator + PR-gate decision
review-cli review <file> --no-llm    # deterministic-only, no API key needed
review-cli index <directory>         # index a local directory into the RAG vector store
```

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
