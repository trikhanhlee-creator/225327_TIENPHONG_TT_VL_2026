"""LLM-assisted Excel form parse — prompt theo từng loại cấu trúc sheet."""

from __future__ import annotations

import asyncio
from typing import Any

from app.core.config import settings
from app.core.logger import logger
from app.services.autofill.excel_structure import (
    ExcelLayoutKind,
    rows_from_openpyxl_worksheet,
    rows_from_xlrd_sheet,
    sheet_to_text_preview,
)
from app.services.autofill.llm_client import LLMClient
from app.services.autofill.llm_word_form_service import (
    _dicts_to_file_fields,
    _merge_parser_and_llm,
    _parse_llm_fields_payload,
)
from app.services.file_parser import FileField

_LAYOUT_PROMPTS: dict[str, str] = {
    ExcelLayoutKind.VERTICAL_KV.value: """
Cấu trúc VERTICAL_KV (phiếu/biểu mẫu dọc):
- Dòng tiêu đề phiếu ở trên.
- Một hàng header: STT | Tên trường | Giá trị cần điền | (Ghi chú).
- Các hàng sau: cột "Tên trường" = nhãn field; cột "Giá trị cần điền" = ô người dùng điền.
Chỉ lấy nhãn từ cột Tên trường; bỏ STT, header, ghi chú.""",
    ExcelLayoutKind.TWO_COLUMN_KV.value: """
Cấu trúc TWO_COLUMN_KV (2 cột nhãn–giá trị, không có header bảng):
- Cột trái: nhãn tiếng Việt (Họ tên, MSSV, ...).
- Cột phải: ô trống hoặc giá trị mẫu cần điền.
- Dòng đầu có thể là tiêu đề phiếu (bỏ qua, không tạo field).""",
    ExcelLayoutKind.HORIZONTAL_HEADER.value: """
Cấu trúc HORIZONTAL_HEADER (bảng ngang / database):
- Một hàng chứa tên cột (header): name, age, email, ...
- Các hàng dưới là dữ liệu mẫu — mỗi header là một field form.""",
    ExcelLayoutKind.FIRST_COLUMN_LABELS.value: """
Cấu trúc FIRST_COLUMN_LABELS:
- Nhãn nằm ở cột đầu tiên, thường có dấu ":" hoặc là nhãn ngắn.
- Mỗi nhãn hợp lệ = một field.""",
}


def _llm_parse_enabled() -> bool:
    if not getattr(settings, "EXCEL_LLM_PARSE_ENABLED", True):
        return False
    return bool(
        (getattr(settings, "GEMINI_API_KEY", "") or "").strip()
        or (getattr(settings, "OPENAI_API_KEY", "") or "").strip()
        or (getattr(settings, "OPENROUTER_API_KEY", "") or "").strip()
        or (getattr(settings, "AI_API_KEY", "") or "").strip()
    )


def _load_sheet_rows(file_path: str) -> list[tuple[Any, ...]]:
    ext = (file_path or "").lower()
    if ext.endswith(".xlsx"):
        from openpyxl import load_workbook

        wb = load_workbook(file_path, data_only=True)
        try:
            ws = wb.active
            return rows_from_openpyxl_worksheet(ws)
        finally:
            wb.close()
    if ext.endswith(".xls"):
        import xlrd

        book = xlrd.open_workbook(file_path)
        if book.nsheets == 0:
            return []
        return rows_from_xlrd_sheet(book.sheet_by_index(0))
    return []


def _build_excel_llm_prompt(
    *,
    sheet_text: str,
    baseline_fields: list[dict],
    original_filename: str,
    layout_kind: str,
    layout_confidence: float,
) -> str:
    baseline_preview = baseline_fields[:40]
    layout_guide = _LAYOUT_PROMPTS.get(
        layout_kind,
        "Xác định cấu trúc sheet và liệt kê các ô người dùng cần điền.",
    )
    return f"""Bạn là chuyên gia phân tích biểu mẫu Excel hành chính Việt Nam.

Parser đã nhận diện layout: **{layout_kind}** (độ tin cậy {layout_confidence:.2f}).
{layout_guide}

Nhiệm vụ: liệt kê TẤT CẢ trường người dùng cần điền.

Quy tắc chung:
1) name: snake_case ASCII, không dấu. label: tiếng Việt có dấu.
2) field_type: text|date|number|email|phone|choice.
3) Giữ thứ tự theo sheet (order tăng dần).
4) Không trùng field; không tạo field cho tiêu đề phiếu hoặc header bảng.

Trả JSON thuần:
{{
  "document_title": "...",
  "fields": [
    {{"name":"ho_va_ten","label":"Họ và tên","field_type":"text","section":"...","order":0,"options":[]}}
  ]
}}

File: {original_filename}

--- NỘI DUNG SHEET ---
{sheet_text[:10000]}

--- GỢI Ý TỪ PARSER (layout={layout_kind}, có thể thiếu) ---
{baseline_preview}
"""


