#!/usr/bin/env python3
"""
Chạy full pipeline parse EXP: batch parse → evaluate → một thư mục run riêng.

Usage (from backend/):
  python scripts/run_parse_experiment.py --dataset-dir path/to/parse_form_dataset --name baseline
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import os
import sys

BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)


def _load_script_module(script_name: str):
    path = os.path.join(BACKEND_ROOT, "scripts", f"{script_name}.py")
    spec = importlib.util.spec_from_file_location(script_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load script: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def main_async() -> int:
    parser = argparse.ArgumentParser(description="Run parse experiment (batch + evaluate) in one folder.")
    parser.add_argument("--dataset-dir", required=True)
    parser.add_argument("--name", default="", help="Short label (e.g. baseline, v2)")
    parser.add_argument("--notes", default="")
    parser.add_argument("--skip-formats", default="")
    parser.add_argument("--strict-type", action="store_true")
    args = parser.parse_args()

    dataset_dir = os.path.abspath(args.dataset_dir)
    run_parse_batch = _load_script_module("run_parse_batch")
    evaluate_parse = _load_script_module("evaluate_parse")
    from app.services.parse_dataset import parse_skip_formats  # noqa: E402
    from app.services.parse_experiment_run import ParseExperimentRun  # noqa: E402

    skip_formats = parse_skip_formats(args.skip_formats or None)
    experiment = ParseExperimentRun.create(
        name=args.name or None,
        dataset_dir=dataset_dir,
        skip_formats=sorted(skip_formats),
        notes=args.notes,
    )

    code = await run_parse_batch.run_batch(
        dataset_dir,
        reset_logs=True,
        skip_formats=skip_formats,
        experiment=experiment,
    )
    if code != 0:
        experiment.update_manifest(status="failed")
        return code

    from app.services.parse_dataset import dataset_paths  # noqa: E402

    paths = dataset_paths(dataset_dir)
    report = evaluate_parse.evaluate(
        ground_truth_path=paths["ground_truth"],
        predictions_path=experiment.predictions_csv,
        latency_path=experiment.latency_csv,
        form_metadata_path=paths["form_metadata"],
        strict_type=args.strict_type,
        skip_formats=skip_formats,
    )
    report["run_id"] = experiment.run_id
    report["run_dir"] = experiment.run_dir
    evaluate_parse._print_report(report)
    exported = experiment.export_evaluation(report)
    print("\nResults saved:")
    for key in ("metrics_json", "summary_csv", "readme_log"):
        if exported.get(key):
            print(f"  {exported[key]}")
    print(f"  {experiment.predictions_csv}")
    print(f"  {experiment.latency_csv}")
    print(f"  {experiment.manifest_path}")
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
