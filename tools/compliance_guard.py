"""
tools/compliance_guard.py

Compliance checking against internal standards — the counterpart to
architecture_guard.py, but for coding/security/process standards docs
instead of Architecture Decision Records.

Examples of what it catches:
  - "All public API endpoints MUST have rate limiting"
    -> PR adds a new @app.route with no rate limiter -> FLAGGED
  - "NEVER log request bodies containing PII fields (email, ssn, dob)"
    -> PR adds logger.info(request.body) -> FLAGGED
  - "All new dependencies MUST be reviewed and pinned to an exact version"
    -> PR adds an unpinned package to requirements.txt -> FLAGGED

How it works (same shape as architecture_guard.py):
  1. Reads standards markdown files from docs/standards/
  2. Embeds them into ChromaDB (separate collection from ADRs)
  3. For each PR file, finds relevant standards via semantic search
  4. Asks the LLM: "Does this code violate any of these standards?"
  5. Returns structured findings in the same schema as every other checker

Usage:
  python tools/compliance_guard.py --index --standards-dir docs/standards/
  python tools/compliance_guard.py --check --pr 3 --repo owner/repo
  python tools/compliance_guard.py --create-sample-standards
"""
from __future__ import annotations

import json
import re
import requests
from dataclasses import dataclass, field
from pathlib import Path
from config import cfg


STANDARDS_COLLECTION = "internal_standards"
STANDARDS_DIR         = Path("./docs/standards")


@dataclass
class Standard:
    """A single internal standards document."""
    id:       str
    title:    str
    filename: str
    rules:    list[str] = field(default_factory=list)


@dataclass
class ComplianceViolation:
    standard_id: str
    standard_title: str
    rule:        str
    file:        str
    line:        int
    code:        str
    severity:    str        # critical | warning
    explanation: str
    fix:         str


class StandardsParser:
    """Parses internal-standards markdown files into Standard objects."""

    def parse_directory(self, standards_dir: str | Path) -> list[Standard]:
        std_path = Path(standards_dir)
        if not std_path.exists():
            print(f"[compliance] Standards directory not found: {std_path}")
            print(f"[compliance] Creating sample standards docs...")
            self.create_sample_standards(std_path)

        standards = []
        for md_file in sorted(std_path.glob("*.md")):
            std = self.parse_file(md_file)
            if std and std.rules:
                standards.append(std)
                print(f"  [compliance] Loaded standard: {std.id} — {std.title}")

        print(f"[compliance] Loaded {len(standards)} standards documents")
        return standards

    def parse_file(self, filepath: Path) -> Standard | None:
        try:
            content = filepath.read_text(encoding="utf-8")
            return self._parse_markdown(content, filepath.name)
        except Exception as e:
            print(f"[compliance] Could not parse {filepath}: {e}")
            return None

    def _parse_markdown(self, content: str, filename: str) -> Standard:
        std_id = re.match(r"(\d+)", filename)
        std_id = std_id.group(1) if std_id else filename

        title_m = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
        title   = title_m.group(1).strip() if title_m else filename

        rules = self._extract_rules(content)

        return Standard(id=std_id, title=title, filename=filename, rules=rules)

    def _extract_rules(self, text: str) -> list[str]:
        rules = []
        for m in re.finditer(r"^[\*\-]\s+(.+)$", text, re.MULTILINE):
            rule = m.group(1).strip()
            if len(rule) > 15:
                rules.append(rule)
        for m in re.finditer(r"^\d+\.\s+(.+)$", text, re.MULTILINE):
            rule = m.group(1).strip()
            if len(rule) > 15:
                rules.append(rule)
        for m in re.finditer(
            r"(?:MUST|MUST NOT|SHALL|SHOULD|SHOULD NOT|NEVER|ALWAYS)\s+.+[.!]",
            text, re.IGNORECASE,
        ):
            rules.append(m.group(0).strip())
        return rules[:15]

    def create_sample_standards(self, standards_dir: Path) -> None:
        standards_dir.mkdir(parents=True, exist_ok=True)

        samples = [
            {
                "filename": "001-logging-and-pii.md",
                "content": """# Logging and PII Handling

## Rules

- NEVER log full request/response bodies that may contain PII (email, phone, SSN, address, DOB).
- ALWAYS redact or hash user identifiers before logging (log a user_id, not an email).
- MUST NOT log raw authentication tokens, passwords, or API keys, even at debug level.
- SHOULD use structured logging (key=value) instead of free-text string concatenation.
""",
            },
            {
                "filename": "002-dependency-management.md",
                "content": """# Dependency Management

## Rules

- ALL new third-party dependencies MUST be pinned to an exact version in requirements.txt.
- NEVER add a dependency with a known critical CVE without an approved exception.
- MUST NOT vendor a package's source directly into the repo instead of using the package manager.
- New dependencies SHOULD prefer actively maintained packages (a commit within the last 12 months).
""",
            },
            {
                "filename": "003-api-endpoint-standards.md",
                "content": """# Public API Endpoint Standards

## Rules

- ALL public-facing endpoints MUST validate and sanitize input before processing.
- NEVER return raw internal exception messages or stack traces in an API response.
- ALL endpoints that accept file uploads MUST enforce a maximum file size and type allowlist.
- SHOULD apply rate limiting to any endpoint that triggers an expensive operation (LLM call, DB write, external API call).
""",
            },
        ]

        for s in samples:
            path = standards_dir / s["filename"]
            path.write_text(s["content"], encoding="utf-8")
            print(f"  [compliance] Created sample standard: {path}")

        print(f"[compliance] Created {len(samples)} sample standards docs in {standards_dir}")


