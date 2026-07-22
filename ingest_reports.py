# ingest_reports.py
#
# Embeds past PR review reports (reports/*.json) into a ChromaDB collection
# so they're searchable later (e.g. by ask_copilot in mcp_server.py).
#
# NOTE on the storage path: this intentionally uses a different Chroma
# store than github_chat.py / mcp_server.py's RAG pipeline. Those index
# your *source code* for chat (vectordb/chroma_data). This indexes your
# *past review reports* instead — different data, kept in its own
# collection so the two don't collide. Renamed the folder from the
# original "./vector_db" to "./vectordb/reports_chroma_data" so it sits
# clearly alongside the other vectordb/ data instead of looking like an
# accidental duplicate of vectordb/chroma_data.

from __future__ import annotations

import argparse
import json
from pathlib import Path


def ingest_reports(reports_dir: str = "reports", persist_dir: str = "./vectordb/reports_chroma_data") -> int:
    import chromadb
    from sentence_transformers import SentenceTransformer

    reports_path = Path(reports_dir)
    if not reports_path.exists():
        print(f"[ingest-reports] No reports directory found at {reports_path}")
        return 0

    client = chromadb.PersistentClient(path=persist_dir)
    collection = client.get_or_create_collection(name="github_reviews")
    embedding_model = SentenceTransformer("all-MiniLM-L6-v2")

    count = 0
    for file in reports_path.glob("*.json"):
        report = json.loads(file.read_text(encoding="utf-8"))

        findings = "\n".join(
            f"{f.get('severity')} - {f.get('message')}"
            for f in report.get("findings", [])
        )

        document = f"""
        Repository: {report.get('repo')}
        PR Number: {report.get('pr_number')}
        Title: {report.get('pr_title')}
        Score: {report.get('overall_score')}
        Approved: {report.get('approved')}

        Findings:
        {findings}
        """

        embedding = embedding_model.encode(document).tolist()

        collection.upsert(
            ids=[file.stem],
            documents=[document],
            embeddings=[embedding],
        )
        count += 1

    print(f"[ingest-reports] {count} reports indexed into {persist_dir}")
    return count


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Embed past PR review reports into ChromaDB"
    )
    parser.add_argument(
        "--reports-dir", default="reports",
        help="Directory containing review_*.json reports (default: reports)",
    )
    parser.add_argument(
        "--persist-dir", default="./vectordb/reports_chroma_data",
        help="ChromaDB persist directory (default: ./vectordb/reports_chroma_data)",
    )
    args = parser.parse_args()
    ingest_reports(reports_dir=args.reports_dir, persist_dir=args.persist_dir)


if __name__ == "__main__":
    main()