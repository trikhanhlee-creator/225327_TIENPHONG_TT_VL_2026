"""Extract Word document structure for LLM form understanding."""

from __future__ import annotations

import os
import re


def _normalize_line(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def extract_structured_document(file_path: str) -> str:
    """
    Build a human/LLM-readable representation of a .docx (or converted .doc)
    preserving paragraph order and table grids.
    """
    ext = os.path.splitext(file_path or "")[1].lower()
    if ext not in (".docx", ".doc") or not os.path.exists(file_path):
        return ""

    docx_path = file_path
    if ext == ".doc":
        try:
            from app.services.doc_converter import ensure_docx_for_processing

            docx_path = ensure_docx_for_processing(file_path)
        except Exception:
            return ""

    try:
        from docx import Document
        from docx.oxml.ns import qn
        from docx.table import Table
        from docx.text.paragraph import Paragraph
    except ImportError:
        return ""

    try:
        doc = Document(docx_path)
    except Exception:
        return ""

    parts: list[str] = []
    table_index = 0

    for child in doc.element.body.iterchildren():
        if child.tag == qn("w:p"):
            text = _normalize_line(Paragraph(child, doc).text)
            if text:
                parts.append(text)
            continue

        if child.tag != qn("w:tbl"):
            continue

        table_index += 1
        table = Table(child, doc)
        parts.append(f"\n=== BẢNG {table_index} ({len(table.rows)} dòng) ===")

        for row_idx, row in enumerate(table.rows):
            cells = [_normalize_line(cell.text) for cell in row.cells]
            if not any(cells):
                continue
            parts.append(f"  Hàng {row_idx + 1}: " + " | ".join(cells))

    return "\n".join(parts).strip()
