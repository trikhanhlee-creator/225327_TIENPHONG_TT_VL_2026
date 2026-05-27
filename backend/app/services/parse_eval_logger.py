"""
Logging parse output for reproducible parse experiments (ground truth vs predictions).
"""

from __future__ import annotations

import csv
import os
from datetime import datetime, timezone
from typing import Any, Iterable

from app.core.config import settings

PREDICTIONS_HEADER = ("form_id", "field_key", "field_type", "source_file", "file_type")
LATENCY_HEADER = ("form_id", "latency_ms", "source_file", "file_type")


def normalize_field_key(raw: str) -> str:
    key = (raw or "").strip().lower()
    key = key.replace(" ", "_")
    while "__" in key:
        key = key.replace("__", "_")
    return key.strip("_")


def _normalize_field_type(raw: str) -> str:
    return (raw or "text").strip().lower() or "text"


def field_record_to_row(
    *,
    form_id: str,
    field: dict[str, Any] | Any,
    source_file: str = "",
    file_type: str = "",
) -> dict[str, str]:
    if isinstance(field, dict):
        payload = field
    elif hasattr(field, "to_dict"):
        payload = field.to_dict()
    else:
        payload = {}

    field_key = normalize_field_key(
        str(payload.get("field_key") or payload.get("name") or "")
    )
    return {
        "form_id": str(form_id).strip(),
        "field_key": field_key,
        "field_type": _normalize_field_type(str(payload.get("field_type") or "text")),
        "source_file": source_file,
        "file_type": file_type,
    }


def fields_to_eval_rows(
    *,
    form_id: str,
    fields: Iterable[Any],
    source_file: str = "",
    file_type: str = "",
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for field in fields or []:
        row = field_record_to_row(
            form_id=form_id,
            field=field,
            source_file=source_file,
            file_type=file_type,
        )
        if not row["field_key"]:
            continue
        dedupe_key = (row["form_id"], row["field_key"])
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        rows.append(row)
    return rows


def _resolve_eval_dir() -> str:
    base = getattr(settings, "PARSE_EVAL_DIR", "data/parse_eval")
    if not os.path.isabs(base):
        backend_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        base = os.path.join(backend_root, base)
    os.makedirs(base, exist_ok=True)
    return base


def predictions_csv_path() -> str:
    custom = getattr(settings, "PARSE_PREDICTIONS_CSV", "") or ""
    if custom:
        return os.path.abspath(custom)
    return os.path.join(_resolve_eval_dir(), "parse_predictions.csv")


def latency_csv_path() -> str:
    custom = getattr(settings, "PARSE_LATENCY_CSV", "") or ""
    if custom:
        return os.path.abspath(custom)
    return os.path.join(_resolve_eval_dir(), "parse_latency.csv")


def _write_csv_rows(
    *,
    csv_path: str,
    header: tuple[str, ...],
    rows: list[dict[str, str]],
    append: bool,
) -> str:
    os.makedirs(os.path.dirname(csv_path) or ".", exist_ok=True)
    file_exists = os.path.isfile(csv_path) and os.path.getsize(csv_path) > 0
    mode = "a" if append and file_exists else "w"
    with open(csv_path, mode, encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(header))
        if mode == "w" or not file_exists:
            writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in header})
    return csv_path


def save_parse_result_to_csv(
    rows: list[dict[str, str]],
    *,
    csv_path: str | None = None,
    append: bool = True,
) -> str | None:
    if not rows:
        return None
    if not getattr(settings, "PARSE_EVAL_LOG_ENABLED", True):
        return None
    target = csv_path or predictions_csv_path()
    return _write_csv_rows(
        csv_path=target,
        header=PREDICTIONS_HEADER,
        rows=rows,
        append=append,
    )


def log_parse_latency(
    *,
    form_id: str,
    latency_ms: float,
    source_file: str = "",
    file_type: str = "",
    csv_path: str | None = None,
    append: bool = True,
) -> str | None:
    if not getattr(settings, "PARSE_EVAL_LOG_ENABLED", True):
        return None
    row = {
        "form_id": str(form_id).strip(),
        "latency_ms": f"{latency_ms:.2f}",
        "source_file": source_file,
        "file_type": file_type,
    }
    target = csv_path or latency_csv_path()
    return _write_csv_rows(
        csv_path=target,
        header=LATENCY_HEADER,
        rows=[row],
        append=append,
    )


def log_parse_run(
    *,
    form_id: str,
    fields: Iterable[Any],
    latency_ms: float,
    source_file: str = "",
    file_type: str = "",
    append: bool = True,
    predictions_csv: str | None = None,
    latency_csv: str | None = None,
) -> dict[str, str | None]:
    rows = fields_to_eval_rows(
        form_id=form_id,
        fields=fields,
        source_file=source_file,
        file_type=file_type,
    )
    predictions_path = save_parse_result_to_csv(
        rows, csv_path=predictions_csv, append=append
    )
    latency_path = log_parse_latency(
        form_id=form_id,
        latency_ms=latency_ms,
        source_file=source_file,
        file_type=file_type,
        csv_path=latency_csv,
        append=append,
    )
    return {
        "predictions_csv": predictions_path,
        "latency_csv": latency_path,
        "fields_logged": str(len(rows)),
        "logged_at": datetime.now(timezone.utc).isoformat(),
    }
