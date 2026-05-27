"""Shared filters for autocomplete / RAG field-value suggestions."""
from __future__ import annotations

import re


def is_valid_field_suggestion_value(value: str, *, max_len: int = 80) -> bool:
    """
    Return True when `value` is appropriate to show as a single form-field suggestion.
    Rejects long paragraphs, technical noise, and multi-sentence document excerpts.
    """
    text = (value or "").strip()
    if not text:
        return False

    if re.match(r"^[a-z0-9]+_[0-9]{8,}$", text.lower()):
        return False
    if re.match(r"^\d{10,}$", text):
        return False
    if re.match(r"^[._\-\s]{3,}$", text):
        return False
    if text.lower() in {"n/a", "na", "null", "none", "khong", "không", "test", "unknown", "...", "-"}:
        return False
    if len(text) > max_len:
        return False

    # Paragraph / cover-letter excerpts indexed from uploaded templates (RAG).
    if text.count(".") >= 2 or text.count(",") >= 4:
        return False
    words = [w for w in re.split(r"\s+", text) if w]
    if len(words) > 10:
        return False

    return True
