
from __future__ import annotations

import argparse

import json
import sys
from datetime import datetime, timezone
from pathlib import Path


from ingestion.github_loader import GitHubLoader, MockGitHubLoader
from ingestion.parser import Parser
from ingestion.chunker import Chunker
from embeddings.embed import Embedder
from mcp.codeintel_mcp import CodeIntelMCP

from vectordb.factory import get_vector_store
from agents.reviewer_agent import ReviewerAgent
from tools.architecture_guard import ArchitectureGuard

from agents.syntax_agent import SyntaxAgent
from agents.runtime_agent import RuntimeAgent
from agents.logic_agent import LogicAgent


def _get_retriever():
    """
    Auto-detect whether a repo index exists and use
    RepoAwareRetriever if it does, otherwise fall back
    to the original Retriever (PR-files only).
    """
    try:
        import chromadb
        from config import cfg
        client = chromadb.PersistentClient(path=cfg.chroma_dir)
        col    = client.get_or_create_collection("repo_index")
        if col.count() > 0:
            from rag.repo_retriever import RepoAwareRetriever
            print(
                f"[app] Using RepoAwareRetriever "
                f"({col.count()} vectors in repo index)"
            )
            return RepoAwareRetriever()
    except Exception:
        pass

    from rag.retriever import Retriever
    print("[app] Using standard Retriever (no repo index found)")
    print(
        "[app] Tip: run  python index_repo.py --repo owner/repo  "
        "to enable full repo context"
    )
    return Retriever()

def _run_linear_review(files, pr_ctx, retriever_getter=_get_retriever):
    retriever = retriever_getter()
    reviewer = ReviewerAgent(retriever=retriever)
    report = reviewer.review_pr(
        files=files,
        pr_title=pr_ctx.title,
        pr_description=pr_ctx.description,
    )
    return report
# ─────────────────────────────────────────────────────────────────────────────
# CORE PIPELINE
# ─────────────────────────────────────────────────────────────────────────────

def _get_loader(provider: str, mock: bool):
    """
    provider: "github" or "gitlab". The rest of the pipeline (parsing,
    review, security, autofix, ...) is provider-agnostic — it only ever
    calls loader.load_pr()/post_review_comments(), so any loader that
    implements those two methods against PRFile/PRContext works here.

    Note: a few specialized posting agents (autofix suggestions,
    architecture/compliance PR comments, PR gate) still call the GitHub
    REST API directly rather than going through this loader abstraction —
    against a GitLab repo those steps no-op (they're wrapped in
    try/except) rather than posting. The core load -> review -> summary
    comment flow works for both providers.
    """
    if provider == "gitlab":
        from ingestion.gitlab_loader import GitLabLoader, MockGitLabLoader
        return MockGitLabLoader() if mock else GitLabLoader()
    return MockGitHubLoader() if mock else GitHubLoader()


