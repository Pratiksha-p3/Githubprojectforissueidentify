"""
agents/copilot_agent.py

Phase 8: AI Engineering Copilot

Answers questions about:
  - Code in the repo (via RAG)
  - Security findings across all PRs
  - How to fix specific issues
  - Generate test cases for a file
  - Generate documentation
  - Suggest refactoring
  - Explain code

Used by the dashboard's /api/copilot endpoint.
"""
from __future__ import annotations

import json
from pathlib import Path

from config import cfg

REPORTS_DIR = Path("./reports")


COPILOT_SYSTEM = """\
You are an AI Engineering Copilot for a code review system.

You have access to:
1. All PR review reports (findings, scores, security issues)
2. Code from the indexed repository (via RAG)

You can help with:
- Explaining security findings ("explain the SQL injection on line 15")
- Generating test cases ("generate tests for the login function")
- Generating documentation ("write docstrings for hash_password")
- Suggesting refactoring ("how should I refactor the auth module")
- Trend analysis ("which files have the most security issues")
- Fix explanations ("how do I fix the hardcoded secret")
- Code explanations ("what does generate_token() do")

Be specific, practical, and cite actual code/findings when available.
Format your answers clearly with code blocks where appropriate.
"""


class GitHubCopilotAgent:

    def __init__(self):
        self._client = None

    def ask(self, question: str) -> str:
        """
        Answer a question using:
        1. Recent PR reports as context
        2. RAG search of the codebase
        """
        # Build context from reports + RAG
        context = self._build_context(question)

        prompt = f"""CONTEXT FROM YOUR CODEBASE AND REVIEWS:
{context}

QUESTION: {question}

Answer based on the actual code and findings above.
Be specific and practical. Include code examples where helpful.
"""
        try:
            client = self._get_groq()
            resp   = client.chat.completions.create(
                model       = cfg.review_model,
                temperature = 0.2,
                max_tokens  = 1500,
                messages    = [
                    {"role": "system", "content": COPILOT_SYSTEM},
                    {"role": "user",   "content": prompt},
                ],
            )
            return resp.choices[0].message.content.strip()
        except Exception as e:
            if "429" in str(e) or "rate_limit" in str(e):
                return (
                    "⚠️ Groq rate limit reached (free tier: 100k tokens/day). "
                    "Please try again in a few minutes."
                )
            return f"Error: {e}"

    def _build_context(self, question: str) -> str:
        parts = []

        # 1. Load recent findings from reports
        reports = self._load_recent_reports(limit=5)
        if reports:
            parts.append("=== RECENT PR REVIEW FINDINGS ===")
            for r in reports:
                parts.append(
                    f"PR #{r.get('pr_number','?')} in {r.get('repo','?')} "
                    f"— score: {r.get('overall_score','?')}"
                )
                for f in r.get("findings", [])[:5]:
                    parts.append(
                        f"  [{f.get('severity','?').upper()}] "
                        f"{f.get('file','?')}:L{f.get('line',0)} "
                        f"— {f.get('message','')[:100]}"
                    )
            parts.append("")

        # 2. RAG search of codebase
        rag_context = self._rag_search(question)
        if rag_context:
            parts.append("=== RELEVANT CODE FROM REPOSITORY ===")
            parts.append(rag_context)

        return "\n".join(parts) if parts else "No context available yet."

    def _rag_search(self, question: str) -> str:
        try:
            from embeddings.embed import Embedder
            import chromadb
            embedder = Embedder()
            client   = chromadb.PersistentClient(path=cfg.chroma_dir)

            # Try repo index first, then PR review collection
            for col_name in ("repo_index", "code_review"):
                try:
                    col = client.get_collection(col_name)
                    if col.count() == 0:
                        continue

                    q_vec = embedder.embed_query(question)
                    results = col.query(
                        query_embeddings = [q_vec],
                        n_results        = min(4, col.count()),
                        include          = ["documents", "metadatas", "distances"],
                    )

                    chunks = []
                    for i in range(len(results["ids"][0])):
                        score = max(0.0, 1.0 - results["distances"][0][i])
                        if score < 0.2:
                            continue
                        meta = results["metadatas"][0][i]
                        doc  = results["documents"][0][i]
                        chunks.append(
                            f"--- {meta.get('filename','?')} "
                            f"L{meta.get('start_line',0)}-{meta.get('end_line',0)} "
                            f"(relevance: {score:.2f}) ---\n"
                            f"{doc[:600]}"
                        )

                    if chunks:
                        return "\n\n".join(chunks)

                except Exception:
                    continue

        except Exception as e:
            print(f"[copilot] RAG search failed: {e}")

        return ""

    def _load_recent_reports(self, limit: int = 5) -> list[dict]:
        if not REPORTS_DIR.exists():
            return []
        reports = []
        for f in sorted(REPORTS_DIR.glob("*.json"), reverse=True)[:limit]:
            try:
                reports.append(json.loads(f.read_text(encoding="utf-8")))
            except Exception:
                pass
        return reports

    def _get_groq(self):
        if self._client is None:
            from groq import Groq
            self._client = Groq(api_key=cfg.groq_api_key)
        return self._client