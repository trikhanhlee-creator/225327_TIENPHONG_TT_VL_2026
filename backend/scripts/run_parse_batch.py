#!/usr/bin/env python3
"""
Batch-parse forms using standard dataset files:
  form_metadata.csv, parse_ground_truth.csv, README.md

Usage (from backend/):
  python scripts/run_parse_batch.py --dataset-dir path/to/parse_dataset
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time

BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

from app.core.config import settings  # noqa: E402
from app.services.doc_converter import DocConversionError, ensure_docx_for_processing  # noqa: E402
from app.services.file_parser import FileParserFactory  # noqa: E402
from app.services.parse_dataset import (  # noqa: E402
    filter_items_by_format,
    load_form_metadata,
    parse_skip_formats,
    validate_dataset_layout,
)
from app.services.parse_eval_logger import log_parse_run  # noqa: E402
from app.services.parse_experiment_run import ParseExperimentRun  # noqa: E402

try:
    from app.api.routes.word import _parse_word_file_fields
except Exception:
    _parse_word_file_fields = None  # type: ignore


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


def _discover_forms_fallback(dataset_dir: str) -> list[dict[str, str]]:
    """Fallback when form_metadata.csv is missing: scan forms/ folder."""
    forms_dir = os.path.join(dataset_dir, "forms")
    scan_dir = forms_dir if os.path.isdir(forms_dir) else dataset_dir
    items = []
    for name in sorted(os.listdir(scan_dir)):
        file_path = os.path.join(scan_dir, name)
        if not os.path.isfile(file_path):
            continue
        if not FileParserFactory.is_supported(name):
            continue
        stem, ext = os.path.splitext(name)
        form_id = stem.split("_", 1)[0] if "_" in stem else stem
        items.append(
            {
                "form_id": form_id,
                "file_path": file_path,
                "file_type": _ext_to_form_type(ext),
                "source_file": name,
            }
        )
    return items


def _discover_forms(dataset_dir: str) -> list[dict[str, str]]:
    items = load_form_metadata(dataset_dir)
    if items:
        return items
    return _discover_forms_fallback(dataset_dir)


async def _parse_one(file_path: str, file_ext: str, original_filename: str):
    if file_ext in (".doc", ".docx") and _parse_word_file_fields is not None:
        return await _parse_word_file_fields(
            file_path=file_path,
            file_ext=file_ext,
            original_filename=original_filename,
        )
    parser = FileParserFactory.create_parser(file_path)
    fields = parser.parse()
    metadata = parser.get_metadata()
    return fields, metadata, {}


async def run_batch(
    dataset_dir: str,
    *,
    reset_logs: bool,
    skip_formats: set[str],
    experiment: ParseExperimentRun | None = None,
) -> int:
    missing = validate_dataset_layout(dataset_dir)
    metadata_items = load_form_metadata(dataset_dir)
    if metadata_items:
        missing = [m for m in missing if m != "form_metadata.csv"]
    if missing and not metadata_items:
        print("Dataset warnings (missing):", ", ".join(missing), file=sys.stderr)

    items = _discover_forms(dataset_dir)
    items, skipped_ids = filter_items_by_format(items, skip_formats=skip_formats)
    if skipped_ids:
        print(f"Skipping formats {sorted(skip_formats)}: {', '.join(skipped_ids)}")
    if not items:
        print(f"No supported forms found under: {dataset_dir}", file=sys.stderr)
        print("Expected form_metadata.csv + forms/ or supported files in dataset root.", file=sys.stderr)
        return 1

    if experiment is None:
        experiment = ParseExperimentRun.create(
            dataset_dir=dataset_dir,
            skip_formats=sorted(skip_formats),
        )
    pred_path = experiment.predictions_csv
    lat_path = experiment.latency_csv
    if reset_logs:
        for path in (pred_path, lat_path):
            if os.path.isfile(path):
                os.remove(path)

    experiment.update_manifest(forms_total=len(items), status="parsing")
    print(f"Experiment run : {experiment.run_dir}")
    print(f"Parsing {len(items)} form(s). Predictions -> {pred_path}")
    ok = 0
    for item in items:
        form_id = item["form_id"]
        file_path = item["file_path"]
        file_type = item["file_type"]
        original_filename = os.path.basename(file_path)
        file_ext = os.path.splitext(file_path)[1].lower()

        working_path = file_path
        if file_ext == ".doc":
            try:
                working_path = ensure_docx_for_processing(file_path)
                file_ext = ".docx"
            except DocConversionError as exc:
                print(f"[SKIP] {form_id}: {exc}")
                continue

        started = time.perf_counter()
        try:
            fields, _, _ = await _parse_one(working_path, file_ext, original_filename)
        except Exception as exc:
            print(f"[FAIL] {form_id} ({original_filename}): {exc}")
            continue
        latency_ms = (time.perf_counter() - started) * 1000.0

        log_parse_run(
            form_id=form_id,
            fields=fields,
            latency_ms=latency_ms,
            source_file=original_filename,
            file_type=file_type,
            append=True,
            predictions_csv=pred_path,
            latency_csv=lat_path,
        )
        ok += 1
        print(f"[OK] {form_id}: {len(fields)} fields, {latency_ms:.0f} ms")

    experiment.mark_completed(forms_parsed=ok, forms_total=len(items))
    print(f"\nDone: {ok}/{len(items)} parsed.")
    from app.services.parse_dataset import dataset_paths  # noqa: E402

    gt_path = dataset_paths(dataset_dir)["ground_truth"]
    print(f"Next: python scripts/evaluate_parse.py --dataset-dir \"{dataset_dir}\" --run-dir \"{experiment.run_dir}\"")
    if os.path.isfile(gt_path):
        print(f"       (ground truth: {gt_path})")
    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Batch parse dataset forms and log predictions.")
    parser.add_argument("--dataset-dir", required=True, help="Folder containing forms/ and CSV ground truth")
    parser.add_argument(
        "--reset-logs",
        action="store_true",
        help="Delete existing parse_predictions.csv and parse_latency.csv before run",
    )
    parser.add_argument(
        "--skip-formats",
        default="",
        help="Comma-separated formats to skip (default: PARSE_EVAL_SKIP_FORMATS, usually pdf)",
    )
    parser.add_argument(
        "--name",
        default="",
        help="Short label for experiment folder (e.g. baseline)",
    )
    parser.add_argument(
        "--run-dir",
        default="",
        help="Reuse existing experiment folder (skip creating new run)",
    )
    parser.add_argument(
        "--notes",
        default="",
        help="Notes saved in run_manifest.json",
    )
    args = parser.parse_args()
    skip_formats = parse_skip_formats(args.skip_formats or None)
    dataset_dir = os.path.abspath(args.dataset_dir)
    if not os.path.isdir(dataset_dir):
        print(f"Dataset directory not found: {dataset_dir}", file=sys.stderr)
        return 1
    if not settings.PARSE_EVAL_LOG_ENABLED:
        print("Warning: PARSE_EVAL_LOG_ENABLED=false — CSV logging is disabled.", file=sys.stderr)

    if args.run_dir:
        experiment = ParseExperimentRun.open_existing(args.run_dir)
    else:
        experiment = ParseExperimentRun.create(
            name=args.name or None,
            dataset_dir=dataset_dir,
            skip_formats=sorted(skip_formats),
            notes=args.notes,
        )

    return asyncio.run(
        run_batch(
            dataset_dir,
            reset_logs=args.reset_logs,
            skip_formats=skip_formats,
            experiment=experiment,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
