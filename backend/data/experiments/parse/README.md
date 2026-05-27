# Thư mục kết quả thực nghiệm Parse

Mỗi lần chạy EXP tạo **một thư mục con**:

```
data/experiments/parse/
  20260525_143022_baseline/
    run_manifest.json
    parse_predictions.csv
    parse_latency.csv
    parse_metrics.json
    summary_by_format.csv
```

## Chạy nhanh

```powershell
cd backend
python scripts/run_parse_experiment.py --dataset-dir "C:\path\to\parse_form_dataset" --name baseline
```

Hoặc tách bước:

```powershell
python scripts/run_parse_batch.py --dataset-dir ... --name baseline
python scripts/evaluate_parse.py --dataset-dir ... --run-dir data/experiments/parse/<run_id>
```
