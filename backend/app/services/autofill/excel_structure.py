"""
Excel structure detection and layout-specific parsing.

Supported layouts (excel_layout):
  - vertical_kv:     STT | Tên trường | Giá trị cần điền | Ghi chú
  - two_column_kv:   nhãn cột trái + ô trống cột phải (không có header bảng)
  - horizontal_header: hàng header ngang + nhiều cột dữ liệu (demoexcel / DB)
  - first_column_labels: nhãn ở cột đầu, có dấu : hoặc nhãn ngắn
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Callable, Sequence

from app.services.file_parser import FileField

# --- shared text helpers (also used by excel_vertical_form) ---

_HEADER_LABEL_MARKERS = ("ten truong", "tên trường", "field name")
_HEADER_VALUE_MARKERS = ("gia tri", "giá trị", "gia tri can dien", "value")
_SKIP_LABEL_MARKERS = (
    "stt",
    "so thu tu",
    "số thứ tự",
    "ten truong",
    "tên trường",
    "gia tri",
    "giá trị",
    "ghi chu",
    "ghi chú",
    "note",
    "notes",
)


class ExcelLayoutKind(StrEnum):
    VERTICAL_KV = "vertical_kv"
    TWO_COLUMN_KV = "two_column_kv"
    HORIZONTAL_HEADER = "horizontal_header"
    FIRST_COLUMN_LABELS = "first_column_labels"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class VerticalFormLayout:
    header_row_index: int
    label_col: int
    value_col: int
    title: str = ""


@dataclass
class LayoutCandidate:
    kind: ExcelLayoutKind
    confidence: float
    vertical_layout: VerticalFormLayout | None = None
    header_row_index: int = 0
    label_col: int = 0
    value_col: int = 1
    title: str = ""


@dataclass
class StructureDetectionResult:
    chosen: LayoutCandidate
    candidates: list[LayoutCandidate] = field(default_factory=list)


def normalize_cell_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    text = unicodedata.normalize("NFC", text)
    text = text.lower()
    text = unicodedata.normalize("NFD", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def ascii_slug(text: str, fallback: str = "field") -> str:
    value = (text or "").lower().strip()
    value = unicodedata.normalize("NFD", value)
    value = "".join(ch for ch in value if unicodedata.category(ch) != "Mn")
    value = re.sub(r"[^\w\s]", " ", value)
    value = re.sub(r"\s+", "_", value).strip("_")
    return value or fallback


def _row_cells(row: Sequence[Any]) -> list[tuple[int, str, str]]:
    cells: list[tuple[int, str, str]] = []
    for col_index, cell in enumerate(row or []):
        if cell is None:
            continue
        raw = str(cell).strip()
        if not raw:
            continue
        cells.append((col_index, raw, normalize_cell_text(raw)))
    return cells


def _max_column_count(rows: Sequence[Sequence[Any]], *, limit: int = 40) -> int:
    max_cols = 0
    for row in rows[:limit]:
        if row:
            max_cols = max(max_cols, len(row))
    return max_cols


def _is_skip_label(normalized_label: str, raw_label: str) -> bool:
    if not normalized_label or len(normalized_label) < 2:
        return True
    if normalized_label.isdigit():
        return True
    if any(marker in normalized_label for marker in _SKIP_LABEL_MARKERS):
        return True
    if raw_label.strip().lower() in {"stt", "no", "no."}:
        return True
    return False


def extract_sheet_title(rows: Sequence[Sequence[Any]], before_row: int) -> str:
    best = ""
    for row in rows[: max(0, before_row)]:
        for _, raw, norm in _row_cells(row):
            if len(raw) < 4 or norm.isdigit():
                continue
            if any(m in norm for m in _HEADER_LABEL_MARKERS + _HEADER_VALUE_MARKERS):
                continue
            if len(raw) > len(best):
                best = raw
    return best


def detect_vertical_form_layout(rows: Sequence[Sequence[Any]]) -> VerticalFormLayout | None:
    for row_index, row in enumerate(rows[:25]):
        label_col: int | None = None
        value_col: int | None = None
        for col_index, _, norm in _row_cells(row):
            if any(marker in norm for marker in _HEADER_LABEL_MARKERS):
                label_col = col_index
            if any(marker in norm for marker in _HEADER_VALUE_MARKERS):
                value_col = col_index
        if label_col is None or value_col is None or label_col == value_col:
            continue
        title = extract_sheet_title(rows, row_index)
        return VerticalFormLayout(
            header_row_index=row_index,
            label_col=label_col,
            value_col=value_col,
            title=title,
        )
    return None


def _count_vertical_labels(
    rows: Sequence[Sequence[Any]],
    layout: VerticalFormLayout,
) -> int:
    count = 0
    for row in rows[layout.header_row_index + 1 :]:
        if layout.label_col >= len(row or ()):
            continue
        raw = row[layout.label_col]
        if raw is None:
            continue
        label = str(raw).strip()
        if not label:
            continue
        norm = normalize_cell_text(label)
        if _is_skip_label(norm, label):
            continue
        count += 1
    return count


def _score_vertical_kv(rows: Sequence[Sequence[Any]]) -> LayoutCandidate:
    layout = detect_vertical_form_layout(rows)
    if layout is None:
        return LayoutCandidate(ExcelLayoutKind.VERTICAL_KV, 0.0)

    label_count = _count_vertical_labels(rows, layout)
    if label_count < 2:
        return LayoutCandidate(ExcelLayoutKind.VERTICAL_KV, 0.0)

    confidence = 0.72 + min(label_count * 0.025, 0.22)
    return LayoutCandidate(
        ExcelLayoutKind.VERTICAL_KV,
        min(confidence, 0.98),
        vertical_layout=layout,
        header_row_index=layout.header_row_index,
        label_col=layout.label_col,
        value_col=layout.value_col,
        title=layout.title,
    )


def _score_two_column_kv(rows: Sequence[Sequence[Any]]) -> LayoutCandidate:
    max_cols = _max_column_count(rows)
    if max_cols < 2:
        return LayoutCandidate(ExcelLayoutKind.TWO_COLUMN_KV, 0.0)

    best: LayoutCandidate = LayoutCandidate(ExcelLayoutKind.TWO_COLUMN_KV, 0.0)

    for label_col in range(max_cols - 1):
        value_col = label_col + 1
        label_rows = 0
        empty_value_rows = 0
        for row in rows[:40]:
            cells = _row_cells(row)
            if not cells:
                continue
            if len(cells) == 1 and len(cells[0][1]) > 60:
                continue
            if any(m in c[2] for c in cells for m in _HEADER_LABEL_MARKERS + _HEADER_VALUE_MARKERS):
                continue

            label_raw = ""
            value_raw = ""
            if label_col < len(row or ()) and row[label_col] is not None:
                label_raw = str(row[label_col]).strip()
            if value_col < len(row or ()) and row[value_col] is not None:
                value_raw = str(row[value_col]).strip()

            if not label_raw or len(label_raw) > 80:
                continue
            norm = normalize_cell_text(label_raw)
            if _is_skip_label(norm, label_raw):
                continue

            label_rows += 1
            if not value_raw:
                empty_value_rows += 1

        if label_rows < 3:
            continue

        empty_ratio = empty_value_rows / max(label_rows, 1)
        confidence = 0.35 + min(label_rows * 0.04, 0.35) + empty_ratio * 0.25
        if confidence > best.confidence:
            best = LayoutCandidate(
                ExcelLayoutKind.TWO_COLUMN_KV,
                min(confidence, 0.9),
                label_col=label_col,
                value_col=value_col,
            )

    return best


def _looks_like_header_cell(norm: str, raw: str) -> bool:
    if not norm or len(raw) > 60:
        return False
    if any(m in norm for m in _HEADER_LABEL_MARKERS + _HEADER_VALUE_MARKERS):
        return False
    if norm.isdigit():
        return False
    return 2 <= len(norm) <= 40


def _score_horizontal_header(rows: Sequence[Sequence[Any]]) -> LayoutCandidate:
    best = LayoutCandidate(ExcelLayoutKind.HORIZONTAL_HEADER, 0.0)

    for row_index, row in enumerate(rows[:15]):
        cells = _row_cells(row)
        if not cells:
            continue

        if len(cells) == 1 and len(cells[0][1]) > 50:
            continue

        header_cells = [(c, r, n) for c, r, n in cells if _looks_like_header_cell(n, r)]
        if len(header_cells) < 2:
            continue

        if any("ten truong" in n or "gia tri" in n for _, _, n in header_cells):
            continue

        confidence = 0.3 + len(header_cells) * 0.12
        if row_index > 0:
            confidence += 0.05

        data_rows = 0
        for data_row in rows[row_index + 1 : row_index + 6]:
            if _row_cells(data_row):
                data_rows += 1
        if data_rows >= 1:
            confidence += 0.08

        if confidence > best.confidence:
            best = LayoutCandidate(
                ExcelLayoutKind.HORIZONTAL_HEADER,
                min(confidence, 0.92),
                header_row_index=row_index,
            )

    return best


def _score_first_column_labels(rows: Sequence[Sequence[Any]]) -> LayoutCandidate:
    label_rows = 0
    for row in rows[:25]:
        cells = _row_cells(row)
        if not cells:
            continue
        col0_raw = cells[0][1]
        col0_norm = cells[0][2]
        if _is_skip_label(col0_norm, col0_raw):
            continue
        if len(col0_raw) > 120:
            continue
        has_sep = ":" in col0_raw or any(sep in col0_raw for sep in (".", "─"))
        is_short = 2 <= len(col0_raw) < 50 and len(col0_raw.split()) <= 12
        if has_sep or is_short:
            label_rows += 1

    if label_rows < 2:
        return LayoutCandidate(ExcelLayoutKind.FIRST_COLUMN_LABELS, 0.0)

    confidence = 0.28 + min(label_rows * 0.05, 0.45)
    return LayoutCandidate(
        ExcelLayoutKind.FIRST_COLUMN_LABELS,
        min(confidence, 0.75),
        label_col=0,
    )


def classify_excel_structure(rows: Sequence[Sequence[Any]]) -> StructureDetectionResult:
    """Score all layout types and pick the best match."""
    candidates = [
        _score_vertical_kv(rows),
        _score_two_column_kv(rows),
        _score_horizontal_header(rows),
        _score_first_column_labels(rows),
    ]
    candidates.sort(key=lambda c: c.confidence, reverse=True)
    chosen = candidates[0] if candidates and candidates[0].confidence >= 0.4 else LayoutCandidate(
        ExcelLayoutKind.UNKNOWN, 0.0
    )
    return StructureDetectionResult(chosen=chosen, candidates=candidates)


def _build_field(
    *,
    label: str,
    order: int,
    section: str,
    detect_field_type: Callable[[str], str],
    clean_field_label: Callable[[str], str],
) -> FileField | None:
    cleaned = clean_field_label(label)
    norm = normalize_cell_text(cleaned)
    if _is_skip_label(norm, cleaned):
        return None
    name = ascii_slug(cleaned, f"field_{order + 1}")
    return FileField(
        name=name,
        field_type=detect_field_type(cleaned),
        label=cleaned,
        order=order,
        section=section or "general",
    )


def _parse_vertical_kv(
    rows: Sequence[Sequence[Any]],
    candidate: LayoutCandidate,
    *,
    detect_field_type: Callable[[str], str],
    clean_field_label: Callable[[str], str],
) -> list[FileField]:
    layout = candidate.vertical_layout or detect_vertical_form_layout(rows)
    if layout is None:
        return []

    section = (layout.title or candidate.title or "").strip() or "general"
    fields: list[FileField] = []
    order = 0

    for row in rows[layout.header_row_index + 1 :]:
        if order >= 80:
            break
        if layout.label_col >= len(row or ()):
            continue
        raw = row[layout.label_col]
        if raw is None:
            if fields:
                break
            continue
        label = str(raw).strip()
        if not label:
            if fields:
                break
            continue
        field = _build_field(
            label=label,
            order=order,
            section=section,
            detect_field_type=detect_field_type,
            clean_field_label=clean_field_label,
        )
        if field:
            fields.append(field)
            order += 1
    return fields


def _parse_two_column_kv(
    rows: Sequence[Sequence[Any]],
    candidate: LayoutCandidate,
    *,
    detect_field_type: Callable[[str], str],
    clean_field_label: Callable[[str], str],
) -> list[FileField]:
    label_col = candidate.label_col
    value_col = candidate.value_col
    sheet_title = extract_sheet_title(rows, len(rows))
    section = (candidate.title or sheet_title or "").strip() or "general"
    fields: list[FileField] = []
    order = 0

    for row in rows:
        if order >= 80:
            break
        cells = _row_cells(row)
        if len(cells) == 1 and len(cells[0][1]) > 60:
            section = cells[0][1] or section
            continue
        if any(m in c[2] for c in cells for m in _HEADER_LABEL_MARKERS + _HEADER_VALUE_MARKERS):
            continue

        if label_col >= len(row or ()):
            continue
        raw_label = str(row[label_col]).strip() if row[label_col] is not None else ""
        if not raw_label:
            continue

        field = _build_field(
            label=raw_label,
            order=order,
            section=section,
            detect_field_type=detect_field_type,
            clean_field_label=clean_field_label,
        )
        if field:
            fields.append(field)
            order += 1
    return fields


def _parse_horizontal_header(
    rows: Sequence[Sequence[Any]],
    candidate: LayoutCandidate,
    *,
    detect_field_type: Callable[[str], str],
    clean_field_label: Callable[[str], str],
) -> list[FileField]:
    row_index = candidate.header_row_index
    if row_index >= len(rows):
        return []

    header_row = rows[row_index]
    fields: list[FileField] = []
    order = 0

    for col_index, cell in enumerate(header_row or []):
        if cell is None:
            continue
        raw = str(cell).strip()
        if not raw or len(raw) > 100:
            continue
        norm = normalize_cell_text(raw)
        if not _looks_like_header_cell(norm, raw):
            continue
        field = _build_field(
            label=raw,
            order=order,
            section="general",
            detect_field_type=detect_field_type,
            clean_field_label=clean_field_label,
        )
        if field:
            fields.append(field)
            order += 1
    return fields


def _parse_first_column_labels(
    rows: Sequence[Sequence[Any]],
    *,
    detect_field_type: Callable[[str], str],
    clean_field_label: Callable[[str], str],
) -> list[FileField]:
    fields: list[FileField] = []
    order = 0

    for row in rows[:25]:
        if order >= 20:
            break
        if not row or row[0] is None:
            continue
        text = str(row[0]).strip()
        if not text or len(text) > 500:
            continue

        has_separator = any(sep in text for sep in (":", ".", "─", "(", "[", "{"))
        is_short_label = 2 <= len(text) < 50 and len(text.split()) <= 15
        if not (has_separator or is_short_label):
            continue

        field = _build_field(
            label=text,
            order=order,
            section="general",
            detect_field_type=detect_field_type,
            clean_field_label=clean_field_label,
        )
        if field:
            fields.append(field)
            order += 1
    return fields


def parse_excel_rows(
    rows: list[tuple[Any, ...]],
    *,
    detect_field_type: Callable[[str], str],
    clean_field_label: Callable[[str], str],
) -> tuple[list[FileField], dict[str, Any]]:
    """
    Detect Excel structure, parse with the matching algorithm, return fields + metadata.
    """
    detection = classify_excel_structure(rows)
    chosen = detection.chosen
    meta: dict[str, Any] = {
        "excel_layout": chosen.kind.value,
        "excel_layout_confidence": round(chosen.confidence, 3),
        "excel_layout_candidates": [
            {"kind": c.kind.value, "confidence": round(c.confidence, 3)}
            for c in detection.candidates
            if c.confidence > 0
        ],
    }

    fields: list[FileField] = []
    if chosen.kind == ExcelLayoutKind.VERTICAL_KV:
        fields = _parse_vertical_kv(
            rows, chosen, detect_field_type=detect_field_type, clean_field_label=clean_field_label
        )
        if chosen.vertical_layout:
            meta["header_row"] = chosen.vertical_layout.header_row_index + 1
            meta["label_col"] = chosen.vertical_layout.label_col + 1
            meta["value_col"] = chosen.vertical_layout.value_col + 1
    elif chosen.kind == ExcelLayoutKind.TWO_COLUMN_KV:
        fields = _parse_two_column_kv(
            rows, chosen, detect_field_type=detect_field_type, clean_field_label=clean_field_label
        )
        meta["label_col"] = chosen.label_col + 1
        meta["value_col"] = chosen.value_col + 1
    elif chosen.kind == ExcelLayoutKind.HORIZONTAL_HEADER:
        fields = _parse_horizontal_header(
            rows, chosen, detect_field_type=detect_field_type, clean_field_label=clean_field_label
        )
        meta["header_row"] = chosen.header_row_index + 1
    elif chosen.kind == ExcelLayoutKind.FIRST_COLUMN_LABELS:
        fields = _parse_first_column_labels(
            rows, detect_field_type=detect_field_type, clean_field_label=clean_field_label
        )
    else:
        fields = _parse_first_column_labels(
            rows, detect_field_type=detect_field_type, clean_field_label=clean_field_label
        )
        meta["excel_layout"] = ExcelLayoutKind.FIRST_COLUMN_LABELS.value
        meta["excel_layout_confidence"] = 0.0

    title = (
        (chosen.vertical_layout.title if chosen.vertical_layout else "")
        or chosen.title
        or extract_sheet_title(rows, chosen.header_row_index or 0)
    )
    if title:
        meta["document_title"] = title
        meta["title"] = title

    if len(fields) < 2 and chosen.kind != ExcelLayoutKind.UNKNOWN:
        for alt in detection.candidates:
            if alt.kind == chosen.kind or alt.confidence < 0.35:
                continue
            alt_fields = _parse_by_kind(
                rows,
                kind=alt.kind,
                candidate=alt,
                detect_field_type=detect_field_type,
                clean_field_label=clean_field_label,
            )
            if len(alt_fields) >= 2:
                fields = alt_fields
                meta["excel_layout"] = alt.kind.value
                meta["excel_layout_confidence"] = round(alt.confidence, 3)
                meta["excel_layout_fallback"] = True
                break

    meta["fields_count"] = len(fields)
    return fields, meta


def _parse_by_kind(
    rows: list[tuple[Any, ...]],
    *,
    kind: ExcelLayoutKind,
    candidate: LayoutCandidate,
    detect_field_type: Callable[[str], str],
    clean_field_label: Callable[[str], str],
) -> list[FileField]:
    if kind == ExcelLayoutKind.VERTICAL_KV:
        return _parse_vertical_kv(
            rows, candidate, detect_field_type=detect_field_type, clean_field_label=clean_field_label
        )
    if kind == ExcelLayoutKind.TWO_COLUMN_KV:
        return _parse_two_column_kv(
            rows, candidate, detect_field_type=detect_field_type, clean_field_label=clean_field_label
        )
    if kind == ExcelLayoutKind.HORIZONTAL_HEADER:
        return _parse_horizontal_header(
            rows, candidate, detect_field_type=detect_field_type, clean_field_label=clean_field_label
        )
    return _parse_first_column_labels(
        rows, detect_field_type=detect_field_type, clean_field_label=clean_field_label
    )


def rows_from_openpyxl_worksheet(worksheet: Any, *, max_row: int = 80) -> list[tuple[Any, ...]]:
    limit = min(max_row, worksheet.max_row or max_row)
    return [
        tuple(row)
        for row in worksheet.iter_rows(min_row=1, max_row=limit, values_only=True)
    ]


def rows_from_xlrd_sheet(sheet: Any, *, max_row: int = 80) -> list[tuple[Any, ...]]:
    limit = min(max_row, sheet.nrows or 0)
    return [tuple(sheet.row_values(row_idx)) for row_idx in range(limit)]


def sheet_to_text_preview(rows: Sequence[Sequence[Any]], *, max_rows: int = 40) -> str:
    lines: list[str] = []
    for row in rows[:max_rows]:
        cells = [str(c).strip() if c is not None else "" for c in row]
        while cells and not cells[-1]:
            cells.pop()
        if not any(cells):
            continue
        lines.append(" | ".join(cells))
    return "\n".join(lines)
