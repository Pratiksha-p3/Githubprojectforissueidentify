# AI Code Review & DevSecOps Automation

An AI-assisted PR review pipeline: deterministic AST checkers plus an LLM
supplement (grounded against real file content before anything is
surfaced), RAG-backed repo context, CVE/SBOM enrichment, and async
GitHub/GitLab webhook delivery with a durable queue and dead-letter
handling.

**Current stage: Stage 13 — deep testing (golden-vuln regression suite, RAG
eval CI gate, concurrency correctness).**
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

## Deep testing (Stage 13)

- **Golden-vuln regression suite** (`tests/golden_vuln_repo/`, `tests/test_golden_vuln_repo.py`):
  real fixture files (not mocked) run through the actual deterministic-checker
  pipeline, each asserting the specific checker that should fire on it does.
  A coverage-tripwire test fails the suite if a new checker is added to the
  registry without a matching fixture, so checker coverage can't silently
  regress over time.
- **RAG eval CI gate** (`src/eval/rag_eval.py`, `tests/test_rag_eval.py`):
  precision/recall against a small labeled query set, run as a normal pytest
  test (no separate CI script) so retrieval-quality regressions fail the
  build the same way any other test failure would.
- **Red-team prompt-injection corpus** (`tests/test_red_team_prompt_injection.py`):
  runs a corpus of real-world-style injection phrasings through layer 1
  (`prompt_sanitizer.py`) and documents its actual boundary rather than
  assuming full coverage — some phrasings (indirection, base64, no
  imperative verb) are asserted to pass through *unmarked*, since a
  regex-based layer 1 was never going to catch everything. A separate
  corpus of manipulative finding text is run against layer 2
  (`guard_agent.py`) to confirm it's the actual backstop for what layer 1
  misses, plus a fail-open check under a simulated LLM outage.
- **Concurrency correctness** (`tests/test_load_concurrency.py`): real Python
  threads against fakeredis, standing in for "simulate concurrent PR reviews"
  from the spec (no live Celery/Redis stack in this environment). This is how
  a genuine race condition was found: the idempotency check used to be two
  separate Redis calls (`already_processed()` then `mark_processed()`), and
  under real thread interleaving all 20 concurrent callers proceeded instead
  of just one. Fixed in `src/storage/idempotency_store.py` by replacing both
  with a single atomic `try_mark_processed()` (Redis `SET NX EX`), plus a
  `release()` so a claim is given back if the work then fails — otherwise a
  Celery retry would see the commit as already claimed by the failed attempt
  and silently skip redoing it.

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
