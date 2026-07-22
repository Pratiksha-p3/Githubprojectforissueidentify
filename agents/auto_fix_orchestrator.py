# agents/auto_fix_orchestrator.py
#
# auto_fix_runner.py has been removed — it was a pure pass-through that
# forwarded all six args to AutoFixEngine.process_findings() unchanged.
# This orchestrator now calls the engine directly.
#
# GitHubAutoCommit has also been dropped from here — it was imported and
# instantiated but never called anywhere in execute(). AutoFixEngine already
# posts suggestions to GitHub itself via _post_suggestion(). If you have a
# separate "commit the fix directly to the branch" flow that's supposed to
# use GitHubAutoCommit, wire it in explicitly below (see TODO) rather than
# leaving it instantiated with no call site.

from agents.autofix_engine import AutoFixEngine


class AutoFixOrchestrator:

    def __init__(self):
        self.engine = AutoFixEngine()
        # TODO: if direct-commit (not suggestion-comment) flow is needed,
        # import and wire in agents.github_auto_commit.GitHubAutoCommit here,
        # and call it explicitly in execute() below.

    def execute(
        self,
        repo,
        branch,
        findings,
        pr_files,
        pr_number,
        head_sha,
        loader,
    ):
        """
        Orchestrates the auto-fix process:
        1. Runs AutoFixEngine to attempt fixes on findings.
        2. Returns a summary of fixed and unresolved findings.
        """
        safe_findings = [f for f in findings if isinstance(f, dict)]

        fixed, unresolved = self.engine.process_findings(
            findings=safe_findings,
            pr_files=pr_files,
            repo=repo,
            pr_number=pr_number,
            head_sha=head_sha,
            loader=loader,
        )

        return {
            "fixed_count": len([f for f in fixed if f.fix_applied]),
            "fixed_files": [f.finding["file"] for f in fixed if f.fix_applied],
            "unresolved_count": len(unresolved),
            "unresolved": unresolved,
        }