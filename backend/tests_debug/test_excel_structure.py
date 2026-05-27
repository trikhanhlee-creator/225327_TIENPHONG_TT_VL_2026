"""Tests for Excel structure classification and layout-specific parsing."""

from __future__ import annotations

import os
import sys
import tempfile

import pytest

BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

from app.services.autofill.excel_structure import (  # noqa: E402
    ExcelLayoutKind,
    classify_excel_structure,
    parse_excel_rows,
)
from app.services.file_parser import FileParserFactory, XlsxParser  # noqa: E402


def _detect_type(label: str) -> str:
    lower = label.lower()
    if "ngày" in lower or "ngay" in lower:
        return "date"
    if "email" in lower:
        return "email"
    return "text"


def _clean_label(text: str) -> str:
    return (text or "").strip().rstrip(":")


def _vertical_form_rows():
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


def _two_column_rows():
    return [
        ("Phiếu xác nhận sinh viên", ""),
        ("Họ và tên", ""),
        ("Mã số sinh viên", ""),
        ("Ngày sinh", ""),
        ("Khoa", ""),
        ("Ngành học", ""),
        ("Mục đích xác nhận", ""),
        ("Ngày làm đơn", ""),
    ]


def test_classify_vertical_kv():
    result = classify_excel_structure(_vertical_form_rows())
    assert result.chosen.kind == ExcelLayoutKind.VERTICAL_KV
    assert result.chosen.confidence >= 0.7


def test_classify_horizontal_header():
    rows = [("name", "age", "email"), ("John", "20", "a@b.com")]
    result = classify_excel_structure(rows)
    assert result.chosen.kind == ExcelLayoutKind.HORIZONTAL_HEADER
    assert result.chosen.confidence >= 0.4


def test_classify_two_column_kv():
    result = classify_excel_structure(_two_column_rows())
    assert result.chosen.kind in (
        ExcelLayoutKind.TWO_COLUMN_KV,
        ExcelLayoutKind.VERTICAL_KV,
    )
    assert result.chosen.confidence >= 0.4


def test_title_row_not_parsed_as_horizontal_fields():
    """Tiêu đề phiếu dòng 1 không được nhận nhầm thành header ngang."""
    fields, meta = parse_excel_rows(
        _vertical_form_rows(),
        detect_field_type=_detect_type,
        clean_field_label=_clean_label,
    )
    assert meta["excel_layout"] == ExcelLayoutKind.VERTICAL_KV.value
    assert len(fields) == 8
    names = {f.name for f in fields}
    assert "ho_va_ten" in names
    assert "phieu_xac_nhan_sinh_vien" not in names


def test_parse_two_column_without_table_header():
    fields, meta = parse_excel_rows(
        _two_column_rows(),
        detect_field_type=_detect_type,
        clean_field_label=_clean_label,
    )
    assert meta["excel_layout"] in (
        ExcelLayoutKind.TWO_COLUMN_KV.value,
        ExcelLayoutKind.VERTICAL_KV.value,
    )
    assert len(fields) >= 6


def test_horizontal_workbook():
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["name", "age", "email"])
    ws.append(["x", "1", "a@b.com"])
    tmp = tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False)
    tmp.close()
    try:
        wb.save(tmp.name)
        fields = FileParserFactory.create_parser(tmp.name).parse()
        meta = FileParserFactory.create_parser(tmp.name).get_metadata()
        # parse again for meta - use single parser
        parser = FileParserFactory.create_parser(tmp.name)
        fields = parser.parse()
        meta = parser.get_metadata()
        assert meta["excel_layout"] == ExcelLayoutKind.HORIZONTAL_HEADER.value
        assert {f.name for f in fields} == {"name", "age", "email"}
    finally:
        os.unlink(tmp.name)


def test_vertical_workbook_metadata():
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    ws = wb.active
    for row in _vertical_form_rows():
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
        assert meta["excel_layout"] == ExcelLayoutKind.VERTICAL_KV.value
        assert meta.get("excel_layout_confidence", 0) >= 0.7
        assert "excel_layout_candidates" in meta
    finally:
        os.unlink(tmp.name)
