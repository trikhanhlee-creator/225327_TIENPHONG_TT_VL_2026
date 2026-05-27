"""
Mỗi lần thực nghiệm parse → một thư mục riêng dưới data/experiments/parse/.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.core.config import settings

EXP_RESULTS_START = "<!-- EXP_RESULTS_START -->"
EXP_RESULTS_END = "<!-- EXP_RESULTS_END -->"
EXP_RESULTS_TABLE_HEADER = (
    "| Run ID | Thời gian (UTC) | Dataset | Bỏ qua | Forms | TP | FP | FN "
    "| Precision | Recall | F1 | Latency μ (ms) | Thư mục kết quả |\n"
    "|--------|-----------------|---------|--------|-------|----|----|-----|"
    "|-----------|--------|-----|----------------|-----------------|"
)


def parse_eval_readme_path() -> str:
    backend_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    return os.path.join(backend_root, "data", "parse_eval", "README.md")


def _format_pct(value: float | int | str) -> str:
    try:
        num = float(value)
    except (TypeError, ValueError):
        return str(value)
    return f"{num * 100:.2f}%"


def _build_results_table_row(report: dict[str, Any], run: ParseExperimentRun) -> str:
    overall = report.get("overall") or {}
    latency = report.get("latency") or {}
    skipped = ", ".join(report.get("skipped_formats") or run.manifest.get("skip_formats") or [])
    dataset = run.manifest.get("dataset_dir") or report.get("ground_truth", "")
    if len(dataset) > 56:
        dataset = "…" + dataset[-53:]
    created = (run.manifest.get("created_at") or "")[:19].replace("T", " ")
    return (
        f"| `{run.run_id}` | {created} | {dataset} | {skipped or '—'} "
        f"| {overall.get('forms', '')} | {overall.get('tp', '')} | {overall.get('fp', '')} | {overall.get('fn', '')} "
        f"| {_format_pct(overall.get('precision', 0))} | {_format_pct(overall.get('recall', 0))} "
        f"| {_format_pct(overall.get('f1', 0))} | {latency.get('mean_ms', '—')} "
        f"| `data/experiments/parse/{run.run_id}/` |"
    )


def append_experiment_results_to_readme(report: dict[str, Any], run: ParseExperimentRun) -> str:
    """Append one result row to data/parse_eval/README.md (between HTML markers)."""
    readme_path = parse_eval_readme_path()
    if not os.path.isfile(readme_path):
        return readme_path

    with open(readme_path, encoding="utf-8") as handle:
        content = handle.read()

    new_row = _build_results_table_row(report, run)
    if EXP_RESULTS_START not in content or EXP_RESULTS_END not in content:
        block = (
            "\n## Kết quả thực nghiệm đã chạy\n\n"
            "> Cập nhật tự động sau mỗi lần `evaluate_parse --run-dir` hoặc `run_parse_experiment`.\n\n"
            f"{EXP_RESULTS_START}\n"
            f"{EXP_RESULTS_TABLE_HEADER}\n"
            f"{new_row}\n"
            f"{EXP_RESULTS_END}\n"
        )
        content = content.rstrip() + "\n" + block
    else:
        before, rest = content.split(EXP_RESULTS_START, 1)
        middle, after = rest.split(EXP_RESULTS_END, 1)
        lines = [line for line in middle.strip().splitlines() if line.strip()]
        if not lines or not lines[0].startswith("| Run ID"):
            lines = [EXP_RESULTS_TABLE_HEADER.strip(), new_row]
        else:
            header = lines[0]
            sep = lines[1] if len(lines) > 1 and lines[1].startswith("|---") else ""
            data_lines = [
                ln
                for ln in lines[2 if sep else 1 :]
                if ln.strip() and not ln.strip().startswith("|---")
            ]
            data_lines = [ln for ln in data_lines if f"`{run.run_id}`" not in ln]
            data_lines.append(new_row)
            lines = [header]
            if sep:
                lines.append(sep)
            lines.extend(data_lines)
        middle = "\n" + "\n".join(lines) + "\n"
        content = before + EXP_RESULTS_START + middle + EXP_RESULTS_END + after

    with open(readme_path, "w", encoding="utf-8") as handle:
        handle.write(content)
    return readme_path


def experiments_root_dir() -> str:
    base = getattr(settings, "PARSE_EXPERIMENTS_DIR", "data/experiments/parse")
    if not os.path.isabs(base):
        backend_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        base = os.path.join(backend_root, base)
    os.makedirs(base, exist_ok=True)
    return base


def _slugify(name: str) -> str:
    text = (name or "").strip().lower()
    text = re.sub(r"[^\w\-]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text[:48] or "run"


def build_run_id(name: str | None = None) -> str:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if name:
        return f"{stamp}_{_slugify(name)}"
    return f"{stamp}_parse"


@dataclass
class ParseExperimentRun:
    """Paths and metadata for one parse experiment run."""

    run_id: str
    run_dir: str
    manifest_path: str
    predictions_csv: str
    latency_csv: str
    metrics_json: str
    summary_csv: str
    manifest: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        *,
        name: str | None = None,
        dataset_dir: str = "",
        skip_formats: list[str] | None = None,
        notes: str = "",
    ) -> ParseExperimentRun:
        run_id = build_run_id(name)
        run_dir = os.path.join(experiments_root_dir(), run_id)
        os.makedirs(run_dir, exist_ok=True)

        manifest = {
            "run_id": run_id,
            "experiment_type": "parse",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "dataset_dir": os.path.abspath(dataset_dir) if dataset_dir else "",
            "skip_formats": list(skip_formats or []),
            "notes": (notes or "").strip(),
            "status": "started",
            "forms_parsed": 0,
            "forms_total": 0,
        }
        paths = cls(
            run_id=run_id,
            run_dir=run_dir,
            manifest_path=os.path.join(run_dir, "run_manifest.json"),
            predictions_csv=os.path.join(run_dir, "parse_predictions.csv"),
            latency_csv=os.path.join(run_dir, "parse_latency.csv"),
            metrics_json=os.path.join(run_dir, "parse_metrics.json"),
            summary_csv=os.path.join(run_dir, "summary_by_format.csv"),
            manifest=manifest,
        )
        paths.save_manifest()
        return paths

    @classmethod
    def open_existing(cls, run_dir: str) -> ParseExperimentRun:
        root = os.path.abspath(run_dir)
        run_id = os.path.basename(root)
        manifest_path = os.path.join(root, "run_manifest.json")
        manifest: dict[str, Any] = {}
        if os.path.isfile(manifest_path):
            with open(manifest_path, encoding="utf-8") as handle:
                manifest = json.load(handle)
        return cls(
            run_id=run_id,
            run_dir=root,
            manifest_path=manifest_path,
            predictions_csv=os.path.join(root, "parse_predictions.csv"),
            latency_csv=os.path.join(root, "parse_latency.csv"),
            metrics_json=os.path.join(root, "parse_metrics.json"),
            summary_csv=os.path.join(root, "summary_by_format.csv"),
            manifest=manifest,
        )

    def save_manifest(self) -> None:
        with open(self.manifest_path, "w", encoding="utf-8") as handle:
            json.dump(self.manifest, handle, ensure_ascii=False, indent=2)

    def update_manifest(self, **kwargs: Any) -> None:
        self.manifest.update(kwargs)
        self.save_manifest()

    def mark_completed(self, *, forms_parsed: int, forms_total: int) -> None:
        self.update_manifest(
            status="completed",
            completed_at=datetime.now(timezone.utc).isoformat(),
            forms_parsed=forms_parsed,
            forms_total=forms_total,
        )

    def mark_evaluated(self, overall_f1: float) -> None:
        self.update_manifest(
            evaluated_at=datetime.now(timezone.utc).isoformat(),
            overall_f1=round(overall_f1, 4),
        )

    def save_metrics_report(self, report: dict[str, Any]) -> None:
        with open(self.metrics_json, "w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)

    def save_summary_csv(self, report: dict[str, Any]) -> None:
        import csv

        overall = report.get("overall") or {}
        by_type = report.get("by_form_type") or {}
        latency = report.get("latency") or {}
        rows: list[dict[str, str]] = []

        rows.append(
            {
                "scope": "OVERALL",
                "form_type": "ALL",
                "forms": str(overall.get("forms", "")),
                "tp": str(overall.get("tp", "")),
                "fp": str(overall.get("fp", "")),
                "fn": str(overall.get("fn", "")),
                "precision": str(overall.get("precision", "")),
                "recall": str(overall.get("recall", "")),
                "f1": str(overall.get("f1", "")),
                "latency_mean_ms": str(latency.get("mean_ms", "")),
            }
        )
        for form_type, stats in sorted(by_type.items()):
            rows.append(
                {
                    "scope": "BY_FORMAT",
                    "form_type": form_type,
                    "forms": str(stats.get("forms", "")),
                    "tp": str(stats.get("tp", "")),
                    "fp": str(stats.get("fp", "")),
                    "fn": str(stats.get("fn", "")),
                    "precision": str(stats.get("precision", "")),
                    "recall": str(stats.get("recall", "")),
                    "f1": str(stats.get("f1", "")),
                    "latency_mean_ms": "",
                }
            )

        fieldnames = [
            "scope",
            "form_type",
            "forms",
            "tp",
            "fp",
            "fn",
            "precision",
            "recall",
            "f1",
            "latency_mean_ms",
        ]
        with open(self.summary_csv, "w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

    def export_evaluation(self, report: dict[str, Any]) -> dict[str, str]:
        self.save_metrics_report(report)
        self.save_summary_csv(report)
        overall = report.get("overall") or {}
        self.mark_evaluated(float(overall.get("f1") or 0))
        readme_path = append_experiment_results_to_readme(report, self)
        return {
            "metrics_json": self.metrics_json,
            "summary_csv": self.summary_csv,
            "readme_log": readme_path,
        }
