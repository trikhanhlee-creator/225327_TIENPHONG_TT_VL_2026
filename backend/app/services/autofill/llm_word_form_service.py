"""LLM-assisted Word form field extraction for complex Vietnamese templates."""

from __future__ import annotations

import asyncio
import re
import unicodedata
from typing import Any

from app.core.config import settings
from app.core.logger import logger
from app.services.autofill.document_structure import extract_structured_document
from app.services.autofill.form_field_rules import (
    dedupe_fields_by_label,
    filter_fillable_fields,
    is_signature_footer_field,
    labels_are_equivalent,
    normalize_sections_for_document,
)
from app.services.autofill.llm_client import LLMClient
from app.services.file_parser import FileField

_VALID_FIELD_TYPES = {
    "text",
    "date",
    "number",
    "email",
    "phone",
    "choice",
    "radio",
}


def _ascii_field_name(label: str, fallback: str = "field") -> str:
    value = (label or "").lower().strip()
    value = unicodedata.normalize("NFD", value)
    value = "".join(ch for ch in value if unicodedata.category(ch) != "Mn")
    value = re.sub(r"[^\w\s]", " ", value)
    value = re.sub(r"\s+", "_", value).strip("_")
    return value or fallback


def _normalize_field_type(raw: str) -> str:
    normalized = str(raw or "text").strip().lower()
    if normalized in _VALID_FIELD_TYPES:
        return "choice" if normalized == "radio" else normalized
    return "text"


def _llm_parse_enabled() -> bool:
    if not getattr(settings, "WORD_LLM_PARSE_ENABLED", True):
        return False
    return bool(
        (getattr(settings, "GEMINI_API_KEY", "") or "").strip()
        or (getattr(settings, "OPENAI_API_KEY", "") or "").strip()
        or (getattr(settings, "OPENROUTER_API_KEY", "") or "").strip()
        or (getattr(settings, "AI_API_KEY", "") or "").strip()
    )


def _is_syll_filename(filename: str) -> bool:
    name = (filename or "").lower().replace("_", "-")
    return any(
        token in name
        for token in (
            "so-yeu",
            "ly-lich",
            "syll",
            "soyeu",
            "lylich",
        )
    )


def _is_syll_template(document_text: str) -> bool:
    lower = (document_text or "").lower()
    return "sơ yếu lý lịch" in lower or "so yeu ly lich" in lower or "tự thuật" in lower


