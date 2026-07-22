"""
tools/architecture_guard.py

Tier 3 — Feature 9: Architecture Drift Detection

Parses Architecture Decision Records (ADRs) and detects when
a PR violates documented architectural decisions.

Examples of drift it catches:
  - "We agreed: no direct DB calls from controllers"
    → PR adds db.execute() in views.py → FLAGGED
  
  - "We agreed: use Redis for all caching, not in-memory"
    → PR adds {} dict as cache → FLAGGED
  
  - "We agreed: all external API calls go through api_client.py"
    → PR adds requests.get() directly in service → FLAGGED

  - "We agreed: passwords must use bcrypt"
    → PR uses hashlib.md5 → FLAGGED

How it works:
  1. Reads all ADR markdown files from docs/adr/ folder
  2. Embeds them into ChromaDB (separate collection)
  3. For each PR change, finds relevant ADRs via semantic search
  4. Asks LLM: "Does this code violate any of these decisions?"
  5. Posts violations as PR comments

Usage:
  # Index your ADR docs first (run once)
  python tools/architecture_guard.py --index --adr-dir docs/adr/

  # Check a PR against ADRs
  python tools/architecture_guard.py --check --pr 3 --repo owner/repo

  # Create sample ADR template
  python tools/architecture_guard.py --create-sample-adrs

Install:
  pip install sentence-transformers chromadb
"""
from __future__ import annotations

import json
import os
import re
import requests
from dataclasses import dataclass, field
from pathlib import Path
from config import cfg


ADR_COLLECTION = "architecture_decisions"
ADR_DIR        = Path("./docs/adr")


@dataclass
class ADR:
    """A single Architecture Decision Record."""
    id:          str
    title:       str
    status:      str        # accepted | deprecated | superseded
    context:     str        # why this decision was needed
    decision:    str        # what was decided
    consequences: str       # what this means going forward
    filename:    str
    rules:       list[str] = field(default_factory=list)   # extracted rules


@dataclass
class DriftViolation:
    """A detected violation of an architectural decision."""
    adr_id:      str
    adr_title:   str
    rule:        str        # the specific rule violated
    file:        str
    line:        int
    code:        str        # the offending code
    severity:    str        # critical | warning
    explanation: str
    fix:         str


