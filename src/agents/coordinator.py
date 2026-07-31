"""
src/agents/coordinator.py

Runs every specialized LLM agent (runtime/logic via llm_supplement,
security, style, test_coverage) and merges their findings — Stage 11's
multi-agent refactor of Stage 2's single LLM-supplement pass. Every
finding still passes through the same grounding/confidence trust layer
(src/core/grounding.py's is_trustworthy) before being returned — running
multiple agents doesn't get a looser or different trust boundary than
the single-agent path.

This is opt-in (src/core/orchestrator.py's review_code(use_multi_agent=
True)), not the default: running 4 specialized LLM calls per file
instead of 1 meaningfully multiplies cost/latency/rate-limit exposure —
a real, previously-hit pain point this session with Groq's per-minute
token budget — so a project should turn this on deliberately, not have
it silently quadruple LLM spend on every review.

"succeeded" means every agent that ran completed without error — one
agent failing means DEGRADED for the whole result, the same all-or-
nothing honesty principle src/core/orchestrator.py already applies to
the single-agent path.

`canary_key` (Stage 14, src/core/canary.py) is threaded through to all
four agents so the multi-agent path gets the same deterministic
stable/canary routing as the single-agent path — every agent for a given
review resolves to the same variant (all stable or all canary), not a
mix, since each agent independently hashes the same key.
"""
from __future__ import annotations

from src.agents import llm_supplement, security_agent, style_agent, test_coverage_agent
from src.core.grounding import is_trustworthy
from src.core.models import Finding


def run_all_agents(
    code: str, filename: str, *, context: str = "", canary_key: str | None = None
) -> tuple[list[Finding], bool]:
    # Each call goes through the module object (llm_supplement.<fn>(...),
    # not a pre-bound reference captured once at import time) so tests
    # can monkeypatch e.g. `coordinator.security_agent.
    # get_security_findings_with_status` and have it actually take
    # effect on every call, the same patching pattern used everywhere
    # else in this codebase.
    agent_results = (
        llm_supplement.get_llm_findings_with_status(
            code, filename, context=context, canary_key=canary_key
        ),
        security_agent.get_security_findings_with_status(
            code, filename, context=context, canary_key=canary_key
        ),
        style_agent.get_style_findings_with_status(
            code, filename, context=context, canary_key=canary_key
        ),
        test_coverage_agent.get_test_coverage_findings_with_status(
            code, filename, context=context, canary_key=canary_key
        ),
    )

    all_findings: list[Finding] = []
    all_succeeded = True
    for findings, succeeded in agent_results:
        all_findings.extend(f for f in findings if is_trustworthy(f, code))
        all_succeeded = all_succeeded and succeeded

    return _dedupe(all_findings), all_succeeded


def _dedupe(findings: list[Finding]) -> list[Finding]:
    """Two agents can legitimately flag the same line (e.g. security_agent
    and the logic pass both notice a bad conditional with security
    implications) — keep the first (agent-priority-ordered) finding per
    (line, category) pair rather than showing duplicates."""
    seen: set[tuple[int, str]] = set()
    deduped = []
    for f in findings:
        key = (f.line, f.category)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(f)
    return deduped