def _fallback_syll_template_fields() -> list[FileField]:
    """Deterministic full schema for standard Vietnamese Sơ yếu lý lịch templates."""
    specs: list[tuple[str, str, str, str, list[str]]] = [
        ("ho_va_ten", "Họ và tên (chữ in hoa)", "text", "I. THÔNG TIN BẢN THÂN", []),
        ("gioi_tinh", "Giới tính", "choice", "I. THÔNG TIN BẢN THÂN", ["Nam", "Nữ"]),
        ("ngay_sinh", "Ngày sinh", "number", "I. THÔNG TIN BẢN THÂN", []),
        ("thang_sinh", "Tháng sinh", "number", "I. THÔNG TIN BẢN THÂN", []),
        ("nam_sinh", "Năm sinh", "number", "I. THÔNG TIN BẢN THÂN", []),
        ("noi_sinh", "Nơi sinh", "text", "I. THÔNG TIN BẢN THÂN", []),
        ("nguyen_quan", "Nguyên quán", "text", "I. THÔNG TIN BẢN THÂN", []),
        ("ho_khau_thuong_tru", "Nơi đăng ký hộ khẩu thường trú", "text", "I. THÔNG TIN BẢN THÂN", []),
        ("cho_o_hien_nay", "Chỗ ở hiện nay", "text", "I. THÔNG TIN BẢN THÂN", []),
        ("dien_thoai_lien_he", "Điện thoại liên hệ", "phone", "I. THÔNG TIN BẢN THÂN", []),
        ("dan_toc", "Dân tộc", "text", "I. THÔNG TIN BẢN THÂN", []),
        ("ton_giao", "Tôn giáo", "text", "I. THÔNG TIN BẢN THÂN", []),
        ("so_cccd_cmnd", "Số CCCD/CMND", "text", "I. THÔNG TIN BẢN THÂN", []),
        ("ngay_cap_cccd", "Ngày cấp CCCD/CMND", "date", "I. THÔNG TIN BẢN THÂN", []),
        ("noi_cap_cccd", "Nơi cấp CCCD/CMND", "text", "I. THÔNG TIN BẢN THÂN", []),
        ("trinh_do_van_hoa", "Trình độ văn hóa", "text", "I. THÔNG TIN BẢN THÂN", []),
        ("ket_nap_doan", "Kết nạp Đoàn TNCS HCM", "text", "I. THÔNG TIN BẢN THÂN", []),
        ("ket_nap_doan_tai", "Kết nạp Đoàn - tại", "text", "I. THÔNG TIN BẢN THÂN", []),
        ("ket_nap_dang", "Kết nạp Đảng CSVN", "text", "I. THÔNG TIN BẢN THÂN", []),
        ("ket_nap_dang_tai", "Kết nạp Đảng - tại", "text", "I. THÔNG TIN BẢN THÂN", []),
        ("khen_thuong_ky_luat", "Khen thưởng / Kỷ luật", "text", "I. THÔNG TIN BẢN THÂN", []),
        ("so_truong", "Sở trường", "text", "I. THÔNG TIN BẢN THÂN", []),
        ("cha_ho_ten", "Họ và tên cha", "text", "II. QUAN HỆ GIA ĐÌNH", []),
        ("cha_nam_sinh", "Năm sinh (cha)", "number", "II. QUAN HỆ GIA ĐÌNH", []),
        ("cha_nghe_nghiep", "Nghề nghiệp hiện nay (cha)", "text", "II. QUAN HỆ GIA ĐÌNH", []),
        ("cha_co_quan", "Cơ quan công tác (cha)", "text", "II. QUAN HỆ GIA ĐÌNH", []),
        ("cha_cho_o", "Chỗ ở hiện nay (cha)", "text", "II. QUAN HỆ GIA ĐÌNH", []),
        ("me_ho_ten", "Họ và tên mẹ", "text", "II. QUAN HỆ GIA ĐÌNH", []),
        ("me_nam_sinh", "Năm sinh (mẹ)", "number", "II. QUAN HỆ GIA ĐÌNH", []),
        ("me_nghe_nghiep", "Nghề nghiệp hiện nay (mẹ)", "text", "II. QUAN HỆ GIA ĐÌNH", []),
        ("me_co_quan", "Cơ quan công tác (mẹ)", "text", "II. QUAN HỆ GIA ĐÌNH", []),
        ("me_cho_o", "Chỗ ở hiện nay (mẹ)", "text", "II. QUAN HỆ GIA ĐÌNH", []),
    ]

    for sibling_idx in range(1, 4):
        prefix = f"anh_chi_em_{sibling_idx}"
        section = "II. QUAN HỆ GIA ĐÌNH"
        specs.extend(
            [
                (f"{prefix}_ho_ten", f"Họ và tên anh/chị/em ruột ({sibling_idx})", "text", section, []),
                (f"{prefix}_nam_sinh", f"Năm sinh anh/chị/em ({sibling_idx})", "number", section, []),
                (f"{prefix}_nghe_nghiep", f"Nghề nghiệp anh/chị/em ({sibling_idx})", "text", section, []),
                (f"{prefix}_co_quan", f"Cơ quan công tác anh/chị/em ({sibling_idx})", "text", section, []),
            ]
        )

    for row_idx in range(1, 6):
        section = "III. TÓM TẮT QUÁ TRÌNH ĐÀO TẠO"
        specs.extend(
            [
                (f"dao_tao_{row_idx}_thoi_gian", f"Đào tạo {row_idx} - Thời gian", "text", section, []),
                (f"dao_tao_{row_idx}_truong", f"Đào tạo {row_idx} - Trường/cơ sở", "text", section, []),
                (f"dao_tao_{row_idx}_nganh", f"Đào tạo {row_idx} - Ngành học", "text", section, []),
                (f"dao_tao_{row_idx}_hinh_thuc", f"Đào tạo {row_idx} - Hình thức", "text", section, []),
                (f"dao_tao_{row_idx}_bang", f"Đào tạo {row_idx} - Văn bằng/chứng chỉ", "text", section, []),
            ]
        )

    for row_idx in range(1, 5):
        section = "IV. TÓM TẮT QUÁ TRÌNH CÔNG TÁC"
        specs.extend(
            [
                (f"cong_tac_{row_idx}_thoi_gian", f"Công tác {row_idx} - Thời gian", "text", section, []),
                (f"cong_tac_{row_idx}_don_vi", f"Công tác {row_idx} - Đơn vị", "text", section, []),
                (f"cong_tac_{row_idx}_chuc_vu", f"Công tác {row_idx} - Chức vụ", "text", section, []),
            ]
        )

    specs.append(("ngay_nop_don", "Ngày nộp đơn", "date", "KÝ TÊN", []))

    fields: list[FileField] = []
    for order, (name, label, field_type, section, options) in enumerate(specs):
        fields.append(
            FileField(
                name=name,
                field_type=field_type,
                label=label,
                order=order,
                options=options,
                section=section,
            )
        )
    return fields


