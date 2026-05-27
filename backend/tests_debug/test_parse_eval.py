"""Unit tests for parse experiment metrics."""

import os
import sys
import tempfile

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, backend_dir)

from scripts.evaluate_parse import evaluate  # noqa: E402
from app.services.parse_eval_logger import (  # noqa: E402
    fields_to_eval_rows,
    normalize_field_key,
    save_parse_result_to_csv,
)


def test_normalize_field_key():
    assert normalize_field_key("Full Name") == "full_name"
    assert normalize_field_key("  student id ") == "student_id"


def test_fields_to_eval_rows_dedupes():
    rows = fields_to_eval_rows(
        form_id="F001",
        fields=[
            {"name": "full_name", "field_type": "text"},
            {"name": "full_name", "field_type": "text"},
            {"name": "student_id", "field_type": "text"},
        ],
    )
    assert len(rows) == 2
    assert rows[0]["field_key"] == "full_name"


def test_evaluate_precision_recall_f1():
    with tempfile.TemporaryDirectory() as tmp:
        gt_path = os.path.join(tmp, "gt.csv")
        pred_path = os.path.join(tmp, "pred.csv")
        with open(gt_path, "w", encoding="utf-8", newline="") as handle:
            handle.write("form_id,field_key,field_type,form_type\n")
            handle.write("F001,full_name,text,DOCX\n")
            handle.write("F001,student_id,text,DOCX\n")
            handle.write("F001,birth_date,date,DOCX\n")
        with open(pred_path, "w", encoding="utf-8", newline="") as handle:
            handle.write("form_id,field_key,field_type,source_file,file_type\n")
            handle.write("F001,full_name,text,a.docx,DOCX\n")
            handle.write("F001,student_id,text,a.docx,DOCX\n")
            handle.write("F001,nickname,text,a.docx,DOCX\n")

        report = evaluate(ground_truth_path=gt_path, predictions_path=pred_path)
        overall = report["overall"]
        assert overall["tp"] == 2
        assert overall["fp"] == 1
        assert overall["fn"] == 1
        assert round(overall["precision"], 2) == 0.67
        assert round(overall["recall"], 2) == 0.67
        assert round(overall["f1"], 2) == 0.67


def test_experiment_run_creates_files(tmp_path, monkeypatch):
    import app.core.config as config_module
    from app.services.parse_experiment_run import ParseExperimentRun

    exp_root = tmp_path / "experiments"
    monkeypatch.setattr(config_module.settings, "PARSE_EXPERIMENTS_DIR", str(exp_root))
    run = ParseExperimentRun.create(name="test_run", dataset_dir="/data/sample")
    assert os.path.isdir(run.run_dir)
    assert os.path.isfile(run.manifest_path)
    assert run.predictions_csv.endswith("parse_predictions.csv")


def test_save_parse_result_to_csv(tmp_path, monkeypatch):
    import app.core.config as config_module

    monkeypatch.setattr(config_module.settings, "PARSE_EVAL_LOG_ENABLED", True)
    monkeypatch.setattr(config_module.settings, "PARSE_PREDICTIONS_CSV", str(tmp_path / "pred.csv"))

    rows = fields_to_eval_rows(form_id="F002", fields=[{"name": "email", "field_type": "email"}])
    path = save_parse_result_to_csv(rows, append=False)
    assert path and os.path.isfile(path)
    content = (tmp_path / "pred.csv").read_text(encoding="utf-8")
    assert "F002" in content
    assert "email" in content