class ADRParser:
    """Parses ADR markdown files into structured ADR objects."""

    def parse_directory(self, adr_dir: str | Path) -> list[ADR]:
        """Parse all markdown files in an ADR directory."""
        adr_path = Path(adr_dir)
        if not adr_path.exists():
            print(f"[arch-guard] ADR directory not found: {adr_path}")
            print(f"[arch-guard] Creating sample ADRs...")
            self.create_sample_adrs(adr_path)

        adrs = []
        for md_file in sorted(adr_path.glob("*.md")):
            adr = self.parse_file(md_file)
            if adr and adr.status == "accepted":
                adrs.append(adr)
                print(f"  [arch-guard] Loaded ADR: {adr.id} — {adr.title}")

        print(f"[arch-guard] Loaded {len(adrs)} active ADRs")
        return adrs

    def parse_file(self, filepath: Path) -> ADR | None:
        """Parse a single ADR markdown file."""
        try:
            content = filepath.read_text(encoding="utf-8")
            return self._parse_markdown(content, filepath.name)
        except Exception as e:
            print(f"[arch-guard] Could not parse {filepath}: {e}")
            return None

    def _parse_markdown(self, content: str, filename: str) -> ADR:
        """Extract structured data from ADR markdown."""

        def extract_section(name: str) -> str:
            pattern = rf"##\s+{name}\s*\n(.*?)(?=\n##|\Z)"
            m = re.search(pattern, content, re.DOTALL | re.IGNORECASE)
            return m.group(1).strip() if m else ""

        # Extract ADR ID and title from filename or first heading
        adr_id = re.match(r"(\d+)", filename)
        adr_id = adr_id.group(1) if adr_id else filename

        title_m = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
        title   = title_m.group(1).strip() if title_m else filename

        # Extract status
        status_m = re.search(r"(?:Status|status):\s*(\w+)", content)
        status   = status_m.group(1).lower() if status_m else "accepted"

        decision = extract_section("Decision")
        context  = extract_section("Context")
        conseq   = extract_section("Consequences")

        # Extract concrete rules from decision section
        rules = self._extract_rules(decision)

        return ADR(
            id           = adr_id,
            title        = title.replace("# ", "").strip(),
            status       = status,
            context      = context,
            decision     = decision,
            consequences = conseq,
            filename     = filename,
            rules        = rules,
        )

    def _extract_rules(self, decision_text: str) -> list[str]:
        """
        Extract concrete, checkable rules from the decision text.
        Looks for bullet points, numbered lists, and MUST/MUST NOT statements.
        """
        rules = []

        # Bullet points
        for m in re.finditer(r"^[\*\-]\s+(.+)$", decision_text, re.MULTILINE):
            rule = m.group(1).strip()
            if len(rule) > 20:
                rules.append(rule)

        # Numbered lists
        for m in re.finditer(r"^\d+\.\s+(.+)$", decision_text, re.MULTILINE):
            rule = m.group(1).strip()
            if len(rule) > 20:
                rules.append(rule)

        # MUST / MUST NOT / SHALL / SHOULD statements
        for m in re.finditer(
            r"(?:MUST|MUST NOT|SHALL|SHOULD NOT|NEVER|ALWAYS)\s+.+[.!]",
            decision_text,
            re.IGNORECASE,
        ):
            rules.append(m.group(0).strip())

        return rules[:10]  # cap at 10 rules per ADR

    def create_sample_adrs(self, adr_dir: Path) -> None:
        """Create sample ADR files to get started."""
        adr_dir.mkdir(parents=True, exist_ok=True)

        sample_adrs = [
            {
                "filename": "001-no-direct-db-in-controllers.md",
                "content":  """# ADR-001: No Direct Database Calls in Controllers

Status: accepted
Date: 2024-01-15

## Context

Controllers are handling HTTP request/response lifecycle.
Mixing database logic into controllers creates tight coupling,
makes testing hard, and violates separation of concerns.

## Decision

- Controllers MUST NOT contain direct database queries
- All database access MUST go through the service layer
- Service classes MUST be in the `services/` directory
- NEVER call `db.execute()`, `conn.cursor()`, or ORM queries directly in views/controllers

## Consequences

- All controllers import from services/, never from models/ directly
- Database migrations do not affect controller logic
- Controllers are testable without a real database
""",
            },
            {
                "filename": "002-use-bcrypt-for-passwords.md",
                "content":  """# ADR-002: Use bcrypt for Password Hashing

Status: accepted
Date: 2024-01-15

## Context

MD5 and SHA1 are cryptographically broken for password storage.
We need a consistent, secure approach across the codebase.

## Decision

- ALL password hashing MUST use bcrypt
- NEVER use MD5, SHA1, or SHA256 for password storage
- NEVER store passwords in plaintext
- Use `bcrypt.hashpw(password.encode(), bcrypt.gensalt())`
- Minimum work factor: 12

## Consequences

- All authentication code uses bcrypt
- Password comparison uses bcrypt.checkpw()
- hashlib MUST NOT be used for passwords
""",
            },
            {
                "filename": "003-parameterized-queries-only.md",
                "content":  """# ADR-003: Parameterized Queries Only

Status: accepted
Date: 2024-01-15

## Context

SQL injection is the #1 web vulnerability.
String formatting in SQL queries is never acceptable.

## Decision

- ALL database queries MUST use parameterized queries
- NEVER use f-strings, .format(), or % in SQL strings
- Use cursor.execute(query, (params,)) always
- ORM queries are acceptable and preferred

## Consequences

- No f-string SQL anywhere in the codebase
- Code review rejects any string-formatted SQL
""",
            },
            {
                "filename": "004-environment-variables-for-secrets.md",
                "content":  """# ADR-004: Environment Variables for All Secrets

Status: accepted
Date: 2024-01-15

## Context

Hardcoded secrets in code get committed to version control
and can be exposed in logs, errors, and history.

## Decision

- ALL secrets MUST be stored in environment variables
- NEVER hardcode API keys, passwords, tokens, or connection strings
- Use os.getenv() or python-dotenv
- Secret names MUST be in UPPER_SNAKE_CASE
- NEVER commit .env files to version control

## Consequences

- All secrets loaded via os.getenv() at startup
- .env is in .gitignore
- CI/CD uses secret management (GitHub Secrets, Vault)
""",
            },
            {
                "filename": "005-error-handling-policy.md",
                "content":  """# ADR-005: Error Handling Policy

Status: accepted
Date: 2024-01-15

## Context

Bare except clauses hide bugs and make debugging impossible.
We need consistent error handling across the codebase.

## Decision

- NEVER use bare `except:` clauses
- ALWAYS catch specific exception types
- ALWAYS log exceptions with context
- NEVER swallow exceptions silently
- Use `except Exception as e: logger.error(f"Context: {e}")`

## Consequences

- All error handling is explicit and logged
- Bugs surface immediately rather than being hidden
""",
            },
        ]

        for adr in sample_adrs:
            path = adr_dir / adr["filename"]
            path.write_text(adr["content"], encoding="utf-8")
            print(f"  [arch-guard] Created sample ADR: {path}")

        print(f"[arch-guard] Created {len(sample_adrs)} sample ADRs in {adr_dir}")