def _dicts_to_file_fields(items: list[dict[str, Any]]) -> list[FileField]:
    fields: list[FileField] = []
    seen: set[str] = set()

    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            continue

        label = str(item.get("label") or "").strip()
        name = str(item.get("name") or item.get("field_key") or "").strip()
        if not name and label:
            name = _ascii_field_name(label, f"field_{idx + 1}")
        if not label:
            label = name.replace("_", " ").title()

        name = _ascii_field_name(name, f"field_{idx + 1}")
        if not name or name in seen:
            continue
        seen.add(name)

        options_raw = item.get("options") or []
        options: list[str] = []
        if isinstance(options_raw, list):
            for opt in options_raw:
                opt_text = str(opt).strip()
                if opt_text:
                    options.append(opt_text)

        field_type = _normalize_field_type(str(item.get("field_type") or "text"))
        if field_type == "choice" and len(options) < 2:
            field_type = "text"

        if is_signature_footer_field(name, label):
            continue

        fields.append(
            FileField(
                name=name,
                field_type=field_type,
                label=label,
                order=int(item.get("order", idx)) if str(item.get("order", "")).isdigit() else idx,
                options=options,
                section=str(item.get("section") or "").strip(),
            )
        )

    for order, field in enumerate(fields):
        field.order = order

    return fields


def _infer_section_from_field(name: str, label: str) -> str:
    """Heuristic section when LLM/parser omitted section."""
    if is_signature_footer_field(name, label):
        return ""

    text = f"{name} {label}".lower()
    if any(k in text for k in ("dao_tao_", "đào tạo", "tot nghiep", "tốt nghiệp", "truong", "trường")):
        if "cong_tac" not in text and "công tác" not in text:
            return "III. TÓM TẮT QUÁ TRÌNH ĐÀO TẠO"
    if any(k in text for k in ("cong_tac_", "công tác", "don_vi", "chức vụ", "chuc_vu")):
        return "IV. TÓM TẮT QUÁ TRÌNH CÔNG TÁC"
    if any(k in text for k in ("cha_", "me_", "anh_chi_em", "gia đình", "gia dinh", "bố", "mẹ")):
        return "II. QUAN HỆ GIA ĐÌNH"
    if any(k in text for k in ("ky ten", "ký tên", "ngay nop", "ngày nộp", "viet don", "viết đơn")):
        return ""
    return ""


def _ensure_field_sections(fields: list[FileField]) -> None:
    for field in fields:
        if not (field.section or "").strip():
            inferred = _infer_section_from_field(field.name, field.label)
            if inferred:
                field.section = inferred


