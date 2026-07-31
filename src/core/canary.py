"""
src/core/canary.py

Stage 14's "canary prompt rollout" — deterministic hash-based traffic
splitting between the stable and candidate ("canary") model/prompt
version, so a small percentage of real traffic can be routed to a
candidate before it fully replaces the stable one.

variant_for() is deterministic per key (not a coin flip per call): the
same (repo, commit_sha) always resolves to the same variant, including
across a Celery retry of the same task — a retried review flipping from
stable to canary (or back) mid-investigation would make any quality
comparison between the two variants meaningless, since you'd no longer
know which variant actually produced a given result.

This module only decides routing. Judging whether a canary is actually
performing better/worse is the eval harness's job (src/eval/rag_eval.py
for retrieval quality; the golden-vuln suite in tests/golden_vuln_repo/
for checker-level regressions) — promotion/rollback of a canary based on
those results stays a human decision for now, consistent with
src/storage/decision_log.py's existing note that automated eval-drift
retuning is intentionally out of scope until real production data exists
to retune against.
"""
from __future__ import annotations

import hashlib

_CANARY_BUCKET_SIZE = 100


def variant_for(key: str, rollout_percent: int) -> str:
    """
    Returns "canary" or "stable" for `key`, deterministically, such that
    approximately `rollout_percent` of distinct keys resolve to "canary"
    over a large enough population of keys. `rollout_percent` outside
    [0, 100] is clamped rather than raising — a misconfigured value
    should degrade to "always stable" or "always canary", not crash the
    review pipeline over a rollout dial.
    """
    rollout_percent = max(0, min(100, rollout_percent))
    if rollout_percent == 0:
        return "stable"
    if rollout_percent == 100:
        return "canary"
    digest = hashlib.sha256(key.encode()).hexdigest()
    bucket = int(digest[:8], 16) % _CANARY_BUCKET_SIZE
    return "canary" if bucket < rollout_percent else "stable"
