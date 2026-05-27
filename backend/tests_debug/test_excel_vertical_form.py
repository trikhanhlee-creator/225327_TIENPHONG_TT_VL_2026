"""Tests for vertical Excel form layout (STT | Tên trường | Giá trị cần điền)."""

from __future__ import annotations

import os
import sys
import tempfile

import pytest

BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

from app.services.autofill.excel_vertical_form import (  # noqa: E402
    detect_vertical_form_layout,
    parse_vertical_form_fields,
    try_parse_vertical_excel_rows,
)
from app.services.file_parser import FileParserFactory, XlsxParser  # noqa: E402


def _detect_type(label: str) -> str:
    lower = label.lower()
    if "ngày" in lower or "ngay" in lower:
        return "date"
    if "email" in lower:
        return "email"
    if "điện thoại" in lower or "dien thoai" in lower:
        return "phone"
    return "text"


def _clean_label(text: str) -> str:
    return (text or "").strip().rstrip(":")


def _sample_rows():
    return [
        ("Phiếu xác nhận sinh viên", None, None, None),
        (None, None, None, None),
        ("STT", "Tên trường", "Giá trị cần điền", "Ghi chú"),
        (1, "Họ và tên", None, None),
        (2, "Mã số sinh viên", None, None),
        (3, "Ngày sinh", None, None),
        (4, "Khoa", None, None),
        (5, "Ngành học", None, None),
        (6, "Mục đích xác nhận", None, None),
        (7, "Ngày làm đơn", None, None),
        (8, "Người làm đơn ký tên", None, None),
    ]


def test_detect_vertical_layout():
    layout = detect_vertical_form_layout(_sample_rows())
    assert layout is not None
    assert layout.label_col == 1
    assert layout.value_col == 2
    assert layout.header_row_index == 2
    assert "xac nhan" in layout.title.lower() or "Phiếu" in layout.title


def test_parse_vertical_fields_count():
    layout = detect_vertical_form_layout(_sample_rows())
    assert layout is not None
    fields = parse_vertical_form_fields(
        _sample_rows(),
        layout,
        detect_field_type=_detect_type,
        clean_field_label=_clean_label,
    )
    assert len(fields) == 8
    names = {f.name for f in fields}
    assert "ho_va_ten" in names
    assert "ma_so_sinh_vien" in names
    assert "ngay_sinh" in names


def test_try_parse_vertical_wrapper():
    fields, meta = try_parse_vertical_excel_rows(
        _sample_rows(),
        detect_field_type=_detect_type,
        clean_field_label=_clean_label,
    )
    assert len(fields) == 8
    assert meta.get("excel_layout") == "vertical_kv"
    assert meta.get("document_title")


def test_xlsx_parser_vertical_workbook():
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    ws = wb.active
    for row in _sample_rows():
        ws.append(list(row))
    tmp = tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False)
    tmp.close()
    try:
        wb.save(tmp.name)
        parser = FileParserFactory.create_parser(tmp.name)
        assert isinstance(parser, XlsxParser)
        fields = parser.parse()
        meta = parser.get_metadata()
        assert len(fields) >= 8
        assert meta.get("excel_layout") == "vertical_kv"
    finally:
        os.unlink(tmp.name)


def test_horizontal_header_fallback_unchanged():
    """Legacy: first row = column headers still works when not vertical layout."""
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["name", "age", "email"])
    ws.append(["", "", ""])
    tmp = tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False)
    tmp.close()
    try:
        wb.save(tmp.name)
        fields = FileParserFactory.create_parser(tmp.name).parse()
        names = {f.name for f in fields}
        assert "name" in names
        assert "age" in names
        assert "email" in names
    finally:
        os.unlink(tmp.name)