class LLMExcelFormService:
    """LLM enrichment for Excel templates — prompt theo cấu trúc đã phân loại."""

    def __init__(self, llm_client: LLMClient | None = None) -> None:
        self._llm = llm_client or LLMClient()

    async def _fetch_llm_fields(
        self,
        *,
        sheet_text: str,
        baseline_dicts: list[dict],
        original_filename: str,
        layout_kind: str,
        layout_confidence: float,
    ) -> tuple[list[FileField], dict[str, Any]]:
        prompt = _build_excel_llm_prompt(
            sheet_text=sheet_text,
            baseline_fields=baseline_dicts,
            original_filename=original_filename,
            layout_kind=layout_kind,
            layout_confidence=layout_confidence,
        )
        timeout_sec = int(getattr(settings, "EXCEL_LLM_PARSE_TIMEOUT_SEC", 45))
        llm_payload: dict[str, Any] | list[Any] = {}

        try:
            llm_payload = await asyncio.wait_for(
                self._llm.complete_json(
                    task_name="excel_form_intelligent_parse",
                    prompt=prompt,
                    fallback={},
                ),
                timeout=timeout_sec,
            )
        except asyncio.TimeoutError:
            logger.warning("LLM Excel form parse timed out after %ss", timeout_sec)
            return [], {}

        llm_items = _parse_llm_fields_payload(llm_payload)
        llm_fields = _dicts_to_file_fields(llm_items)
        meta: dict[str, Any] = {}
        if isinstance(llm_payload, dict) and llm_payload.get("document_title"):
            meta["document_title"] = llm_payload.get("document_title")
        return llm_fields, meta

    async def enhance_excel_fields(
        self,
        *,
        file_path: str,
        parser_fields: list[FileField],
        original_filename: str,
        parse_meta: dict[str, Any] | None = None,
    ) -> tuple[list[FileField], dict[str, Any]]:
        meta: dict[str, Any] = dict(parse_meta or {})
        meta.setdefault("strategy", "parser_only")
        meta["llm_field_count"] = 0

        baseline_dicts = [field.to_dict() for field in parser_fields]
        min_fields = int(getattr(settings, "EXCEL_LLM_PARSE_MIN_FIELDS", 3))
        layout_kind = str(meta.get("excel_layout") or ExcelLayoutKind.UNKNOWN.value)
        layout_confidence = float(meta.get("excel_layout_confidence") or 0.0)

        if not _llm_parse_enabled():
            meta["reason"] = "llm_disabled_or_no_api_key"
            return list(parser_fields), meta

        rows = _load_sheet_rows(file_path)
        sheet_text = sheet_to_text_preview(rows)
        if not sheet_text.strip():
            meta["reason"] = "empty_sheet_text"
            return list(parser_fields), meta

        llm_fields, llm_meta = await self._fetch_llm_fields(
            sheet_text=sheet_text,
            baseline_dicts=baseline_dicts,
            original_filename=original_filename,
            layout_kind=layout_kind,
            layout_confidence=layout_confidence,
        )
        if llm_meta.get("document_title"):
            meta["document_title"] = llm_meta["document_title"]
            meta["title"] = llm_meta["document_title"]

        if len(llm_fields) >= min_fields:
            meta["strategy"] = "llm"
        elif llm_fields:
            meta["strategy"] = "llm_partial"
        else:
            meta["strategy"] = "parser_only"
            meta["reason"] = "llm_empty_or_invalid"
            return list(parser_fields), meta

        merged = _merge_parser_and_llm(parser_fields, llm_fields)
        meta.update(
            {
                "llm_field_count": len(llm_fields),
                "parser_field_count": len(parser_fields),
                "final_field_count": len(merged),
                "strategy": meta.get("strategy"),
            }
        )
        logger.info(
            "LLMExcelFormService: %s fields for %s (layout=%s, strategy=%s)",
            len(merged),
            original_filename,
            layout_kind,
            meta.get("strategy"),
        )
        return merged, meta


async def enhance_excel_template_fields(
    *,
    file_path: str,
    parser_fields: list[FileField],
    original_filename: str,
    parse_meta: dict[str, Any] | None = None,
) -> tuple[list[FileField], dict[str, Any]]:
    service = LLMExcelFormService()
    return await service.enhance_excel_fields(
        file_path=file_path,
        parser_fields=parser_fields,
        original_filename=original_filename,
        parse_meta=parse_meta,
    )
