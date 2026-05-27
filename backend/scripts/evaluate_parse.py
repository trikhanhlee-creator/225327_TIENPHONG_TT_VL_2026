#!/usr/bin/env python3
"""
Compare parse_ground_truth.csv vs parse_predictions.csv and report Precision / Recall / F1.

Usage (from backend/):
  python scripts/evaluate_parse.py \\
    --ground-truth data/parse_eval/parse_ground_truth.csv \\
    --predictions data/parse_eval/parse_predictions.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from collections import defaultdict
from dataclasses import dataclass
from statistics import mean

BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

from app.services.parse_dataset import (  # noqa: E402
    filter_field_maps_by_format,
    form_type_map_from_ground_truth,
    form_type_map_from_metadata,
    parse_skip_formats,
)
from app.services.parse_eval_logger import normalize_field_key  # noqa: E402
from app.services.parse_experiment_run import ParseExperimentRun  # noqa: E402


@dataclass
class Confusion:
    tp: int = 0
    fp: int = 0
    fn: int = 0

    @property
    def precision(self) -> float:
        denom = self.tp + self.fp
        return self.tp / denom if denom else 0.0

    @property
    def recall(self) -> float:
        denom = self.tp + self.fn
        return self.tp / denom if denom else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return (2 * p * r / (p + r)) if (p + r) else 0.0


def _read_csv(path: str) -> list[dict[str, str]]:
    with open(path, encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _load_field_map(
    rows: list[dict[str, str]],
    *,
    strict_type: bool,
) -> tuple[dict[str, set[str]], dict[str, str]]:
    """Return form_id -> field keys and form_id -> form_type (if present)."""
    fields_by_form: dict[str, set[str]] = defaultdict(set)
    form_types: dict[str, str] = {}

    for row in rows:
        form_id = (row.get("form_id") or "").strip()
        field_key = normalize_field_key(row.get("field_key") or row.get("name") or "")
        if not form_id or not field_key:
            continue

        if strict_type:
            field_type = (row.get("field_type") or "text").strip().lower()
            field_key = f"{field_key}::{field_type}"

        fields_by_form[form_id].add(field_key)

        form_type = (row.get("form_type") or row.get("file_type") or "").strip().upper()
        if form_type and form_id not in form_types:
            form_types[form_id] = form_type

    return fields_by_form, form_types


def _compare_maps(
    ground_truth: dict[str, set[str]],
    predictions: dict[str, set[str]],
) -> Confusion:
    stats = Confusion()
    all_forms = set(ground_truth) | set(predictions)
    for form_id in all_forms:
        gt = ground_truth.get(form_id, set())
        pred = predictions.get(form_id, set())
        stats.tp += len(gt & pred)
        stats.fp += len(pred - gt)
        stats.fn += len(gt - pred)
    return stats


def _load_latency(path: str | None) -> dict[str, list[float]]:
    if not path or not os.path.isfile(path):
        return {}
    latencies: dict[str, list[float]] = defaultdict(list)
    for row in _read_csv(path):
        form_id = (row.get("form_id") or "").strip()
        if not form_id:
            continue
        try:
            latencies[form_id].append(float(row.get("latency_ms") or 0))
        except ValueError:
            continue
    return latencies


def _format_pct(value: float) -> str:
    return f"{value * 100:.2f}%"


def evaluate(
    *,
    ground_truth_path: str,
    predictions_path: str,
    latency_path: str | None = None,
    form_metadata_path: str | None = None,
    strict_type: bool = False,
    skip_formats: set[str] | None = None,
) -> dict:
    gt_rows = _read_csv(ground_truth_path)
    pred_rows = _read_csv(predictions_path)

    gt_map, gt_types = _load_field_map(gt_rows, strict_type=strict_type)
    pred_map, pred_types = _load_field_map(pred_rows, strict_type=strict_type)
    meta_types: dict[str, str] = {}
    if form_metadata_path and os.path.isfile(form_metadata_path):
        meta_types = form_type_map_from_metadata(_read_csv(form_metadata_path))
    form_types = {**meta_types, **gt_types, **pred_types}
    if not meta_types and not gt_types:
        form_types = {**form_types, **form_type_map_from_ground_truth(gt_rows)}

    skip = skip_formats or parse_skip_formats()
    if skip:
        gt_map = filter_field_maps_by_format(gt_map, form_types, skip_formats=skip)
        pred_map = filter_field_maps_by_format(pred_map, form_types, skip_formats=skip)

    overall = _compare_maps(gt_map, pred_map)
    per_form: dict[str, dict] = {}
    for form_id in sorted(set(gt_map) | set(pred_map)):
        stats = _compare_maps({form_id: gt_map.get(form_id, set())}, {form_id: pred_map.get(form_id, set())})
        per_form[form_id] = {
            "tp": stats.tp,
            "fp": stats.fp,
            "fn": stats.fn,
            "precision": round(stats.precision, 4),
            "recall": round(stats.recall, 4),
            "f1": round(stats.f1, 4),
            "form_type": form_types.get(form_id, ""),
        }

    by_type: dict[str, dict] = {}
    type_groups: dict[str, list[str]] = defaultdict(list)
    for form_id, form_type in form_types.items():
        if skip and (form_type or "").upper() in skip:
            continue
        type_groups[form_type or "UNKNOWN"].append(form_id)
    for form_type, form_ids in sorted(type_groups.items()):
        gt_subset = {fid: gt_map.get(fid, set()) for fid in form_ids}
        pred_subset = {fid: pred_map.get(fid, set()) for fid in form_ids}
        stats = _compare_maps(gt_subset, pred_subset)
        by_type[form_type] = {
            "forms": len(form_ids),
            "tp": stats.tp,
            "fp": stats.fp,
            "fn": stats.fn,
            "precision": round(stats.precision, 4),
            "recall": round(stats.recall, 4),
            "f1": round(stats.f1, 4),
        }

    latency_map = _load_latency(latency_path)
    if skip:
        latency_map = {
            fid: vals for fid, vals in latency_map.items() if (form_types.get(fid) or "").upper() not in skip
        }
    latency_values = [v for values in latency_map.values() for v in values]
    latency_summary = {
        "samples": len(latency_values),
        "mean_ms": round(mean(latency_values), 2) if latency_values else 0.0,
        "min_ms": round(min(latency_values), 2) if latency_values else 0.0,
        "max_ms": round(max(latency_values), 2) if latency_values else 0.0,
    }

    return {
        "ground_truth": ground_truth_path,
        "predictions": predictions_path,
        "latency_source": latency_path or "",
        "strict_type_match": strict_type,
        "skipped_formats": sorted(skip),
        "overall": {
            "forms": len(set(gt_map) | set(pred_map)),
            "tp": overall.tp,
            "fp": overall.fp,
            "fn": overall.fn,
            "precision": round(overall.precision, 4),
            "recall": round(overall.recall, 4),
            "f1": round(overall.f1, 4),
        },
        "by_form_type": by_type,
        "per_form": per_form,
        "latency": latency_summary,
    }


def _print_report(report: dict) -> None:
    skipped = report.get("skipped_formats") or []
    if skipped:
        print(f"\n(Excluded formats: {', '.join(skipped)})")

    overall = report["overall"]
    print("\n=== Parse evaluation (overall) ===")
    print(f"Forms evaluated : {overall['forms']}")
    print(f"TP / FP / FN    : {overall['tp']} / {overall['fp']} / {overall['fn']}")
    print(f"Precision       : {_format_pct(overall['precision'])}")
    print(f"Recall          : {_format_pct(overall['recall'])}")
    print(f"F1              : {_format_pct(overall['f1'])}")

    print("\n=== By form type ===")
    print(f"{'Form Type':<12} {'Precision':>12} {'Recall':>12} {'F1':>12} {'Forms':>8}")
    for form_type, stats in sorted(report["by_form_type"].items()):
        print(
            f"{form_type:<12} "
            f"{_format_pct(stats['precision']):>12} "
            f"{_format_pct(stats['recall']):>12} "
            f"{_format_pct(stats['f1']):>12} "
            f"{stats['forms']:>8}"
        )

    latency = report.get("latency") or {}
    if latency.get("samples"):
        print("\n=== Parse latency ===")
        print(f"Samples : {latency['samples']}")
        print(f"Mean    : {latency['mean_ms']} ms")
        print(f"Min/Max : {latency['min_ms']} / {latency['max_ms']} ms")


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate parse predictions against ground truth.")
    parser.add_argument(
        "--dataset-dir",
        default="",
        help="Dataset folder with parse_ground_truth.csv and form_metadata.csv",
    )
    parser.add_argument(
        "--ground-truth",
        default="",
        help="Path to parse_ground_truth.csv (overrides --dataset-dir)",
    )
    parser.add_argument(
        "--form-metadata",
        default="",
        help="Path to form_metadata.csv (overrides --dataset-dir)",
    )
    parser.add_argument(
        "--run-dir",
        default="",
        help="Experiment run folder (reads/writes parse_metrics.json, summary_by_format.csv)",
    )
    parser.add_argument(
        "--predictions",
        default="",
        help="Path to parse_predictions.csv (default: run-dir or legacy parse_eval/)",
    )
    parser.add_argument(
        "--latency",
        default="",
        help="Path to parse_latency.csv (optional)",
    )
    parser.add_argument(
        "--output-json",
        default="",
        help="Optional path to write full metrics JSON (default: run-dir/parse_metrics.json)",
    )
    parser.add_argument(
        "--strict-type",
        action="store_true",
        help="Count TP only when field_key AND field_type both match",
    )
    parser.add_argument(
        "--skip-formats",
        default="",
        help="Comma-separated formats to exclude (default: PARSE_EVAL_SKIP_FORMATS, usually pdf)",
    )
    args = parser.parse_args()
    skip_formats = parse_skip_formats(args.skip_formats or None)

    dataset_dir = os.path.abspath(args.dataset_dir) if args.dataset_dir else ""
    ground_truth_path = args.ground_truth
    form_metadata_path = args.form_metadata
    if dataset_dir:
        from app.services.parse_dataset import dataset_paths  # noqa: E402

        paths = dataset_paths(dataset_dir)
        if not ground_truth_path:
            ground_truth_path = paths["ground_truth"]
        if not form_metadata_path:
            form_metadata_path = paths["form_metadata"]
    if not ground_truth_path:
        ground_truth_path = os.path.join(BACKEND_ROOT, "data", "parse_eval", "parse_ground_truth.csv")

    experiment: ParseExperimentRun | None = None
    if args.run_dir:
        experiment = ParseExperimentRun.open_existing(args.run_dir)

    predictions_path = args.predictions
    latency_path = args.latency
    output_json = args.output_json
    if experiment:
        if not predictions_path:
            predictions_path = experiment.predictions_csv
        if not latency_path:
            latency_path = experiment.latency_csv
        if not output_json:
            output_json = experiment.metrics_json
    if not predictions_path:
        predictions_path = os.path.join(BACKEND_ROOT, "data", "parse_eval", "parse_predictions.csv")
    if not latency_path:
        legacy_latency = os.path.join(BACKEND_ROOT, "data", "parse_eval", "parse_latency.csv")
        latency_path = legacy_latency

    if not os.path.isfile(ground_truth_path):
        print(f"Missing ground truth file: {ground_truth_path}", file=sys.stderr)
        return 1
    if not os.path.isfile(predictions_path):
        print(f"Missing predictions file: {predictions_path}", file=sys.stderr)
        return 1

    latency_path = latency_path if os.path.isfile(latency_path) else None
    if not form_metadata_path or not os.path.isfile(form_metadata_path):
        form_metadata_path = None

    report = evaluate(
        ground_truth_path=ground_truth_path,
        predictions_path=predictions_path,
        latency_path=latency_path,
        form_metadata_path=form_metadata_path,
        strict_type=args.strict_type,
        skip_formats=skip_formats,
    )
    if experiment:
        report["run_id"] = experiment.run_id
        report["run_dir"] = experiment.run_dir
    _print_report(report)

    if output_json:
        os.makedirs(os.path.dirname(os.path.abspath(output_json)) or ".", exist_ok=True)
        with open(output_json, "w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
        print(f"\nSaved metrics JSON: {output_json}")

    if experiment:
        exported = experiment.export_evaluation(report)
        print(f"Saved summary CSV: {exported['summary_csv']}")
        print(f"Run manifest: {experiment.manifest_path}")
        if exported.get("readme_log"):
            print(f"Updated experiment log: {exported['readme_log']}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