class ComplianceGuard:
    """
    Checks PR changes against internal standards documents, the same
    semantic-search-then-LLM-check shape as ArchitectureGuard, but for
    process/security/coding standards rather than architecture decisions.
    """

    def __init__(self, standards_dir: str = "./docs/standards"):
        self.standards_dir = Path(standards_dir)
        self.parser = StandardsParser()
        self._col = None
        self._embed = None
        self._standards: list[Standard] = []

    # ── Public API ────────────────────────────────────────

    def index_standards(self) -> int:
        self._standards = self.parser.parse_directory(self.standards_dir)
        if not self._standards:
            return 0

        col = self._get_collection()
        for std in self._standards:
            text = f"{std.title}\n\nRules:\n" + "\n".join(std.rules)
            vec = self._embed_text(text)
            col.upsert(
                ids=[f"standard-{std.id}"],
                embeddings=[vec],
                documents=[text],
                metadatas=[{
                    "standard_id": std.id,
                    "title": std.title,
                    "filename": std.filename,
                    "rules": json.dumps(std.rules),
                }],
            )
        print(f"[compliance] Indexed {len(self._standards)} standards into ChromaDB")
        return len(self._standards)

    def check_pr(self, pr_files) -> list[ComplianceViolation]:
        col = self._get_collection()
        if col.count() == 0:
            print("[compliance] No standards indexed. Run --index first.")
            self.index_standards()
            col = self._get_collection()

        if not self._standards:
            self._standards = self.parser.parse_directory(self.standards_dir)

        all_violations = []
        for pf in pr_files:
            if not getattr(pf, "patch", ""):
                continue
            relevant = self._find_relevant_standards(pf, col)
            if not relevant:
                continue
            all_violations.extend(self._check_violations(pf, relevant))

        if all_violations:
            print(f"[compliance] {len(all_violations)} compliance violations found")
        else:
            print(f"[compliance] No compliance violations detected")
        return all_violations

    def scan(self, files) -> list[dict]:
        """Compatibility wrapper for app.py — same pattern as ArchitectureGuard.scan()."""
        return self.violations_to_findings(self.check_pr(files))

    def violations_to_findings(self, violations: list[ComplianceViolation]) -> list[dict]:
        return [
            {
                "file": v.file,
                "line": v.line,
                "severity": v.severity,
                "category": "compliance",
                "message": (
                    f"[STD-{v.standard_id}] {v.standard_title}: {v.rule}\n"
                    f"Offending code: `{v.code}`\n{v.explanation}"
                ),
                "fix": v.fix,
                "source": "compliance_guard",
                "standard_id": v.standard_id,
            }
            for v in violations
        ]

    def post_to_pr(self, violations, repo: str, pr_number: int, loader) -> None:
        if not violations:
            return
        lines = [f"## \U0001f4cb Compliance Check — {len(violations)} violation(s)\n"]
        for v in violations:
            icon = "\U0001f534" if v.severity == "critical" else "\U0001f7e1"
            lines.append(
                f"{icon} **`{v.file}` Line {v.line}** — STD-{v.standard_id} {v.standard_title}\n"
                f"> {v.rule}\n```\n{v.code}\n```\n{v.explanation}\n"
            )
        try:
            resp = requests.post(
                f"https://api.github.com/repos/{repo}/issues/{pr_number}/comments",
                headers=loader.auth.headers(),
                json={"body": "\n".join(lines)},
                timeout=15,
            )
            resp.raise_for_status()
            print(f"[compliance] Violations posted to PR #{pr_number}")
        except Exception as e:
            print(f"[compliance] Failed to post: {e}")

    # ── Internal ──────────────────────────────────────────

    def _find_relevant_standards(self, pf, col) -> list[Standard]:
        changed = [
            line[1:] for line in getattr(pf, "patch", "").splitlines()
            if line.startswith("+") and not line.startswith("+++")
        ]
        if not changed:
            return []

        query = (
            f"File: {pf.filename}\n"
            f"Language: {getattr(pf, 'language', 'unknown')}\n"
            f"Changed code:\n" + "\n".join(changed[:30])
        )
        q_vec = self._embed_text(query)
        results = col.query(
            query_embeddings=[q_vec],
            n_results=min(3, col.count()),
            include=["metadatas", "distances"],
        )

        relevant = []
        for i, meta in enumerate(results["metadatas"][0]):
            score = max(0.0, 1.0 - results["distances"][0][i])
            if score < 0.25:
                continue
            std_id = meta.get("standard_id", "")
            for std in self._standards:
                if std.id == std_id:
                    relevant.append(std)
                    break
        return relevant

    def _check_violations(self, pf, relevant: list[Standard]) -> list[ComplianceViolation]:
        rules_text = ""
        for std in relevant:
            rules_text += f"\nSTD-{std.id}: {std.title}\nRules:\n"
            for rule in std.rules:
                rules_text += f"  - {rule}\n"

        changed_lines = [
            (i, line[1:])
            for i, line in enumerate(getattr(pf, "patch", "").splitlines())
            if line.startswith("+") and not line.startswith("+++")
        ]
        if not changed_lines:
            return []

        changed_text = "\n".join(f"L{i}: {code}" for i, code in changed_lines[:50])

        prompt = f"""You are an internal compliance checker.

INTERNAL STANDARDS (MUST be followed):
{rules_text}

CHANGED CODE IN PR:
File: {pf.filename}
{changed_text}

Check if the changed code VIOLATES any of the standards above.
Only flag CLEAR violations visible in the code — not potential or hypothetical ones.

Return ONLY valid JSON:
{{
  "violations": [
    {{
      "standard_id": "<STD id number>",
      "standard_title": "<standard title>",
      "rule":        "<exact rule text that is violated>",
      "line":        <line number>,
      "code":        "<the offending code snippet>",
      "severity":    "critical|warning",
      "explanation": "<why this violates the standard>",
      "fix":         "<concrete replacement code>"
    }}
  ]
}}

If no violations found, return: {{"violations": []}}"""

        try:
            from agents.llm_client import chat_completion
            text = chat_completion(
                system="You check code against internal compliance standards. Return JSON only.",
                user=prompt,
                temperature=0,
                max_tokens=1024,
            ).strip()
            text = re.sub(r'```[a-z]*\n?', '', text).strip('`').strip()
            data = json.loads(text)

            return [
                ComplianceViolation(
                    standard_id=v.get("standard_id", "?"),
                    standard_title=v.get("standard_title", "?"),
                    rule=v.get("rule", ""),
                    file=pf.filename,
                    line=int(v.get("line", 0)),
                    code=v.get("code", ""),
                    severity=v.get("severity", "warning"),
                    explanation=v.get("explanation", ""),
                    fix=v.get("fix", ""),
                )
                for v in data.get("violations", [])
            ]
        except Exception as e:
            print(f"[compliance] LLM check failed: {e}")
            return []

    # ── ChromaDB + Embeddings ─────────────────────────────

    def _get_collection(self):
        if self._col is not None:
            return self._col
        try:
            import chromadb
            client = chromadb.PersistentClient(path=cfg.chroma_dir)
            self._col = client.get_or_create_collection(
                name=STANDARDS_COLLECTION,
                metadata={"hnsw:space": "cosine"},
            )
            print(f"[compliance] Collection '{STANDARDS_COLLECTION}' ({self._col.count()} standards)")
            return self._col
        except Exception as e:
            raise RuntimeError(f"ChromaDB failed: {e}")

    def _embed_text(self, text: str) -> list[float]:
        if self._embed is None:
            from sentence_transformers import SentenceTransformer
            self._embed = SentenceTransformer("all-MiniLM-L6-v2")
        return self._embed.encode(
            [text[:4000]], normalize_embeddings=True, show_progress_bar=False
        ).tolist()[0]


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Internal Compliance Checking")
    parser.add_argument("--index", action="store_true", help="Index standards docs into ChromaDB")
    parser.add_argument("--check", action="store_true", help="Check a PR for compliance violations")
    parser.add_argument("--create-sample-standards", action="store_true",
                         help="Create sample standards docs in docs/standards/")
    parser.add_argument("--standards-dir", default="./docs/standards")
    parser.add_argument("--repo", default="")
    parser.add_argument("--pr", type=int, default=0)
    args = parser.parse_args()

    guard = ComplianceGuard(standards_dir=args.standards_dir)

    if args.create_sample_standards:
        guard.parser.create_sample_standards(Path(args.standards_dir))
    elif args.index:
        n = guard.index_standards()
        print(f"\n✅ Indexed {n} standards docs")
    elif args.check:
        if not args.repo or not args.pr:
            print("Usage: --check --repo owner/repo --pr 3")
            return
        from ingestion.github_loader import GitHubLoader
        loader = GitHubLoader()
        pr_ctx = loader.load_pr(args.repo, args.pr)
        violations = guard.check_pr(pr_ctx.files)
        if violations:
            print(f"\n⚠️  {len(violations)} compliance violations:")
            for v in violations:
                print(f"  [{v.severity.upper()}] STD-{v.standard_id}: {v.rule}")
                print(f"    {v.file}:L{v.line} — {v.code[:60]}")
        else:
            print("\n✅ No compliance violations detected")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
