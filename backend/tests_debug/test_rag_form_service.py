"""Tests for RAG form helpers (no DB)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.services.autofill.contracts import CanonicalFormField, MemoryCandidate
from app.services.autofill.rag_form_service import RagFormService, extract_indexable_text


def test_extract_indexable_text_missing_file():
    assert extract_indexable_text("/nonexistent/file.docx") == ""


def test_merge_hints_into_template_fields():
    fields = [{"name": "full_name", "label": "Họ và tên"}]
    hints = {"full_name": {"value": "Nguyen Van A", "confidence": 0.9, "source": "memory"}}
    merged = RagFormService.merge_hints_into_template_fields(fields, hints)
    assert merged[0]["suggested_value"] == "Nguyen Van A"
    assert merged[0]["suggestion_confidence"] == 0.9


@patch.object(RagFormService, "_candidate_to_hint", return_value={"value": "a@b.com", "confidence": 0.8, "source": "rag"})
def test_enrich_data_map_fills_empty(mock_hint):
    service = RagFormService(retrieval_agent=MagicMock())
    service._retrieval.retrieve_for_field = MagicMock(return_value=[])
    db = MagicMock()
    data, count = service.enrich_data_map(
        db,
        user_id=1,
        template_fields=[{"name": "email", "label": "Email", "field_type": "email"}],
        data_map={"email": ""},
        only_empty=True,
    )
    assert count == 1
    assert data["email"] == "a@b.com"


def test_build_field_hints_skips_low_confidence():
    agent = MagicMock()
    agent.retrieve_for_field.return_value = [
        MemoryCandidate(
            field_key="phone",
            value="0901234567",
            memory_type="rag",
            score=0.5,
            confidence=0.2,
            metadata={"tier": 5},
        )
    ]
    service = RagFormService(retrieval_agent=agent)
    hints = service.build_field_hints(
        MagicMock(),
        user_id=1,
        fields=[CanonicalFormField(field_key="phone", label="SĐT", field_type="text")],
        min_confidence=0.45,
    )
    assert hints == {}