def run_review(
    repo: str,
    pr_number: int,
    mock: bool = False,
    output_dir: str = "reports",
    provider: str = "github",
) -> dict:
    """
    Full ingestion + review pipeline for a single PR.
    Returns the review report dict.
    """

    # ── 1. Load PR ───────────────────────────────────────
    loader = _get_loader(provider, mock)
    print(f"\n[app] Loading PR #{pr_number} from {repo}…")
    pr_ctx = loader.load_pr(repo, pr_number)

    if not pr_ctx.files:
        print("[app] No reviewable files found.")
        return {}

    files = pr_ctx.files

    # ─────────────────────────────────────────────
    # Syntax / Runtime / Logic Analysis
    # ─────────────────────────────────────────────

    from mcp.codeintel_mcp import CodeIntelMCP

    codeintel = CodeIntelMCP()

    extra_findings = []

    for pf in files:

        result = codeintel.scan(pf)

        print(
        f"[debug] {pf.filename}: "
        f"syntax={len(result['syntax'])}, "
        f"runtime={len(result['runtime'])}, "
        f"logic={len(result['logic'])}"
        )

        extra_findings.extend(
        result["all"]
            )

    print(
    f"[app] Static analysis found "
    f"{len(extra_findings)} issues"
    )

    
    # ── 2. Parse ─────────────────────────────────────────
    parser   = Parser()
    sections = parser.parse_many(files)
    print(f"[app] Parsed {len(sections)} sections")

    # ── 3. Chunk ─────────────────────────────────────────
    chunker = Chunker()
    chunks  = chunker.chunk_sections(sections)
    print(f"[app] Created {len(chunks)} chunks")

    # ── 4. Embed ─────────────────────────────────────────
    embedder       = Embedder()
    embedded_chunks = embedder.embed_chunks(chunks)
    print(f"[app] Embedded {len(embedded_chunks)} chunks")

    # ── 5. Store ─────────────────────────────────────────
    store = get_vector_store()
    store.upsert(embedded_chunks)
    print(f"[app] Vector store total: {store.count()}")

    # ── 6. Review ─────────────────────────────────────────
    print(f"[app] Reviewing {len(files)} files…")

    try:
        from agents.langgraph_agent import run_advanced_review

        print("[app] Using LangGraph pipeline")

        report = run_advanced_review(
        pr_ctx=pr_ctx,
        repo=repo,
        post_to_github=not mock,
        )

        skip_github_post = report.get(
    "already_posted_to_github",
    False
)

    except ImportError:
        print("[app] LangGraph not installed")
        print("[app] Install with: pip install langgraph langchain-core")

        report = _run_linear_review(files, pr_ctx)
        skip_github_post = False

    except Exception as e:
        print(f"[app] LangGraph failed: {e}")
        print("[app] Falling back to linear pipeline")

        report = _run_linear_review(files, pr_ctx)
        skip_github_post = False

    # Attach PR metadata to report
    report["repo"]      = repo
    report["pr_number"] = pr_number
    report["head_sha"]  = pr_ctx.head_sha
    report["pr_title"]  = pr_ctx.title
    report["reviewed_at"] = datetime.now(
    timezone.utc
    ).isoformat()

    report.setdefault("findings", [])
    report["findings"].extend(extra_findings)
    print(
    f"[app] Added "
    f"{len(extra_findings)} syntax/runtime/logic findings"
)
    

    # ─────────────────────────────────────────────
    # SECRET SCAN (Gitleaks / TruffleHog)
    # ─────────────────────────────────────────────

    try:
        from tools.secret_scanner import SecretScanner

        print("[app] Running Secret Scan...")

        from mcp.security_mcp import SecurityMCP

        scanner = SecretScanner()
        repo_path = getattr(pr_ctx, "repo_path", ".")

        scan_report = scanner.scan(
            repo_path=repo_path,
            repo_name=repo,
            since_commit=getattr(pr_ctx, "base_sha", None),
        )
        all_secret_findings = [f.to_dict() for f in scan_report.findings]

        pr_filenames = {pf.filename for pf in files}
        secret_findings = [f for f in all_secret_findings if f.get("file") in pr_filenames]
        skipped_count = len(all_secret_findings) - len(secret_findings)
        if skipped_count:
            print(f"[app] Secret scan: {skipped_count} finding(s) outside this PR's files — excluded from report")

        report.setdefault("findings", [])
        report["findings"].extend(secret_findings)

        print(
        f"[app] Secret scan found "
        f"{len(secret_findings)} secrets"
        )

    except Exception as e:
        print(f"[app] Secret scan failed: {e}")

    # ─────────────────────────────────────────────
    # SONARQUBE (second static analyzer, alongside Semgrep)
    # ─────────────────────────────────────────────

    try:
        from tools.sonarqube_scanner import SonarQubeScanner

        sonar = SonarQubeScanner()
        if sonar.available:
            print("[app] Running SonarQube scan...")
            sonar_findings = sonar.scan_files(files, repo_root=getattr(pr_ctx, "repo_path", "."))
            report["findings"].extend(sonar_findings)
            print(f"[app] SonarQube findings: {len(sonar_findings)}")
        else:
            print("[app] SonarQube not configured — skipping (set SONAR_TOKEN, "
                  "SONAR_HOST_URL, SONAR_PROJECT_KEY to enable)")

    except Exception as e:
        print(f"[app] SonarQube scan failed: {e}")


    # ─────────────────────────────────────────────
    # ARCHITECTURE DRIFT DETECTION
    # ─────────────────────────────────────────────

    try:
        print("[app] Running Architecture Drift Detection...")

        guard = ArchitectureGuard(
        adr_dir="docs/adr"
        )

        violations = guard.check_pr(files)

        drift_findings = guard.violations_to_findings(
        violations
        )

        report.setdefault("findings", [])
        report["findings"].extend(drift_findings)

        

        print(
        f"[app] Architecture findings: "
        f"{len(drift_findings)}"
        )

    except Exception as e:
        print(f"[app] Architecture scan failed: {e}")

    # ─────────────────────────────────────────────
    # COMPLIANCE CHECKING (internal standards)
    # ─────────────────────────────────────────────

    try:
        from tools.compliance_guard import ComplianceGuard
        print("[app] Running Compliance Check...")

        compliance = ComplianceGuard(standards_dir="docs/standards")
        compliance_findings = compliance.scan(files)

        report.setdefault("findings", [])
        report["findings"].extend(compliance_findings)

        print(f"[app] Compliance findings: {len(compliance_findings)}")

    except Exception as e:
        print(f"[app] Compliance scan failed: {e}")

    # ─────────────────────────────────────────────
    # AUTO FIX AGENT
    # ─────────────────────────────────────────────
    findings = report.setdefault("findings", [])
    try:
        from agents.auto_fix_orchestrator import AutoFixOrchestrator
        print("[app] Running Auto Fix Agent...")

        auto_fix = AutoFixOrchestrator()
        auto_result_raw = auto_fix.execute(
            repo      = repo,
            branch    = pr_ctx.head_branch,
            findings  = report.get("findings", []),
            pr_files  = files,
            pr_number = pr_number,
            head_sha  = pr_ctx.head_sha,
            loader    = loader,
        )
        auto_result = {
            "fixed_count": auto_result_raw["fixed_count"],
            "unfixable":   auto_result_raw["unresolved_count"],
            "status":      "success",
            "unresolved":  auto_result_raw["unresolved"],
        }
        report["auto_fix"] = auto_result

        print(
            f"[app] Auto fixed "
            f"{auto_result.get('fixed_count', 0)} issues"
        )



    except Exception as e:
        print(f"[app] AutoFix failed: {e}")

        report["auto_fix"] = {
            "fixed_count": 0,
            "status": "failed",
            "error": str(e)
        }

    # ─────────────────────────────────────────────
    # DEVELOPER SKILL-GAP PROFILING
    # ─────────────────────────────────────────────

    try:
        from tools.skill_profiler import SkillProfiler
        print("[app] Updating skill profile...")

        profiler = SkillProfiler()
        profiler.record(
            author=pr_ctx.author,
            findings=report.get("findings", []),
            pr_number=pr_number,
            repo=repo,
            score=report.get("overall_score", 1.0),
        )
        report["skill_profile"] = {
            "author": pr_ctx.author,
            "growth_report": profiler.growth_report(pr_ctx.author),
        }

        print(f"[app] Skill profile updated for {pr_ctx.author}")

    except Exception as e:
        print(f"[app] Skill profiling failed: {e}")

    # ─────────────────────────────────────────────
    # POST REVIEW TO GITHUB
    # ─────────────────────────────────────────────

    if not mock and not skip_github_post:

        print("[app] Posting review to GitHub PR...")

        exec_summary = (
            report.get("executive_summary", {}).get(
                "executive_summary", ""
            )
            or f"Reviewed {len(files)} files, "
               f"found {report.get('total_findings', 0)} issues."
        )

        try:
            loader.post_review_comments(
                repo=repo,
                pr_number=pr_number,
                head_sha=pr_ctx.head_sha,
                findings=report.get("findings", []),
                summary=exec_summary,
                approved=report.get("approved", False),
            )

            print("[app] Review posted to GitHub successfully!")

        except Exception as e:
            print(f"[app] Could not post to GitHub: {e}")

        try:
            from agents.pr_gate import PRGate

            gate = PRGate(loader=loader)

            gate_result = gate.evaluate(
                repo=repo,
                pr_number=pr_number,
                head_sha=pr_ctx.head_sha,
                report=report,
            )

            report["gate"] = {
                "blocked": gate_result.blocked,
                "reason": gate_result.reason,
                "resolved_issues": gate_result.resolved_issues,
                "new_issues": gate_result.new_issues,
                "still_present": gate_result.still_present,
                "score_before": gate_result.score_before,
                "score_after": gate_result.score_after,
            }

        except Exception as e:
            print(f"[app] PR gate failed: {e}")

    else:
        print("[app] --mock mode: skipping GitHub post and PR gate")


    # ─────────────────────────────────────────────
    # FINAL METRICS RECALCULATION
    # ─────────────────────────────────────────────

    # Recalculate totals from ALL findings
    report["total_findings"] = len(report.get("findings", []))

    report["critical_count"] = sum(
        1
        for f in report.get("findings", [])
        if (
        f.get("severity")
        if isinstance(f, dict)
        else getattr(f, "severity", "")
        ) == "critical"
        )

    report["warning_count"] = sum(
        1
        for f in report.get("findings", [])
        if (
        f.get("severity")
        if isinstance(f, dict)
        else getattr(f, "severity", "")
    ) == "warning"
    )

    # Findings that still remain after AutoFix
    remaining_findings = report.get(
    "auto_fix",
    {}
     ).get(
    "unresolved",
    report.get("findings", [])
    )

    remaining_critical = sum(
    1
    for f in remaining_findings
    if (
        f.get("severity")
        if isinstance(f, dict)
        else getattr(f, "severity", "")
    ) == "critical"
    )

    remaining_warning = sum(
    1
    for f in remaining_findings
    if (
        f.get("severity")
        if isinstance(f, dict)
        else getattr(f, "severity", "")
    ) == "warning"
    )

    report["remaining_critical"] = remaining_critical
    report["remaining_warning"] = remaining_warning

    # Gate decision
    if remaining_critical > 0:
        report["gate_decision"] = "BLOCK"

    elif remaining_warning > 5:
        report["gate_decision"] = "REVIEW_REQUIRED"

    elif report.get("auto_fix", {}).get("status") == "failed":
        report["gate_decision"] = "REVIEW_REQUIRED"

    else:
        report["gate_decision"] = "APPROVE"
    report["approved"] = (report["gate_decision"] == "APPROVE")

    # ─────────────────────────────────────────────
    # SAVE REPORT
    # ─────────────────────────────────────────────

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")

    out_file = (
        out_dir /
        f"review_{repo.replace('/', '_')}_pr{pr_number}_{ts}.json"
    )

    out_file.write_text(
        json.dumps(report, indent=2),
        encoding="utf-8"
    )

    try:
        from storage.postgres_store import save_report, is_configured
        if is_configured():
            save_report(report)
    except Exception as e:
        print(f"[app] Postgres save skipped: {e}")

    _print_summary(report, out_file)

    return report

