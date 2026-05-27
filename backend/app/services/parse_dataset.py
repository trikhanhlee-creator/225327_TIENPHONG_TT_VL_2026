"""
Load standard parse experiment dataset files:
  - form_metadata.csv
  - parse_ground_truth.csv
  - README.md (documentation only)
"""

from __future__ import annotations

import csv
import os
from typing import Iterable

from app.services.parse_eval_logger import normalize_field_key


def read_csv_rows(path: str) -> list[dict[str, str]]:
    with open(path, encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _first_existing_path(*candidates: str) -> str:
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return candidates[0] if candidates else ""


def dataset_paths(dataset_dir: str) -> dict[str, str]:
    root = os.path.abspath(dataset_dir)
    return {
        "readme": os.path.join(root, "README.md"),
        "form_metadata": _first_existing_path(
            os.path.join(root, "form_metadata.csv"),
            os.path.join(root, "metadata", "form_metadata.csv"),
        ),
        "ground_truth": _first_existing_path(
            os.path.join(root, "parse_ground_truth.csv"),
            os.path.join(root, "ground_truth", "parse_ground_truth.csv"),
        ),
        "forms_dir": os.path.join(root, "forms"),
    }


def validate_dataset_layout(dataset_dir: str) -> list[str]:
    """Return list of missing required paths (empty = OK)."""
    paths = dataset_paths(dataset_dir)
    missing: list[str] = []
    if not os.path.isfile(paths["form_metadata"]):
        missing.append("form_metadata.csv (root or metadata/)")
    if not os.path.isfile(paths["ground_truth"]):
        missing.append("parse_ground_truth.csv (root or ground_truth/)")
    if not os.path.isdir(paths["forms_dir"]) and not _has_supported_files(dataset_dir):
        missing.append("forms/ (folder with DOCX/PDF/XLSX files)")
    return missing


def _has_supported_files(dataset_dir: str) -> bool:
    from app.services.file_parser import FileParserFactory

    for name in os.listdir(dataset_dir):
        if FileParserFactory.is_supported(name):
            return True
    return False


_FORMAT_FILE_TYPES = frozenset({"doc", "docx", "pdf", "xlsx", "xls", "csv", "txt"})


def format_type_from_row(row: dict[str, str], *, fallback_ext: str = "") -> str:
    """DOCX / PDF / XLSX from file_type column or file extension."""
    ft = (row.get("file_type") or "").strip().lower()
    if ft == "doc":
        return "DOCX"
    if ft in _FORMAT_FILE_TYPES:
        if ft == "xls":
            return "XLSX"
        return ft.upper()
    if fallback_ext:
        return _ext_to_form_type(fallback_ext)
    file_name = (row.get("file_name") or row.get("filename") or "").strip()
    if file_name:
        return _ext_to_form_type(os.path.splitext(file_name)[1])
    return ""


def _resolve_form_file(dataset_dir: str, row: dict[str, str]) -> str | None:
    forms_dir = os.path.join(dataset_dir, "forms")
    file_type_dir = (row.get("file_type") or "").strip().lower()
    candidates = [
        (row.get("file_path") or "").strip(),
        (row.get("filepath") or "").strip(),
        (row.get("file_name") or "").strip(),
        (row.get("filename") or "").strip(),
        (row.get("file") or "").strip(),
    ]
    search_bases: list[str] = []
    if file_type_dir in _FORMAT_FILE_TYPES:
        search_bases.append(os.path.join(forms_dir, file_type_dir))
    for sub in ("docx", "pdf", "xlsx", "doc"):
        search_bases.append(os.path.join(forms_dir, sub))
    search_bases.extend([forms_dir, dataset_dir])

    for raw in candidates:
        if not raw:
            continue
        if os.path.isabs(raw) and os.path.isfile(raw):
            return raw
        for base in search_bases:
            path = os.path.join(base, raw)
            if os.path.isfile(path):
                return path
    return None


def _ext_to_form_type(ext: str) -> str:
    mapping = {
        ".doc": "DOCX",
        ".docx": "DOCX",
        ".pdf": "PDF",
        ".xlsx": "XLSX",
        ".xls": "XLSX",
        ".csv": "CSV",
        ".txt": "TXT",
    }
    return mapping.get(ext.lower(), ext.lstrip(".").upper() or "UNKNOWN")


def load_form_metadata(dataset_dir: str) -> list[dict[str, str]]:
    """
    Read form_metadata.csv. Required columns: form_id + file reference (filename/file_path).
    Optional: form_type (DOCX|PDF|XLSX), title, description.
    Falls back to legacy forms_manifest.csv if form_metadata.csv is absent.
    """
    paths = dataset_paths(dataset_dir)
    manifest_path = paths["form_metadata"]
    if not os.path.isfile(manifest_path):
        legacy = os.path.join(dataset_dir, "forms_manifest.csv")
        if os.path.isfile(legacy):
            manifest_path = legacy
        else:
            return []

    rows = read_csv_rows(manifest_path)
    items: list[dict[str, str]] = []
    for row in rows:
        form_id = (row.get("form_id") or "").strip()
        file_path = _resolve_form_file(dataset_dir, row)
        if not form_id or not file_path:
            continue
        ext = os.path.splitext(file_path)[1].lower()
        format_type = format_type_from_row(row, fallback_ext=ext)
        items.append(
            {
                "form_id": form_id,
                "file_path": file_path,
                "file_type": format_type,
                "form_category": (row.get("form_type") or "").strip(),
                "title": (row.get("title") or row.get("form_title") or "").strip(),
                "source_file": os.path.basename(file_path),
            }
        )
    return items


def load_ground_truth_rows(dataset_dir: str) -> list[dict[str, str]]:
    path = dataset_paths(dataset_dir)["ground_truth"]
    if not os.path.isfile(path):
        return []
    return read_csv_rows(path)


def ground_truth_field_map(
    rows: Iterable[dict[str, str]],
    *,
    strict_type: bool = False,
) -> dict[str, set[str]]:
    fields_by_form: dict[str, set[str]] = {}
    for row in rows:
        form_id = (row.get("form_id") or "").strip()
        field_key = normalize_field_key(row.get("field_key") or row.get("name") or "")
        if not form_id or not field_key:
            continue
        if strict_type:
            field_type = (row.get("field_type") or "text").strip().lower()
            field_key = f"{field_key}::{field_type}"
        fields_by_form.setdefault(form_id, set()).add(field_key)
    return fields_by_form


def form_type_map_from_metadata(metadata_rows: Iterable[dict[str, str]]) -> dict[str, str]:
    """Map form_id -> file format (DOCX / PDF / XLSX) for metric grouping."""
    out: dict[str, str] = {}
    for row in metadata_rows:
        form_id = (row.get("form_id") or "").strip()
        format_type = format_type_from_row(row)
        if form_id and format_type:
            out[form_id] = format_type
    return out


def parse_skip_formats(raw: str | None = None) -> set[str]:
    """Uppercase format codes to exclude (e.g. PDF)."""
    from app.core.config import settings

    text = (raw if raw is not None else settings.PARSE_EVAL_SKIP_FORMATS) or ""
    return {part.strip().upper() for part in text.split(",") if part.strip()}


def filter_items_by_format(
    items: list[dict[str, str]],
    *,
    skip_formats: set[str] | None = None,
) -> tuple[list[dict[str, str]], list[str]]:
    """Drop forms whose file_type is in skip_formats. Returns (kept, skipped_form_ids)."""
    skip = skip_formats or parse_skip_formats()
    if not skip:
        return items, []
    kept: list[dict[str, str]] = []
    skipped: list[str] = []
    for item in items:
        fmt = (item.get("file_type") or "").strip().upper()
        if fmt in skip:
            skipped.append(item.get("form_id") or "")
            continue
        kept.append(item)
    return kept, [s for s in skipped if s]


def filter_field_maps_by_format(
    field_map: dict[str, set[str]],
    form_types: dict[str, str],
    *,
    skip_formats: set[str] | None = None,
) -> dict[str, set[str]]:
    skip = skip_formats or parse_skip_formats()
    if not skip:
        return field_map
    return {
        form_id: fields
        for form_id, fields in field_map.items()
        if (form_types.get(form_id) or "").upper() not in skip
    }


def form_type_map_from_ground_truth(gt_rows: Iterable[dict[str, str]]) -> dict[str, str]:
    out: dict[str, str] = {}
    for row in gt_rows:
        form_id = (row.get("form_id") or "").strip()
        format_type = format_type_from_row(row)
        if form_id and format_type and form_id not in out:
            out[form_id] = format_type
    return out
