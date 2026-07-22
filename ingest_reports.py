import json
from pathlib import Path

import chromadb
from sentence_transformers import SentenceTransformer

REPORTS_DIR = Path("reports")

client = chromadb.PersistentClient(path="./vector_db")

collection = client.get_or_create_collection(
    name="github_reviews"
)

embedding_model = SentenceTransformer(
    "all-MiniLM-L6-v2"
)

for file in REPORTS_DIR.glob("*.json"):

    report = json.loads(
        file.read_text(encoding="utf-8")
    )

    findings = "\n".join(
        [
            f"{f.get('severity')} - {f.get('message')}"
            for f in report.get("findings", [])
        ]
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

    embedding = embedding_model.encode(
        document
    ).tolist()

    collection.add(
        ids=[file.stem],
        documents=[document],
        embeddings=[embedding]
    )

print("Reports indexed successfully")