def _print_summary(report: dict, out_file: Path) -> None:
    score    = report.get("overall_score", 0)
    total    = report.get("total_findings", 0)
    critical = report.get("remaining_critical",report.get("critical_count", 0))
    warnings = report.get("remaining_warning",report.get("warning_count", 0))
    
    approved = report.get("approved", False)

    bar = "█" * int(score * 20) + "░" * (20 - int(score * 20))

    print("\n" + "═" * 50)
    print(f"  PR Review Complete")
    print("═" * 50)
    print(f"  Score    : [{bar}] {score:.2f}")
    print(f"  Decision : {'✅ APPROVED' if approved else '❌ CHANGES REQUESTED'}")
    print(f"  Findings : {total} total  ({critical} critical, {warnings} warnings)")
    print(f"  Report   : {out_file}")
    print("═" * 50)

    if critical > 0:
        print("\n🔴 Critical Issues:")

        for f in report.get("findings", []):

            severity = (
            f.get("severity")
            if isinstance(f, dict)
            else getattr(f, "severity", "")
            )

            if severity == "critical":

                file_name = (
                f.get("file", "unknown")
                if isinstance(f, dict)
                else getattr(f, "file", "unknown")
                )

                line_no = (
                f.get("line", 0)
                if isinstance(f, dict)
                else getattr(f, "line", 0)
                )

                message = (
                f.get("message", "")
                if isinstance(f, dict)
                else getattr(f, "message", "")
                )

                print(
                f" • [{file_name}:{line_no}] "
                f"{message[:80]}"
                )
    exec_obj = report.get("executive_summary")

    if isinstance(exec_obj, str):
        exec_summary = exec_obj

    elif isinstance(exec_obj, dict):
        exec_summary = exec_obj.get(
        "executive_summary",
        ""
    )

    else:
        exec_summary = ""

    if exec_summary:
        print(f"\n📋 Summary:\n{exec_summary}")


