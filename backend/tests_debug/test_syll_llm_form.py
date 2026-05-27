#!/usr/bin/env python3
"""Tests for Sơ yếu lý lịch intelligent Word form parsing."""

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.autofill.document_structure import extract_structured_document
from app.services.autofill.llm_word_form_service import (
    _fallback_syll_template_fields,
    _is_syll_template,
    enhance_word_template_fields,
)
from app.services.file_parser import FileParserFactory


SYLL_PATH = os.path.join(
    os.path.dirname(__file__),
    "..",
    "uploads",
    "1014_1779157611.717952_so-yeu-ly-lich.docx",
)


def test_structured_document_has_tables() -> None:
    assert os.path.exists(SYLL_PATH), f"Missing fixture: {SYLL_PATH}"
    text = extract_structured_document(SYLL_PATH)
    assert "=== BẢNG 1" in text
    assert "=== BẢNG 2" in text
    assert _is_syll_template(text)


def test_syll_template_fallback_field_count() -> None:
    fields = _fallback_syll_template_fields()
    assert len(fields) >= 55
    names = {field.name for field in fields}
    assert "gioi_tinh" in names
    assert "dao_tao_1_truong" in names
    assert "cong_tac_2_don_vi" in names
    assert "cha_ho_ten" in names


def test_enhance_syll_beats_heuristic_parser() -> None:
    assert os.path.exists(SYLL_PATH)
    parser = FileParserFactory.create_parser(SYLL_PATH)
    parser_fields = parser.parse()
    assert len(parser_fields) < 40

    # Fast path: syll template schema (no live LLM call in CI/dev)
    os.environ["WORD_LLM_SYLL_TRY_LLM"] = "false"
    enhanced, meta = asyncio.run(
        enhance_word_template_fields(
            file_path=SYLL_PATH,
            parser_fields=parser_fields,
            original_filename="so-yeu-ly-lich.docx",
        )
    )

    assert len(enhanced) >= 50, f"Expected >=50 fields, got {len(enhanced)} meta={meta}"
    assert meta.get("strategy") in {"syll_template", "syll_template_plus_llm", "llm", "llm_partial"}

    out_path = os.path.join(os.path.dirname(__file__), "_syll_enhanced.json")
    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump(
            {
                "meta": meta,
                "fields": [field.to_dict() for field in enhanced],
            },
            handle,
            ensure_ascii=False,
            indent=2,
        )


if __name__ == "__main__":
    test_structured_document_has_tables()
    test_syll_template_fallback_field_count()
    test_enhance_syll_beats_heuristic_parser()
    print("Syll LLM form tests passed.")