def _build_llm_prompt(*, document_text: str, baseline_fields: list[dict], filename: str) -> str:
    baseline_preview = baseline_fields[:40]
    return f"""Bạn là chuyên gia phân tích biểu mẫu hành chính Việt Nam.
Nhiệm vụ: đọc tài liệu Word và liệt kê TẤT CẢ ô/trường người dùng cần điền để tạo form điện tử.

Quy tắc bắt buộc:
1) Mỗi nhãn có dấu chấm/placeholder (…, ___, -----) là một trường riêng.
2) Sinh ngày (ngày/tháng/năm sinh trong phần nội dung): MỘT trường field_type "date" tên ngay_sinh, label "Ngày sinh" — KHÔNG tách 3 trường ngày/tháng/năm (trừ mẫu Sơ yếu lý lịch có mục riêng).
3) KHÔNG tạo trường cho khối chữ ký cuối đơn: "..., ngày ... tháng ... năm", "Địa điểm viết đơn", "Ngày/Tháng/Năm viết đơn", "Người viết đơn", "Ký tên" — đây là phần ký, không phải ô nhập dữ liệu hồ sơ.
4) Nam/Nữ hoặc [ ] Nam [ ] Nữ → field_type "choice", options ["Nam","Nữ"].
5) Bảng: mỗi hàng dữ liệu trống dưới header là một bộ trường; đặt tên có hậu tố _1, _2...
6) Phần gia đình (chỉ khi tài liệu có mục II): tách riêng cha, mẹ, từng anh/chị/em.
7) name: snake_case ASCII, không dấu. label: tiếng Việt có dấu, rõ ràng.
8) field_type: text|date|number|email|phone|choice.
9) section: chỉ dùng nhiều section (I, II, III...) khi tài liệu THỰC SỰ có các mục đó. Đơn xin việc đơn giản → một section duy nhất "ĐƠN XIN VIỆC".
10) Không bỏ sót bảng đào tạo và bảng công tác (nếu có trong tài liệu).
11) Giữ thứ tự trường theo thứ tự xuất hiện trong tài liệu (order tăng dần).
12) Không trùng trường (một ý chỉ một field): ví dụ "Công ty ứng tuyển" và "Công ty kính gửi" là một trường.

Trả về JSON thuần (không markdown):
{{
  "document_title": "...",
  "fields": [
    {{"name":"ho_va_ten","label":"Họ và tên","field_type":"text","section":"I. ...","order":0,"options":[]}}
  ]
}}

File: {filename}

--- NỘI DUNG TÀI LIỆU ---
{document_text[:12000]}

--- GỢI Ý TỪ PARSER (có thể thiếu/sai, chỉ tham khảo) ---
{baseline_preview}
"""


def _parse_llm_fields_payload(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]

    if not isinstance(payload, dict):
        return []

    fields = payload.get("fields")
    if isinstance(fields, list):
        return [item for item in fields if isinstance(item, dict)]

    return []


def _merge_parser_and_llm(parser_fields: list[FileField], llm_fields: list[FileField]) -> list[FileField]:
    """Prefer LLM ordering; fill gaps from parser by normalized label."""
    if not llm_fields:
        return parser_fields
    if not parser_fields:
        return llm_fields

    llm_by_name = {field.name: field for field in llm_fields}
    llm_labels = {field.label.strip().lower(): field for field in llm_fields if field.label}

    merged = list(llm_fields)
    seen = set(llm_by_name.keys())

    for parser_field in parser_fields:
        if parser_field.name in seen:
            continue
        if is_signature_footer_field(parser_field.name, parser_field.label):
            continue
        label_key = parser_field.label.strip().lower()
        if label_key and label_key in llm_labels:
            continue
        if any(labels_are_equivalent(parser_field.label, llm_field.label) for llm_field in llm_fields):
            continue
        parser_field.order = len(merged)
        merged.append(parser_field)
        seen.add(parser_field.name)

    return merged


