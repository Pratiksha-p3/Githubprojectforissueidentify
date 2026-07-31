"""
src/api/fix_actions.py

Accept/reject endpoints for a suggested fix on a Finding, plus the
resulting acceptance-rate data feeding the learning loop
(src/storage/decision_log.py).

Enforced here: `auto_apply_permitted` in the response only ever reflects
src/core/confidence.py's is_safe_to_auto_apply() — the single source of
truth for "is this confidence tier mechanically safe to write without a
human looking at it" — not re-implemented as a separate check here. Note
this endpoint records the decision and reports whether auto-apply is
*permitted*; it does not itself perform a GitHub commit (that needs the
fix's actual replacement text + current file content, which aren't part
of this narrow accept/reject action — see src/integrations/publisher.py
for the piece that actually writes to GitHub).
"""
from __future__ import annotations

from fastapi import Depends, FastAPI
from pydantic import BaseModel

from src.core.confidence import is_safe_to_auto_apply
from src.core.health import full_health_report
from src.core.models import ConfidenceTier, Finding, Severity
from src.storage.decision_log import DecisionLog

app = FastAPI(title="AI Code Review Fix Actions")


class FixDecisionRequest(BaseModel):
    repo: str
    commit_sha: str
    file: str
    line: int
    finding_source: str
    confidence: ConfidenceTier
    actor: str = "unknown"


def get_decision_log() -> DecisionLog:
    return DecisionLog()


def _auto_apply_permitted(confidence: ConfidenceTier) -> bool:
    placeholder = Finding(
        file="", line=0, category="unknown", severity=Severity.INFO,
        message="", confidence=confidence,
    )
    return is_safe_to_auto_apply(placeholder)


@app.post("/fix-actions/accept")
def accept_fix(req: FixDecisionRequest, log: DecisionLog = Depends(get_decision_log)) -> dict:
    log.record_decision(
        repo=req.repo,
        commit_sha=req.commit_sha,
        file=req.file,
        line=req.line,
        finding_source=req.finding_source,
        confidence=req.confidence.value,
        decision="accepted",
        actor=req.actor,
    )
    return {
        "decision": "accepted",
        "auto_apply_permitted": _auto_apply_permitted(req.confidence),
    }


@app.post("/fix-actions/reject")
def reject_fix(req: FixDecisionRequest, log: DecisionLog = Depends(get_decision_log)) -> dict:
    log.record_decision(
        repo=req.repo,
        commit_sha=req.commit_sha,
        file=req.file,
        line=req.line,
        finding_source=req.finding_source,
        confidence=req.confidence.value,
        decision="rejected",
        actor=req.actor,
    )
    return {"decision": "rejected", "auto_apply_permitted": False}


@app.get("/fix-actions/acceptance-rates")
def acceptance_rates(log: DecisionLog = Depends(get_decision_log)) -> dict:
    return {"rates": log.acceptance_rates()}


@app.get("/health")
def health() -> dict:
    return full_health_report()
