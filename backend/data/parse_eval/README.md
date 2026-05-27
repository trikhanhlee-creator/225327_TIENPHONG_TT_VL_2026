# Dataset thực nghiệm Parse

Bộ tài liệu chuẩn gồm **3 file** (do bạn chuẩn bị):

| File | Vai trò |
|------|---------|
| `README.md` | Mô tả dataset, quy ước đặt tên, cách chạy thực nghiệm |
| `form_metadata.csv` | Danh mục form: `form_id` ↔ file ↔ loại (DOCX/PDF/XLSX) |
| `parse_ground_truth.csv` | Nhãn vàng: field thật + field type thật |

Hệ thống **sinh thêm** khi chạy parse:

| File | Vai trò |
|------|---------|
| `parse_predictions.csv` | Output parser (append mỗi lần parse) |
| `parse_latency.csv` | Thời gian parse (ms) theo `form_id` |

## Cấu trúc thư mục dataset

Hỗ trợ cả layout phẳng và layout có thư mục con (như `parse_form_dataset`):

```
parse_dataset/
  README.md
  form_metadata.csv              # hoặc metadata/form_metadata.csv
  parse_ground_truth.csv         # hoặc ground_truth/parse_ground_truth.csv
  forms/
    docx/ ...
    pdf/  ...
    xlsx/ ...
```

Cột `file_name` (thay `filename`) và `file_type` = docx/pdf/xlsx đều được nhận.

## `form_metadata.csv`

Một dòng = một form.

```csv
form_id,filename,form_type,title
F001,don_xin_viec.docx,DOCX,Đơn xin việc
F002,bang_diem.pdf,PDF,Bảng điểm
```

Cột bắt buộc: `form_id`, `filename` (hoặc `file_path`).

## `parse_ground_truth.csv`

Một dòng = một field trong ground truth.

```csv
form_id,field_key,field_type,form_type
F001,full_name,text,DOCX
F001,student_id,text,DOCX
```

Cột bắt buộc: `form_id`, `field_key`. Khuyến nghị thêm `field_type`, `form_type` (để nhóm metric theo DOCX/PDF/XLSX).

## Loại trừ PDF

Hệ thống chưa hỗ trợ thao tác PDF — mặc định **bỏ qua PDF** trong batch parse và evaluate:

```env
PARSE_EVAL_SKIP_FORMATS=pdf
```

Chỉ đánh giá **DOCX + XLSX** (16 form trong bộ `parse_form_dataset`).

## Thư mục kết quả mỗi lần EXP

`data/experiments/parse/<run_id>/` — chi tiết: `data/experiments/parse/README.md`

## Workflow (reproducible)

```powershell
cd backend

# Một lệnh: parse + evaluate + lưu run folder
python scripts/run_parse_experiment.py --dataset-dir path/to/parse_dataset --name baseline

# Hoặc tách bước:
python scripts/run_parse_batch.py --dataset-dir path/to/parse_dataset --reset-logs --name baseline
python scripts/evaluate_parse.py --dataset-dir path/to/parse_dataset --run-dir data/experiments/parse/<run_id>
```

Upload qua API (từng form, legacy log):

`POST /api/word/upload?eval_form_id=F001` → `data/parse_eval/parse_predictions.csv`

## Metric

| Metric | Ý nghĩa |
|--------|---------|
| Precision | Trong các field parser sinh ra, bao nhiêu % đúng GT |
| Recall | GT có bao nhiêu % được parser phát hiện |
| F1 | Trung bình điều hòa P và R |
| Latency | ms/form (từ `parse_latency.csv`) |

- **TP**: cùng `form_id` + `field_key` trong GT và prediction  
- **FP**: prediction có, GT không có  
- **FN**: GT có, prediction không có  

`--strict-type`: TP chỉ khi khớp cả `field_type`.

## File mẫu trong repo

- `form_metadata.example.csv`
- `parse_ground_truth.example.csv`

Copy vào dataset thật của bạn và chỉnh `forms/` + nội dung CSV cho khớp.

## Kết quả thực nghiệm đã chạy

> Cập nhật tự động sau mỗi lần `evaluate_parse --run-dir` hoặc `run_parse_experiment`.  
> Chi tiết từng run: `data/experiments/parse/<run_id>/` (`parse_metrics.json`, `summary_by_format.csv`).

<!-- EXP_RESULTS_START -->
| Run ID | Thời gian (UTC) | Dataset | Bỏ qua | Forms | TP | FP | FN | Precision | Recall | F1 | Latency μ (ms) | Thư mục kết quả |
|--------|-----------------|---------|--------|-------|----|----|-----|-----------|--------|-----|----------------|-----------------|
| `legacy_batch_20260525` | 2026-05-25 (thủ công) | `…\Downloads\parse_form_dataset` | PDF | 16 | 4 | 103 | 154 | 3.74% | 2.53% | 3.02% | 8336.26 | `data/parse_eval/` (legacy) |
<!-- EXP_RESULTS_END -->

### Ghi chú run `legacy_batch_20260525`

- Dataset: `C:\Users\KHANH\Downloads\parse_form_dataset` (22 form, GT 210 field).
- Không tính PDF (`PARSE_EVAL_SKIP_FORMATS=pdf`) → 16 form DOCX + XLSX.
- File metric: `parse_metrics_no_pdf.json`, predictions: `parse_predictions.csv`.
- Theo định dạng: DOCX F1 ≈ 3.83%, XLSX F1 = 0% (parser XLSX mới trả 1 field/form).
- Các lần chạy sau dùng `run_parse_experiment --name …` sẽ thêm dòng vào bảng trên và lưu đủ file trong `data/experiments/parse/<run_id>/`.
