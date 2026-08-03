# AI Code Review & DevSecOps Automation

An AI-assisted PR review pipeline: deterministic AST checkers plus an LLM
supplement (grounded against real file content before anything is
surfaced), RAG-backed repo context, CVE/SBOM enrichment, and async
GitHub/GitLab webhook delivery with a durable queue and dead-letter
handling.

**Current stage: Stage 14 — deployment hardening (circuit breaker, canary
rollout, monitoring, CI/CD split, chaos testing). This is the last stage
of the original rewrite roadmap.**

## Running tests locally

```
pip install -e ".[dev]"
pytest
ruff check .
mypy src
```

## Local infra

```
docker compose up                          # everything: redis + postgres + chroma + all 4 app processes
docker compose up redis postgres chroma    # infra only -- pair with running the app processes from the venv below
```

## Running the app processes

If you brought up infra only (not the full `docker compose up`), run the
app processes from the venv directly:

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

## Deployment hardening (Stage 14)

- **Circuit breaker on LLM eval-drift** (`src/core/circuit_breaker.py`):
  a classic CLOSED/OPEN/HALF_OPEN breaker wrapping every LLM call
  (`src/agents/llm_client.py`). Trips on both infra failures (timeouts,
  rate limits — recorded around the raw provider call) and output-quality
  drift (a response that comes back 200 OK but fails to parse as the
  expected JSON shape — recorded in `src/agents/_llm_finding_agent.py`,
  since a model silently degrading in quality without ever raising an
  exception is exactly what a transport-level retry can't catch). When
  open, calls fail fast (`CircuitOpenError`) instead of each queuing its
  own three-attempt backoff against a provider that's already down —
  callers treat this exactly like any other LLM failure and the review
  degrades to deterministic-only (`ReviewStatus.DEGRADED`), never a
  silent clean pass. In-process only (not Redis-backed) — see the
  module's docstring for why that's a deliberate scope boundary, not an
  oversight.
- **Canary prompt rollout** (`src/core/canary.py`): deterministic
  hash-based routing between the stable and a candidate model version,
  keyed on `f"{repo}:{commit_sha}"` so a retried Celery task never flips
  variants mid-flight. `settings.canary_rollout_percent` defaults to `0`
  (always stable) — configure it plus `settings.canary_review_model` (or
  the openai/anthropic equivalents) to opt in. Wired through both the
  single-agent LLM supplement path and the multi-agent path
  (`src/agents/coordinator.py` threads the same key to all four
  specialized agents, so a given review resolves to one variant
  consistently rather than a mix).
- **Monitoring** (`src/core/health.py`, `src/core/metrics.py`): `/health`
  on all three FastAPI apps now does real Redis/Postgres reachability
  checks plus the LLM circuit breaker's state, instead of the old
  unconditional `{"status": "ok"}` stub. `/metrics` on the webhook API
  (gated behind `settings.metrics_enabled`, default off) exposes simple
  in-process counters (`reviews_completed_total`, `dlq_pushes_total`,
  `circuit_breaker_opened_total`, ...) as JSON — deliberately not
  prometheus-client, to avoid adding a new dependency for what a plain
  counter dict already covers at this stage.
- **CI/CD split** (`Dockerfile`, `docker-compose.yml`,
  `.github/workflows/deploy.yml`): the app processes are now
  containerized (previously venv-only — see docker-compose.yml's
  history). `deploy.yml` triggers via `workflow_run` on `ci.yml`'s
  completion (not its own `push` trigger) so a red CI run never reaches
  a deploy attempt; staging deploys automatically once CI passes on
  `main`, production only from a version tag (`v*`) and targets a
  `production` GitHub Environment that should have required reviewers
  configured in this repo's Settings for the manual-approval gate to
  actually take effect. The registry/deploy steps target ghcr.io as a
  realistic placeholder — **not exercised against a live Docker daemon
  in this development environment** (Docker Desktop wasn't running), so
  the Dockerfile is reviewed but not build-verified here; verify with
  `docker build .` before relying on it.
- **Chaos testing** (`docs/chaos_testing.md`, `tests/test_chaos.py`): a
  runbook covering Redis outages, Postgres outages, LLM provider outages,
  malformed webhook payloads, and DLQ growth, each scenario marked
  automated (with a real test backing it) or manual (needs real
  infrastructure to mean anything, e.g. fuzzing a live HTTP server). The
  automated ones inject real failures into the real call paths (e.g. the
  actual provider-call function replaced with one that always raises, so
  the real circuit breaker genuinely trips) rather than mocking around
  the thing being tested.

## GitHub App authentication (src/integrations/github_app_auth.py)

`GitHubClient` supports two auth modes. A personal access token
(`GITHUB_TOKEN`) is simplest and what every example above uses — but
confirmed live against a real repo, GitHub's Check Runs API rejects a
PAT outright with a 403 regardless of what scopes it's granted; only
GitHub App auth is accepted there. Setting `GITHUB_APP_ID` +
`GITHUB_INSTALLATION_ID` + a *readable file* at
`GITHUB_APP_PRIVATE_KEY_PATH` switches `GitHubClient` to sign a JWT and
exchange it for an installation access token automatically (falls back
to the PAT if any of the three isn't satisfied, so partially-configured
App credentials never break anything). `PyJWT[crypto]` is an optional
extra (`pip install -e ".[github-app]"`), imported lazily so a
PAT-only deployment never needs it installed.

**Not exercised against a real GitHub App installation in this
environment** — `tests/test_github_app_auth.py` verifies real JWT
signing/verification against a real (test-generated) RSA keypair, and
`tests/test_github_client.py` verifies the auth-mode selection logic,
but the actual token-exchange HTTP call is only tested against a fake.
To use this for real: create a GitHub App, install it on the target
repo, and put its private key at `secrets/github_app.pem` (or wherever
`GITHUB_APP_PRIVATE_KEY_PATH` points) — that file doesn't ship with this
repo and isn't committed (see `.gitignore`).

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
