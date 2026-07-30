# AI Code Review & DevSecOps Automation

An AI-assisted PR review pipeline: deterministic AST checkers plus an LLM
supplement (grounded against real file content before anything is
surfaced), RAG-backed repo context, CVE/SBOM enrichment, and async
GitHub/GitLab webhook delivery with a durable queue and dead-letter
handling.

**Current stage: Stage 4 — async queue (webhook receiver, Celery/Redis
worker, idempotency, dead-letter queue).** GitHub App integration (real
diff/file fetching) is Stage 5 — see the assistant's staged roadmap for
what's next.

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
```

## CLI

```
review-cli analyze <file>            # deterministic checkers only, no LLM/network
review-cli analyze <file> --llm      # + LLM supplement pass
review-cli review <file>             # full orchestrator + PR-gate decision
review-cli review <file> --no-llm    # deterministic-only, no API key needed
```
