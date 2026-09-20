"""Generate artifact bytes (txt, pdf) for durable storage + channel delivery."""

from __future__ import annotations

from typing import Any


def generate_txt(title: str, content: str) -> tuple[bytes, str]:
    body = f"{title}\n{'=' * min(40, len(title))}\n\n{content}\n"
    return body.encode("utf-8"), "text/plain; charset=utf-8"


def generate_pdf(title: str, content: str) -> tuple[bytes, str]:
    """Minimal PDF without external fonts. Pure Python stdlib-style via reportlab if present."""
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
        from reportlab.lib.units import mm
        from io import BytesIO

        buf = BytesIO()
        doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm)
        styles = getSampleStyleSheet()
        story = [
            Paragraph(title.replace("&", "&amp;").replace("<", "&lt;"), styles["Title"]),
            Spacer(1, 8 * mm),
        ]
        for para in (content or "").split("\n"):
            safe = (
                para.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
            )
            story.append(Paragraph(safe or " ", styles["Normal"]))
            story.append(Spacer(1, 2 * mm))
        doc.build(story)
        return buf.getvalue(), "application/pdf"
    except Exception:
        # Fallback: minimal valid-ish PDF with plain text stream
        text = f"{title}\n\n{content}".replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        # Escape non-latin roughly
        text = "".join(c if ord(c) < 128 else "?" for c in text)
        stream = f"BT /F1 12 Tf 50 750 Td 14 TL ({text[:2000]}) Tj ET"
        objects = []
        objects.append("1 0 obj<< /Type /Catalog /Pages 2 0 R >>endobj\n")
        objects.append("2 0 obj<< /Type /Pages /Kids [3 0 R] /Count 1 >>endobj\n")
        objects.append(
            "3 0 obj<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            "/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>endobj\n"
        )
        objects.append(
            f"4 0 obj<< /Length {len(stream)} >>stream\n{stream}\nendstream\nendobj\n"
        )
        objects.append("5 0 obj<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>endobj\n")
        out = ["%PDF-1.4\n"]
        offsets = [0]
        for obj in objects:
            offsets.append(sum(len(x) for x in out))
            out.append(obj)
        xref_pos = sum(len(x) for x in out)
        out.append(f"xref\n0 {len(offsets)}\n")
        out.append("0000000000 65535 f \n")
        for off in offsets[1:]:
            out.append(f"{off:010d} 00000 n \n")
        out.append(f"trailer<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n")
        return "".join(out).encode("latin-1", errors="replace"), "application/pdf"


def generate_bytes(fmt: str, title: str, content: str) -> tuple[bytes, str, str]:
    fmt = (fmt or "txt").lower().strip()
    if fmt in ("pdf", "application/pdf"):
        data, ctype = generate_pdf(title, content)
        return data, ctype, "pdf"
    data, ctype = generate_txt(title, content)
    return data, ctype, "txt"
