"""Unit tests for legacy .doc conversion helpers."""

import os
from unittest.mock import patch

import pytest

from app.services.doc_converter import (
    DocConversionError,
    convert_doc_to_docx,
    ensure_docx_for_processing,
)


def test_ensure_docx_passthrough_for_docx(tmp_path):
    docx = tmp_path / "form.docx"
    docx.write_bytes(b"fake")
    assert ensure_docx_for_processing(str(docx)) == str(docx)


def test_convert_doc_to_docx_with_libreoffice(tmp_path):
    doc = tmp_path / "legacy.doc"
    doc.write_bytes(b"ole-content")
    expected = tmp_path / "legacy.docx"
    expected.write_bytes(b"docx")

    with patch("app.services.doc_converter._is_ole_doc", return_value=True):
        with patch(
            "app.services.doc_converter._convert_with_libreoffice",
            return_value=str(expected),
        ):
            out = convert_doc_to_docx(str(doc))

    assert out == str(expected)


def test_convert_doc_raises_when_all_backends_fail(tmp_path):
    doc = tmp_path / "legacy.doc"
    doc.write_bytes(b"ole-content")

    with patch("app.services.doc_converter._is_ole_doc", return_value=True):
        with patch(
            "app.services.doc_converter._convert_with_libreoffice",
            side_effect=DocConversionError("no libreoffice"),
        ):
            with patch(
                "app.services.doc_converter._convert_with_word_com",
                side_effect=DocConversionError("no word"),
            ):
                with patch(
                    "app.services.doc_converter._convert_with_plaintext_fallback",
                    side_effect=DocConversionError("no text"),
                ):
                    with pytest.raises(DocConversionError):
                        convert_doc_to_docx(str(doc))