class ArchitectureGuard:
    """
    Main architecture drift detection engine.

    1. Indexes ADRs into ChromaDB
    2. For each PR file, finds relevant ADRs via semantic search
    3. Asks LLM to check for violations
    4. Returns structured violations
    """

    def __init__(self, adr_dir: str = "./docs/adr"):
        self.adr_dir = Path(adr_dir)
        self.parser  = ADRParser()
        self._col    = None
        self._embed  = None
        self._groq   = None
        self._adrs:  list[ADR] = []

    # ── Public API ────────────────────────────────────────

    def index_adrs(self) -> int:
        """Load and embed all ADRs into ChromaDB. Run once."""
        self._adrs = self.parser.parse_directory(self.adr_dir)
        if not self._adrs:
            return 0

        col = self._get_collection()

        for adr in self._adrs:
            # Build searchable text combining all sections
            text = (
                f"ADR-{adr.id}: {adr.title}\n\n"
                f"Context: {adr.context}\n\n"
                f"Decision: {adr.decision}\n\n"
                f"Rules:\n" + "\n".join(adr.rules)
            )

            vec = self._embed_text(text)

            col.upsert(
                ids        = [f"adr-{adr.id}"],
                embeddings = [vec],
                documents  = [text],
                metadatas  = [{
                    "adr_id":   adr.id,
                    "title":    adr.title,
                    "status":   adr.status,
                    "filename": adr.filename,
                    "rules":    json.dumps(adr.rules),
                }],
            )

        print(f"[arch-guard] Indexed {len(self._adrs)} ADRs into ChromaDB")
        return len(self._adrs)

    def check_pr(self, pr_files, repo: str = "", pr_number: int = 0) -> list[DriftViolation]:
        """
        Check all PR files against architectural decisions.
        Returns list of violations found.
        """
        col = self._get_collection()
        if col.count() == 0:
            print("[arch-guard] No ADRs indexed. Run --index first.")
            self.index_adrs()
            col = self._get_collection()

        # Load ADRs if not already loaded
        if not self._adrs:
            self._adrs = self.parser.parse_directory(self.adr_dir)

        all_violations = []

        for pf in pr_files:
            if not getattr(pf, "patch", ""):
                continue

            print(f"[arch-guard] Checking {pf.filename}...")

            # Find relevant ADRs for this file via semantic search
            relevant_adrs = self._find_relevant_adrs(pf, col)

            if not relevant_adrs:
                continue

            # Check for violations using LLM
            violations = self._check_violations(pf, relevant_adrs)
            all_violations.extend(violations)

        if all_violations:
            print(f"[arch-guard] ⚠️  {len(all_violations)} architecture violations found")
        else:
            print(f"[arch-guard] ✅ No architecture drift detected")

        return all_violations
    
    def scan(self, files):
        """
        Compatibility wrapper for app.py.
        Returns findings in standard reviewer format.
        """
        violations = self.check_pr(files)

        return self.violations_to_findings(
            violations
        )


    def violations_to_findings(self, violations: list[DriftViolation]) -> list[dict]:
        """Convert violations to standard finding format for the report."""
        return [
            {
                "file":     v.file,
                "line":     v.line,
                "severity": v.severity,
                "category": "architecture",
                "message":  (
                    f"[ADR-{v.adr_id}] {v.adr_title}: {v.rule}\n"
                    f"Offending code: `{v.code}`\n{v.explanation}"
                ),
                "fix":      v.fix,
                "source":   "architecture_guard",
                "adr_id":   v.adr_id,
            }
            for v in violations
        ]

    def format_pr_comment(self, violations: list[DriftViolation]) -> str:
        """Format violations as a GitHub PR comment."""
        if not violations:
            return ""

        lines = [
            "## 🏗️ Architecture Drift Detected\n",
            f"**{len(violations)} violation(s)** of documented architectural decisions "
            f"found in this PR.\n",
            "> These decisions were made to maintain consistency and quality. "
            "Please address them before merging.\n",
        ]

        # Group by ADR
        by_adr: dict[str, list[DriftViolation]] = {}
        for v in violations:
            by_adr.setdefault(v.adr_id, []).append(v)

        for adr_id, viols in by_adr.items():
            adr_title = viols[0].adr_title
            lines.append(f"\n### 📋 ADR-{adr_id}: {adr_title}\n")

            for v in viols:
                icon = "🔴" if v.severity == "critical" else "🟡"
                lines.append(f"{icon} **`{v.file}` Line {v.line}**")
                lines.append(f"\n**Rule violated:** {v.rule}\n")
                lines.append(f"**Code:**\n```python\n{v.code}\n```\n")
                lines.append(f"**Why:** {v.explanation}\n")
                lines.append(f"**Fix:**\n```python\n{v.fix}\n```\n")
                lines.append("---")

        lines.append(
            f"\n📚 See `{self.adr_dir}/` for full architectural decision records.\n"
            "*🤖 AI Code Review — Architecture Guard*"
        )
        return "\n".join(lines)

    def post_to_pr(
        self,
        violations: list[DriftViolation],
        repo:       str,
        pr_number:  int,
        head_sha:   str,
        loader,
    ) -> None:
        """Post architecture violations to GitHub PR."""
        if not violations:
            return

        comment = self.format_pr_comment(violations)

        try:
            resp = requests.post(
            f"https://api.github.com/repos/{repo}/issues/{pr_number}/comments",
            headers=loader.auth.headers(),
            json={"body": comment},
            timeout=15,
        )

            resp.raise_for_status()
            print(f"[arch-guard] Violations posted to PR #{pr_number}")
        except Exception as e:
            print(f"[arch-guard] Failed to post: {e}")

        # Set commit status
        state = "failure" if any(v.severity == "critical" for v in violations) else "pending"
        try:
            requests.post(
                f"https://api.github.com/repos/{repo}/statuses/{head_sha}",
                headers = loader.auth.headers(),
                json    = {
                    "state":       state,
                    "description": f"{len(violations)} architecture violation(s) detected",
                    "context":     "ai-code-review/architecture",
                },
                timeout = 15,
            )
        except Exception as e:
            print(
            f"[arch-guard] "
            f"Failed to update commit status: {e}"
        )

    # ── Internal ──────────────────────────────────────────

    def _find_relevant_adrs(self, pf, col) -> list[ADR]:
        """Find ADRs most relevant to this file's changes using semantic search."""
        # Build query from changed lines
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
            query_embeddings = [q_vec],
            n_results        = min(3, col.count()),
            include          = ["metadatas", "distances"],
        )

        relevant = []
        for i, meta in enumerate(results["metadatas"][0]):
            score = max(0.0, 1.0 - results["distances"][0][i])
            if score < 0.25:
                continue
            # Find matching ADR object
            adr_id = meta.get("adr_id", "")
            for adr in self._adrs:
                if adr.id == adr_id:
                    relevant.append(adr)
                    break

        return relevant

    def _check_violations(
        self,
        pf,
        relevant_adrs: list[ADR],
    ) -> list[DriftViolation]:
        """Ask LLM to check if the changed code violates any ADR rules."""

        # Build ADR rules context
        rules_text = ""
        for adr in relevant_adrs:
            rules_text += f"\nADR-{adr.id}: {adr.title}\n"
            rules_text += f"Decision: {adr.decision[:500]}\n"
            rules_text += "Rules:\n"
            for rule in adr.rules:
                rules_text += f"  - {rule}\n"

        changed_lines = [
            (i, line[1:])
            for i, line in enumerate(getattr(pf, "patch", "").splitlines())
            if line.startswith("+") and not line.startswith("+++")
        ]
        if not changed_lines:
            return []

        changed_text = "\n".join(
            f"L{pf.filename}:{i}: {code}"
            for i, code in changed_lines[:50]
        )

        prompt = f"""You are an architecture compliance checker.

ARCHITECTURAL DECISION RECORDS (MUST be followed):
{rules_text}

CHANGED CODE IN PR:
File: {pf.filename}
{changed_text}

Check if the changed code VIOLATES any of the ADR rules above.
Only flag CLEAR violations visible in the code — not potential or hypothetical ones.

Return ONLY valid JSON:
{{
  "violations": [
    {{
      "adr_id":      "<ADR id number>",
      "adr_title":   "<ADR title>",
      "rule":        "<exact rule text that is violated>",
      "line":        <line number>,
      "code":        "<the offending code snippet>",
      "severity":    "critical|warning",
      "explanation": "<why this violates the ADR>",
      "fix":         "<concrete replacement code>"
    }}
  ]
}}

If no violations found, return: {{"violations": []}}"""

        try:
            client = self._get_groq()
            resp   = client.chat.completions.create(
                model       = cfg.review_model,
                temperature = 0,
                max_tokens  = 1024,
                messages    = [
                    {
                        "role":    "system",
                        "content": "You check code against architecture decisions. Return JSON only."
                    },
                    {"role": "user", "content": prompt},
                ],
            )
            text = resp.choices[0].message.content.strip()
            text = re.sub(r'```[a-z]*\n?', '', text).strip('`').strip()
            data = json.loads(text)

            violations = []
            for v in data.get("violations", []):
                violations.append(DriftViolation(
                    adr_id      = v.get("adr_id", "?"),
                    adr_title   = v.get("adr_title", "?"),
                    rule        = v.get("rule", ""),
                    file        = pf.filename,
                    line        = int(v.get("line", 0)),
                    code        = v.get("code", ""),
                    severity    = v.get("severity", "warning"),
                    explanation = v.get("explanation", ""),
                    fix         = v.get("fix", ""),
                ))

            return violations

        except Exception as e:
            print(f"[arch-guard] LLM check failed: {e}")
            return []

    # ── ChromaDB + Embeddings ─────────────────────────────

    def _get_collection(self):
        if self._col is not None:
            return self._col
        try:
            import chromadb
            client     = chromadb.PersistentClient(path=cfg.chroma_dir)
            self._col  = client.get_or_create_collection(
                name     = ADR_COLLECTION,
                metadata = {"hnsw:space": "cosine"},
            )
            print(f"[arch-guard] Collection '{ADR_COLLECTION}' ({self._col.count()} ADRs)")
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

    def _get_groq(self):
        if self._groq is None:
            from groq import Groq
            self._groq = Groq(api_key=cfg.groq_api_key)
        return self._groq


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Architecture Drift Detection")
    parser.add_argument("--index",            action="store_true",
                        help="Index ADR docs into ChromaDB")
    parser.add_argument("--check",            action="store_true",
                        help="Check a PR for architecture violations")
    parser.add_argument("--create-sample-adrs", action="store_true",
                        help="Create sample ADR files in docs/adr/")
    parser.add_argument("--adr-dir",          default="./docs/adr",
                        help="Directory containing ADR markdown files")
    parser.add_argument("--repo",             default="",
                        help="GitHub repo (owner/repo)")
    parser.add_argument("--pr",               type=int, default=0,
                        help="PR number to check")
    args = parser.parse_args()

    guard = ArchitectureGuard(adr_dir=args.adr_dir)

    if args.create_sample_adrs:
        guard.parser.create_sample_adrs(Path(args.adr_dir))

    elif args.index:
        n = guard.index_adrs()
        print(f"\n✅ Indexed {n} ADRs")
        print(f"   Next: python tools/architecture_guard.py --check --repo owner/repo --pr 1")

    elif args.check:
        if not args.repo or not args.pr:
            print("Usage: --check --repo owner/repo --pr 3")
            return
        from ingestion.github_loader import GitHubLoader
        loader = GitHubLoader()
        pr_ctx = loader.load_pr(args.repo, args.pr)
        violations = guard.check_pr(pr_ctx.files, args.repo, args.pr)

        if violations:
            print(f"\n⚠️  {len(violations)} architecture violations:")
            for v in violations:
                print(f"  [{v.severity.upper()}] ADR-{v.adr_id}: {v.rule}")
                print(f"    {v.file}:L{v.line} — {v.code[:60]}")
        else:
            print("\n✅ No architecture drift detected")

    else:
        parser.print_help()
        print("\nQuick start:")
        print("  1. python tools/architecture_guard.py --create-sample-adrs")
        print("  2. python tools/architecture_guard.py --index")
        print("  3. python tools/architecture_guard.py --check --repo owner/repo --pr 3")
        print("\n  Edit docs/adr/*.md to add your own architecture decisions.")


if __name__ == "__main__":
    main()