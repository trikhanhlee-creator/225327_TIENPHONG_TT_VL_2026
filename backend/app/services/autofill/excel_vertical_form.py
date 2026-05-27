"""
Backward-compatible re-exports. Prefer app.services.autofill.excel_structure.
"""

from app.services.autofill.excel_structure import (
    VerticalFormLayout,
    ascii_slug as _ascii_slug,
    detect_vertical_form_layout,
    normalize_cell_text,
    parse_excel_rows,
    rows_from_openpyxl_worksheet,
    rows_from_xlrd_sheet,
    sheet_to_text_preview,
)
from app.services.file_parser import FileField


def parse_vertical_form_fields(
    rows,
    layout: VerticalFormLayout,
    *,
    detect_field_type,
    clean_field_label,
    max_fields: int = 80,
):
    from app.services.autofill.excel_structure import ExcelLayoutKind, LayoutCandidate, _parse_vertical_kv

    candidate = LayoutCandidate(
        kind=ExcelLayoutKind.VERTICAL_KV,
        confidence=1.0,
        vertical_layout=layout,
        header_row_index=layout.header_row_index,
        label_col=layout.label_col,
        value_col=layout.value_col,
        title=layout.title,
    )
    fields = _parse_vertical_kv(
        rows,
        candidate,
        detect_field_type=detect_field_type,
        clean_field_label=clean_field_label,
    )
    return fields[:max_fields]


def try_parse_vertical_excel_rows(
    rows,
    *,
    detect_field_type,
    clean_field_label,
):
    from app.services.autofill.excel_structure import ExcelLayoutKind, classify_excel_structure

    detection = classify_excel_structure(rows)
    if detection.chosen.kind != ExcelLayoutKind.VERTICAL_KV:
        return [], {}

    fields, meta = parse_excel_rows(
        rows,
        detect_field_type=detect_field_type,
        clean_field_label=clean_field_label,
    )
    if meta.get("excel_layout") != ExcelLayoutKind.VERTICAL_KV.value:
        return [], {}
    return fields, meta
