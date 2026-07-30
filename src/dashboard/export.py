"""
src/dashboard/export.py

Exports a repo's review history as JSON (structured data for
programmatic consumption / compliance audit) or PDF (a human-readable
report) — the "export reports for compliance/audit purposes" functional
requirement.
"""
from __future__ import annotations

import io
import json


def export_json(repo: str, history: list[dict], risk_score: dict) -> str:
    return json.dumps(
        {"repo": repo, "risk_score": risk_score, "history": history},
        indent=2,
        default=str,
    )


def export_pdf(repo: str, history: list[dict], risk_score: dict) -> bytes:
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    buffer = io.BytesIO()
    doc = canvas.Canvas(buffer, pagesize=letter)
    width, height = letter

    y = height - 50
    doc.setFont("Helvetica-Bold", 16)
    doc.drawString(50, y, f"Code Review Report: {repo}")
    y -= 30

    doc.setFont("Helvetica", 12)
    doc.drawString(50, y, f"Risk score: {risk_score.get('score')} ({risk_score.get('trend')})")
    y -= 20
    doc.drawString(50, y, f"Reviews considered: {risk_score.get('reviews_considered')}")
    y -= 40

    doc.setFont("Helvetica-Bold", 12)
    doc.drawString(50, y, "Recent reviews:")
    y -= 20
    doc.setFont("Helvetica", 10)

    for row in history[:20]:
        if y < 50:
            doc.showPage()
            y = height - 50
            doc.setFont("Helvetica", 10)
        line = (
            f"{row['reviewed_at']} | {str(row['commit_sha'])[:8]} | {row['status']} | "
            f"critical={row['critical_count']} total={row['total_findings']}"
        )
        doc.drawString(50, y, line)
        y -= 15

    doc.save()
    return buffer.getvalue()