class LLMWordFormService:
    """Use LLM + document structure to build complete Word form schemas."""

    def __init__(self, llm_client: LLMClient | None = None) -> None:
        self._llm = llm_client or LLMClient()

    async def _fetch_llm_fields(
        self,
        *,
        structured_text: str,
        baseline_dicts: list[dict],
        original_filename: str,
    ) -> tuple[list[FileField], dict[str, Any]]:
        prompt = _build_llm_prompt(
            document_text=structured_text,
            baseline_fields=baseline_dicts,
            filename=original_filename,
        )
        timeout_sec = int(getattr(settings, "WORD_LLM_PARSE_TIMEOUT_SEC", 45))
        llm_payload: dict[str, Any] | list[Any] = {}

        try:
            llm_payload = await asyncio.wait_for(
                self._llm.complete_json(
                    task_name="word_form_intelligent_parse",
                    prompt=prompt,
                    fallback={},
                ),
                timeout=timeout_sec,
            )
        except asyncio.TimeoutError:
            logger.warning("LLM Word form parse timed out after %ss", timeout_sec)
            return [], {}

        llm_items = _parse_llm_fields_payload(llm_payload)
        llm_fields = _dicts_to_file_fields(llm_items)
        meta: dict[str, Any] = {}
        if isinstance(llm_payload, dict) and llm_payload.get("document_title"):
            meta["document_title"] = llm_payload.get("document_title")
        return llm_fields, meta

    async def enhance_word_fields(
        self,
        *,
        file_path: str,
        parser_fields: list[FileField],
        original_filename: str,
    ) -> tuple[list[FileField], dict[str, Any]]:
        """
        Return enhanced fields and metadata about the parse strategy used.
        """
        meta: dict[str, Any] = {"strategy": "parser_only", "llm_field_count": 0}

        baseline_dicts = [field.to_dict() for field in parser_fields]
        min_fields = int(getattr(settings, "WORD_LLM_PARSE_MIN_FIELDS", 12))
        llm_fields: list[FileField] = []
        use_syll_template = getattr(settings, "WORD_LLM_SYLL_USE_TEMPLATE", True)
        is_syll = _is_syll_filename(original_filename)

        structured_text = ""
        if not (is_syll and use_syll_template):
            structured_text = extract_structured_document(file_path)
            if not structured_text:
                from app.services.autofill.rag_form_service import extract_indexable_text

                structured_text = extract_indexable_text(file_path)
            if not is_syll:
                is_syll = _is_syll_template(structured_text)

        if is_syll and use_syll_template:
            template_fields = _fallback_syll_template_fields()
            llm_fields = list(template_fields)
            meta["strategy"] = "syll_template"
            _ensure_field_sections(llm_fields)

            if _llm_parse_enabled() and getattr(settings, "WORD_LLM_SYLL_TRY_LLM", False):
                extra_fields, llm_meta = await self._fetch_llm_fields(
                    structured_text=structured_text,
                    baseline_dicts=baseline_dicts,
                    original_filename=original_filename,
                )
                if extra_fields:
                    llm_fields = _merge_parser_and_llm(template_fields, extra_fields)
                    meta["strategy"] = "syll_template_plus_llm"
                if llm_meta.get("document_title"):
                    meta["document_title"] = llm_meta["document_title"]
        elif _llm_parse_enabled():
            llm_fields, llm_meta = await self._fetch_llm_fields(
                structured_text=structured_text,
                baseline_dicts=baseline_dicts,
                original_filename=original_filename,
            )
            if llm_meta.get("document_title"):
                meta["document_title"] = llm_meta["document_title"]

            if len(llm_fields) >= min_fields:
                meta["strategy"] = "llm"
            elif is_syll:
                template_fields = _fallback_syll_template_fields()
                llm_fields = _merge_parser_and_llm(template_fields, llm_fields)
                meta["strategy"] = "syll_template_plus_llm"
            elif llm_fields:
                meta["strategy"] = "llm_partial"
            else:
                meta["strategy"] = "parser_only"
                meta["reason"] = "llm_empty_or_invalid"
                cleaned = filter_fillable_fields(dedupe_fields_by_label(list(parser_fields)))
                normalize_sections_for_document(
                    cleaned,
                    filename=original_filename,
                    document_text=structured_text,
                )
                return cleaned, meta
        elif is_syll:
            llm_fields = _fallback_syll_template_fields()
            meta["strategy"] = "syll_template"
        else:
            meta["reason"] = "llm_disabled_or_no_api_key"
            cleaned = filter_fillable_fields(dedupe_fields_by_label(list(parser_fields)))
            normalize_sections_for_document(
                cleaned,
                filename=original_filename,
                document_text=structured_text,
            )
            return cleaned, meta

        merged = _merge_parser_and_llm(parser_fields, llm_fields)
        merged = filter_fillable_fields(merged)
        merged = dedupe_fields_by_label(merged)
        _ensure_field_sections(merged)
        normalize_sections_for_document(
            merged,
            filename=original_filename,
            document_text=structured_text,
            document_title=str(meta.get("document_title") or "").strip() or None,
        )
        meta.update(
            {
                "llm_field_count": len(llm_fields),
                "parser_field_count": len(parser_fields),
                "final_field_count": len(merged),
                "section_count": len({(f.section or "").strip() for f in merged if (f.section or "").strip()}),
            }
        )

        logger.info(
            "LLMWordFormService: %s fields for %s (strategy=%s)",
            len(merged),
            original_filename,
            meta.get("strategy"),
        )
        return merged, meta


async def enhance_word_template_fields(
    *,
    file_path: str,
    parser_fields: list[FileField],
    original_filename: str,
) -> tuple[list[FileField], dict[str, Any]]:
    service = LLMWordFormService()
    return await service.enhance_word_fields(
        file_path=file_path,
        parser_fields=parser_fields,
        original_filename=original_filename,
    )
