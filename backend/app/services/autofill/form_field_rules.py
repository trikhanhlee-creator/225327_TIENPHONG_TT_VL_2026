"""Heuristics to filter non-fillable fields and normalize sections for Word forms."""

from __future__ import annotations

import re
import unicodedata

from app.services.file_parser import FileField

_SIGNATURE_FOOTER_NAME_MARKERS = (
    "dia_diem_viet_don",
    "ngay_viet_don",
    "thang_viet_don",
    "nam_viet_don",
    "ngay_nop_don",
    "noi_ky",
    "nguoi_viet_don",
    "nguoi_ky",
)

_SIGNATURE_FOOTER_LABEL_MARKERS = (
    "địa điểm viết đơn",
    "ngày viết đơn",
    "tháng viết đơn",
    "năm viết đơn",
    "người viết đơn",
    "người ký",
    "ký tên",
    "nơi ký",
    "ngày nộp đơn",
)

_LABEL_SYNONYM_GROUPS = (
    frozenset({"cong ty", "kinh gui", "cong ty ung tuyen", "cong ty kinh gui"}),
    frozenset({"dia chi hien tai", "cho o hien nay", "dia chi", "noi o hien nay"}),
    frozenset({"so dien thoai", "dien thoai lien he", "sdt"}),
    frozenset({"ho va ten", "ho ten", "ten"}),
    frozenset({"vi tri ung tuyen", "vi tri"}),
    frozenset({"truong tot nghiep", "truong"}),
    frozenset({"xep loai tot nghiep", "xep loai"}),
    frozenset({"khoa hoc", "khoa hoc da tham gia"}),
    frozenset({"ngay sinh"}),
)


def _ascii_key(text: str) -> str:
    value = (text or "").strip().lower()
    value = unicodedata.normalize("NFD", value)
    value = "".join(ch for ch in value if unicodedata.category(ch) != "Mn")
    value = value.replace("đ", "d")
    value = re.sub(r"[^\w\s]", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def is_signature_footer_field(name: str, label: str) -> bool:
    """Footer block: '..., ngày ... tháng ... năm' / Người viết đơn — not data-entry fields."""
    name_key = _ascii_key(name).replace(" ", "_")
    label_key = _ascii_key(label)

    if name_key in _SIGNATURE_FOOTER_NAME_MARKERS:
        return True

    for marker in _SIGNATURE_FOOTER_LABEL_MARKERS:
        if marker.replace(" ", "_") in name_key.replace(" ", "_"):
            return True
        if label_key == _ascii_key(marker) or label_key.endswith(_ascii_key(marker)):
            return True

    combined = f"{name_key} {label_key}"
    if "viet don" in combined or "viet_don" in name_key:
        if any(token in combined for token in ("dia diem", "ngay", "thang", "nam", "noi ky", "nguoi")):
            return True

    if "nguoi viet" in combined or "nguoi ky" in combined:
        return True

    return False


def filter_fillable_fields(fields: list[FileField]) -> list[FileField]:
    kept: list[FileField] = []
    for field in fields:
        if is_signature_footer_field(field.name, field.label):
            continue
        kept.append(field)
    for idx, field in enumerate(kept):
        field.order = idx
    return kept


def labels_are_equivalent(label_a: str, label_b: str) -> bool:
    ka = _ascii_key(label_a)
    kb = _ascii_key(label_b)
    if not ka or not kb:
        return False
    if ka == kb:
        return True
    if ka in kb or kb in ka:
        if min(len(ka), len(kb)) >= 4:
            return True
    for group in _LABEL_SYNONYM_GROUPS:
        if ka in group and kb in group:
            return True
    return False


def is_job_application_document(filename: str, document_text: str) -> bool:
    blob = _ascii_key(f"{filename} {document_text[:4000]}")
    return any(
        token in blob
        for token in (
            "don xin viec",
            "xin viec",
            "ung tuyen",
            "application letter",
        )
    )


def document_has_multi_section_structure(document_text: str) -> bool:
    text = (document_text or "").lower()
    if re.search(r"\bii\.\s", text):
        return True
    if re.search(r"\biii\.\s", text):
        return True
    if "quan hệ gia đình" in text and "thông tin bản thân" in text:
        return True
    if "tóm tắt quá trình" in text:
        return True
    return False


def normalize_sections_for_document(
    fields: list[FileField],
    *,
    filename: str,
    document_text: str,
    document_title: str | None = None,
) -> None:
    """Collapse spurious sections on single-part forms (e.g. đơn xin việc)."""
    if not fields:
        return

    if document_has_multi_section_structure(document_text):
        return

    if is_job_application_document(filename, document_text):
        title = (document_title or "").strip() or "ĐƠN XIN VIỆC"
        if len(title) > 80:
            title = "ĐƠN XIN VIỆC"
        for field in fields:
            field.section = title
        return

    # Generic single-section form: one bucket
    primary = (document_title or "").strip() or "Thông tin chung"
    if len(primary) > 80:
        primary = "Thông tin chung"
    for field in fields:
        if not (field.section or "").strip() or field.section.startswith("I. THÔNG TIN"):
            field.section = primary


def dedupe_fields_by_label(fields: list[FileField]) -> list[FileField]:
    """Drop near-duplicate fields (parser + LLM overlap)."""
    unique: list[FileField] = []
    seen_names: set[str] = set()

    for field in fields:
        name_key = (field.name or "").strip().lower()
        if not name_key or name_key in seen_names:
            continue

        duplicate = False
        for existing in unique:
            if labels_are_equivalent(existing.label, field.label):
                duplicate = True
                break
            if _ascii_key(existing.name) == _ascii_key(field.name):
                duplicate = True
                break

        if duplicate:
            continue

        seen_names.add(name_key)
        unique.append(field)

    for idx, field in enumerate(unique):
        field.order = idx

    return unique