# ─────────────────────────────────────────────────────────────────────────────
# FASTAPI WEBHOOK  (optional — run with: uvicorn app:fastapi_app)
# ─────────────────────────────────────────────────────────────────────────────

def _dispatch_review(repo: str, pr_number: int, mock: bool = False,
                      output_dir: str = "reports", provider: str = "github") -> dict:
    """
    Runs a review, via Temporal (retries + observability) when
    TEMPORAL_ADDRESS is configured, otherwise calling run_review()
    directly — same pipeline either way, this only changes who
    orchestrates it.
    """
    from workflows.temporal_workflow import is_configured as temporal_configured
    if temporal_configured():
        from workflows.temporal_workflow import submit_review
        return submit_review(repo, pr_number, mock, output_dir, provider)
    return run_review(repo, pr_number, mock, output_dir, provider)


try:
    import hashlib
    import hmac
    from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
    from config import cfg

    fastapi_app = FastAPI(title="AI Code Review")

    @fastapi_app.post("/webhook/github")
    async def github_webhook(
        request: Request,
        background_tasks: BackgroundTasks,
    ):
        body = await request.body()

        # Verify webhook signature
        sig = request.headers.get("X-Hub-Signature-256", "")
        if cfg.github_webhook_secret:
            expected = "sha256=" + hmac.new(
                cfg.github_webhook_secret.encode(),
                body,
                hashlib.sha256,
            ).hexdigest()
            if not hmac.compare_digest(sig, expected):
                raise HTTPException(status_code=401, detail="Invalid signature")

        payload = json.loads(body)
        action  = payload.get("action", "")

        if action not in ("opened", "synchronize", "reopened"):
            return {"status": "skipped", "action": action}

        repo      = payload["repository"]["full_name"]
        pr_number = payload["pull_request"]["number"]

        background_tasks.add_task(_dispatch_review, repo, pr_number)
        return {"status": "queued", "repo": repo, "pr": pr_number}

    @fastapi_app.post("/webhook/gitlab")
    async def gitlab_webhook(
        request: Request,
        background_tasks: BackgroundTasks,
    ):
        body = await request.body()

        # GitLab uses a static shared-secret header, not an HMAC signature.
        token = request.headers.get("X-Gitlab-Token", "")
        if cfg.gitlab_webhook_secret and not hmac.compare_digest(token, cfg.gitlab_webhook_secret):
            raise HTTPException(status_code=401, detail="Invalid token")

        payload = json.loads(body)
        if payload.get("object_kind") != "merge_request":
            return {"status": "skipped", "reason": "not a merge_request event"}

        attrs  = payload.get("object_attributes", {})
        action = attrs.get("action", "")
        if action not in ("open", "update", "reopen"):
            return {"status": "skipped", "action": action}

        repo      = payload.get("project", {}).get("path_with_namespace", "")
        pr_number = attrs.get("iid")

        background_tasks.add_task(_dispatch_review, repo, pr_number, False, "reports", "gitlab")
        return {"status": "queued", "repo": repo, "pr": pr_number}

    @fastapi_app.get("/health")
    async def health():
        return {"status": "ok"}

except Exception as e:
    print(f"[app] FastAPI webhook disabled (not needed for CLI use): {e}")
    fastapi_app = None


# ─────────────────────────────────────────────────────────────────────────────
# CLI ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="AI Code Review — review a GitHub PR"
    )
    parser.add_argument(
        "--repo", type=str,
        help="GitHub repo (e.g. owner/repo)",
        default="",
    )
    parser.add_argument(
        "--pr", type=int,
        help="Pull request number",
        default=1,
    )
    parser.add_argument(
        "--mock", action="store_true",
        help="Use MockGitHubLoader (no credentials needed)",
    )
    parser.add_argument(
        "--provider", type=str, choices=["github", "gitlab"], default="github",
        help="Which platform to load the PR/MR from (default: github)",
    )
    parser.add_argument(
        "--output", type=str,
        default="reports",
        help="Output directory for review reports",
    )
    args = parser.parse_args()

    if not args.repo and not args.mock:
        print("Usage: python app.py --repo owner/repo --pr 42")
        print("       python app.py --repo group/project --pr 42 --provider gitlab")
        print("       python app.py --mock  (offline demo)")
        sys.exit(1)

    repo = args.repo or "demo/repo"
    report = run_review(
        repo=repo, pr_number=args.pr, mock=args.mock,
        output_dir=args.output, provider=args.provider,
    )

    # Non-zero exit on unresolved critical findings so CI (GitHub Actions,
    # Jenkins, etc.) can fail the build/block the merge on this check.
    if report.get("remaining_critical", report.get("critical_count", 0)) > 0:
        print(f"[app] BLOCKED — {report.get('remaining_critical', report.get('critical_count'))} "
              f"unresolved critical finding(s)")
        sys.exit(1)


if __name__ == "__main__":
    main()