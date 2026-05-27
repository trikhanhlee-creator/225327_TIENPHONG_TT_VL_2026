# Thuật Toán Được Sử Dụng Trong Hệ Thống Autofill AI

> Tài liệu đồng bộ với mã nguồn tại nhánh hiện tại (kiểm tra: tháng 5/2026).

## 1) Tổng quan

Hệ thống dùng kiến trúc **hybrid**:

- Thuật toán **rule-based** (lịch sử nhập, matching trường, fuzzy Excel)
- **Heuristic parsing + layout detection** đa định dạng (Word `.doc`/`.docx`, Excel, PDF, CSV, TXT)
- **LLM-assisted form parsing** cho Word/Excel (prompt theo cấu trúc tài liệu, fallback parser-first)
- **Pipeline Autofill đa-agent** (parse → hiểu trường → truy xuất → quyết định LLM + fallback deterministic)
- **RAG tùy chọn**: embedding vector + cosine similarity trên chunk trong DB; tích hợp qua `RagFormService`
- **AI Composer** (`AIComposerService`) với failover provider/model cho soạn thảo và task JSON của agent

### Bảng tham chiếu mã nguồn

| Khu vực | File / thư mục |
|--------|----------------|
| Gợi ý same-field + cross-field + RAG merge | `backend/app/ai/rule_engine.py`, `backend/app/api/routes/suggestions.py` |
| Parse đa định dạng | `backend/app/services/file_parser.py` |
| Parse cấu trúc Word/Excel (layout-aware) | `backend/app/services/autofill/document_structure.py`, `backend/app/services/autofill/excel_structure.py` |
| LLM parse Word/Excel | `backend/app/services/autofill/llm_word_form_service.py`, `backend/app/services/autofill/llm_excel_form_service.py`, `backend/app/services/autofill/form_field_rules.py` |
| Chuyển `.doc` → `.docx` | `backend/app/services/doc_converter.py` |
| Excel session + fuzzy map cột | `backend/app/api/routes/excel.py` |
| Word upload/export + RAG fill | `backend/app/api/routes/word.py` |
| Form replacement (dot-line legacy + intelligent API) | `backend/app/api/routes/form_replacement.py`, `backend/app/services/form_replacement/` |
| Soạn thảo AI + failover | `backend/app/services/ai_composer_service.py` |
| Autofill agents + orchestrator | `backend/app/services/autofill/` |
| RAG trên form/upload/export | `backend/app/services/autofill/rag_form_service.py` |
| API Autofill / Memory | `backend/app/api/routes/autofill.py`, `backend/app/api/routes/memory.py` |
| Parse evaluation / experiments | `backend/app/services/parse_dataset.py`, `backend/app/services/parse_eval_logger.py`, `backend/app/services/parse_experiment_run.py`, `backend/scripts/run_parse_experiment.py` |
| Cấu hình AI & RAG | `backend/app/core/config.py` |
| Model OpenRouter (profile riêng) | `backend/app/api/providers/config.py` |

### Router FastAPI đã mount (`main.py`)

`auth`, `suggestions`, `word`, `form_replacement`, `excel`, `composer`, `form_edit`, `admin`, `payment`, `autofill`, `memory`

UI chính: `/word-upload`, `/excel`, `/form`, `/autofill-review`, `/payment`, admin pages; Composer UI tạm redirect về `/home`.

---

## 2) Thuật toán gợi ý theo lịch sử nhập liệu (same-field suggestions)

### Mục tiêu

Gợi ý giá trị cho **cùng một trường** dựa trên lịch sử người dùng (`Entry` + `Field`).

### `RuleEngine` (`backend/app/ai/rule_engine.py`)

1. Lấy `Entry` theo `user_id` + `field_id`
2. Tính **tần suất** (`Counter`) và **thời gian gần nhất** (`recency`) cho từng giá trị
3. Xếp hạng: `frequency` giảm dần; hòa thì `recency` mới hơn
4. Yêu cầu tối thiểu **2 entries** mới trả gợi ý (trong `rank_suggestions`)

### API `/api/suggestions`

- Endpoint same-field có thể sort trực tiếp trên `Entry` theo `(frequency, latest_time)` — hành vi gần với `RuleEngine`, có thể khác ngưỡng tối thiểu tùy endpoint.
- Khi `RAG_ENABLED=true`, hàm `_merge_rag_into_suggestion_stats` bổ sung ứng viên từ `RagFormService.suggest_values_for_field_name` vào thống kê cross-field.

### Đặc điểm

- Nhanh, không bắt buộc embedding
- Dễ giải thích kết quả

---

## 3) Thuật toán gợi ý liên thông giữa các trường (cross-field suggestions)

### Mục tiêu

Gợi ý cho trường hiện tại bằng dữ liệu từ các trường **tương tự** trên toàn lịch sử (Word/Excel/form).

### Pipeline (`suggestions.py`)

1. **Chuẩn hóa** tên trường: bỏ dấu tiếng Việt, token hóa (`_normalize_field_key`)
2. **Similarity token-based**: exact (1.0), containment (0.9), hoặc kết hợp recall/precision/Jaccard
3. **Phân loại category**: `name`, `date`, `email`, `phone`, `identifier`, `general`
4. Lọc nhiễu và xung đột **persona** (ngữ cảnh giảng viên vs sinh viên, v.v.)
5. Xử lý **họ tên**: ghép họ + tên, tách full name khi cần
6. **Xếp hạng đa tín hiệu**: template hits, persona, similarity, frequency, `latest_time`
7. **Bổ sung RAG** (nếu bật): merge ứng viên semantic vào `stats` với `source_fields` ghi `"Bộ nhớ RAG"`

---

## 4) Pipeline Autofill đa-agent

Module: `backend/app/services/autofill/`, điều phối bởi `AutofillOrchestrator`.

### Luồng chuẩn bị form (`parse_and_prepare_schema`)

1. **Parse file** — `LLMFormParseAgent`:
   - **Parser-first**: `FileParserFactory` → danh sách field heuristic ổn định
   - **LLM enrichment**:
     - Word: `enhance_word_template_fields` (dịch vụ `LLMWordFormService`, có chiến lược `parser_only` / `llm` / `syll_template` / `syll_template_plus_llm`)
     - Excel: `enhance_excel_template_fields` (dịch vụ `LLMExcelFormService`, prompt theo `excel_layout`)
   - Nếu LLM timeout/lỗi/không đủ trường thì giữ hoặc merge với parser output theo fallback deterministic
2. **Hiểu trường** — `LLMFieldUnderstandingAgent` (aliases, kiểu, ngữ nghĩa bổ sung)
3. **RAG khi upload** (nếu `RAG_ENABLED` và `RAG_INDEX_ON_UPLOAD`):
   - `RagFormService.index_uploaded_file`: trích text DOCX/DOC → `StandaloneTextIndexer`
   - `build_field_hints` → ghi `schema.metadata["rag_hints"]`
4. **Lưu DB**: `FormInstance` + `FormInstanceField`

API: `POST /api/autofill/upload-and-parse`

### Luồng chạy autofill (`run_autofill`)

Với mỗi ô trong `FormInstance`:

1. **Truy xuất** — `LLMMemoryRetrievalAgent.retrieve_for_field` (mục 5)
2. **Quyết định** — `LLMAutofillDecisionAgent.decide`:
   - LLM chọn `{value, confidence, reason, source_index}` trong danh sách ứng viên
   - JSON không hợp lệ → **fallback deterministic**: ứng viên đầu sau sort tier/composite
3. **Ghi nhận** `AutofillRun` / `AutofillSuggestion`, `latency_ms`

API: `POST /api/autofill/run`, review tại `/autofill-review`

### Học từ phản hồi

`LLMFeedbackLearningAgent` (`accepted` / `edited` / `rejected`):

- Accept/edit: tạo/cập nhật `UserMemoryItem` (`is_confirmed`, tăng `score`/`confidence`)
- Reject: giảm `score`/`confidence` của giá trị bị từ chối
- Sau accept/edit: **reindex chunk** qua `MemoryChunkIndexer.index_memory_item` cho RAG

---

## 5) Thuật toán truy xuất bộ nhớ (hybrid + tier ranking)

Thực hiện trong `LLMMemoryRetrievalAgent`.

### Thứ tự nguồn và tier (ưu tiên thấp = tốt hơn)

| Tier | Nguồn | Mô tả |
|------|--------|--------|
| 0 | `UserMemoryItem` đã xác nhận | `is_confirmed=true`, confidence ≥ 0.92 |
| 1 | `UserMemoryItem` thường | Theo `score`, `updated_at` |
| 2 | RAG khớp `field_key` chunk | `field_key` chunk = field đang điền |
| 3 | RAG similarity cao | `similarity ≥ RAG_HIGH_SIMILARITY` (mặc định 0.72) |
| 4 | Legacy `Entry` | Khớp `field_name` chuẩn hóa = `field_key` |
| 5 | RAG yếu | `similarity ≥ RAG_MIN_SIMILARITY` (mặc định 0.55) |

### Điểm composite

\[
\text{composite} = \text{tier\_base} + 50 \cdot \text{confidence} + 8 \cdot \text{score} + 120 \cdot \text{similarity} - \text{length\_penalty}(value)
\]

`tier_base` lần lượt: 1000, 820, 680, 540, 360, 220.

### Legacy Entry (tier 4)

\[
\text{freq\_score} = \text{freq} \times 1.5 + \frac{\max(0,\, 30 - \text{recency\_days})}{30}
\]

`confidence = min(0.9, 0.45 + freq × 0.08)`

### RAG semantic

1. Chuỗi truy vấn: `field_key`, `label`, `field_type`, `aliases`
2. `embed_texts_sync` → cosine trên tối đa `RAG_MAX_CHUNKS_SCAN` chunk
3. Lọc `similarity < RAG_MIN_SIMILARITY`
4. Trích giá trị: ưu tiên dòng `field_key: value` trong chunk (`_extract_rag_value`)
5. Phân tier 2/3/5 theo khớp field và ngưỡng similarity

### Tín hiệu hoạt động

- `UserActivity.count > 20` → cộng **+0.04** `confidence` cho ứng viên tier ≤ 1 (memory)

### Tùy chọn bỏ qua RAG

- `RAG_SKIP_IF_STRONG_MEMORY=true`: bỏ bước RAG nếu đủ memory mạnh (`confidence ≥ 0.85`, tier ≤ 1)

### Hậu xử lý

- Sort theo `(tier, -composite_score, -confidence, len(value))`
- Dedupe theo value lower-case, giữ top `top_k`

**Lưu ý vận hành**: RAG quét chunk trong Python — phù hợp pilot; production lớn nên dùng vector DB / ANN.

---

## 6) `RagFormService` — RAG trên form thực tế

File: `backend/app/services/autofill/rag_form_service.py`

| Hàm | Vai trò |
|-----|---------|
| `index_uploaded_file` | Sau upload Word/form: trích text (DOCX hoặc `.doc` qua converter), index `StandaloneTextIndexer` với `source_ref=upload:...` |
| `build_field_hints` | Gợi ý sớm per-field khi parse (merge vào template fields) |
| `suggest_values_for_field_name` | API suggestions / word — danh sách value + confidence + tier |
| `enrich_data_map` | Export DOCX: điền ô trống từ RAG (`RAG_FILL_ON_EXPORT`) |
| `merge_hints_into_template_fields` | Gắn `suggested_value`, `suggestion_confidence` vào JSON field |

**Tích hợp**:

- `AutofillOrchestrator.parse_and_prepare_schema`
- `word.py` upload + export (`fill_missing_from_rag`)
- `form_replacement.py` upload intelligent
- `suggestions.py` merge RAG vào cross-field

Validation giá trị theo loại trường (email regex, SĐT 8–15 chữ số, ngày có digit, text ≤ 800 ký tự).

---

## 7) Index chunk & embedding

### `MemoryChunkIndexer`

- Chunk `UserMemoryItem.value_text` với `RAG_CHUNK_CHAR_LIMIT` / `RAG_CHUNK_OVERLAP`
- Prefix `field_key:` trên mỗi chunk
- Lưu `UserMemoryChunk` (`embedding_json`, model, dimension)
- `semantic_search_for_user`: cosine trong Python, top-K

### `StandaloneTextIndexer`

- Index plaintext từ file upload (không gắn `memory_item_id` cụ thể)
- Dùng cho recall ngữ cảnh toàn văn bản biểu mẫu

### `embedding_service.embed_texts_sync`

- Provider: `RAG_EMBEDDING_PROVIDER` (`openai` | `gemini` | rỗng = auto)
- Auto: ưu tiên OpenAI (`OPENAI_API_KEY` / `AI_API_KEY`), fallback Gemini (`GEMINI_API_KEY`)
- Model mặc định: `text-embedding-3-small` (OpenAI) hoặc `models/gemini-embedding-001`
- Batch: `RAG_EMBED_BATCH_SIZE` (tối đa 100 mỗi lần gọi OpenAI)

### Biến môi trường RAG (`config.py`)

| Biến | Mặc định | Ý nghĩa |
|------|----------|---------|
| `RAG_ENABLED` | `true` | Bật/tắt toàn bộ RAG |
| `RAG_EMBEDDING_PROVIDER` | `""` | ép provider embedding |
| `RAG_EMBEDDING_MODEL` | `text-embedding-3-small` | tên model |
| `RAG_SEMANTIC_TOP_K` | `5` | top chunk semantic |
| `RAG_MIN_SIMILARITY` | `0.55` | ngưỡng RAG yếu |
| `RAG_HIGH_SIMILARITY` | `0.72` | ngưỡng RAG mạnh |
| `RAG_SKIP_IF_STRONG_MEMORY` | `false` | bỏ RAG khi memory đủ mạnh |
| `RAG_MAX_CHUNKS_SCAN` | `2000` | giới hạn chunk quét |
| `RAG_CHUNK_CHAR_LIMIT` | `480` | độ dài chunk |
| `RAG_CHUNK_OVERLAP` | `64` | overlap chunk |
| `RAG_EMBED_BATCH_SIZE` | `64` | batch embed |
| `RAG_INDEX_ON_UPLOAD` | `true` | index file khi upload |
| `RAG_FILL_ON_EXPORT` | `true` | điền thiếu khi export Word |

---

## 8) LLM Client cho agent (JSON)

`LLMClient` tái sử dụng `AIComposerService.get_text_suggestions` (mode `rewrite`, ép JSON), parse bằng `_extract_json`. Dùng cho: parse enrichment, field understanding, autofill decision.

---

## 9) Thuật toán parse tài liệu đa định dạng

Factory: `FileParserFactory` trong `file_parser.py`.

### 9.1 DOCX (`.docx`)

- Regex placeholder: `...`, `___`, `===`, checkbox, template ngày-tháng-năm
- Heuristic heading vs field (style, alignment, độ dài, tiền tố)
- Loại footer / đoạn dài không phải ô nhập
- Chuẩn hóa label → `field_name` snake_case, dedupe

### 9.2 DOC legacy (`.doc`)

- `DocParser` gọi `ensure_docx_for_processing` (`doc_converter.py`) rồi delegate `DocxParser`
- Converter thử lần lượt: **LibreOffice headless** → **Word COM (Windows)** → **plaintext fallback** (antiword hoặc heuristic binary OLE)

### 9.3 PDF / TXT / CSV

- PDF (`pdfplumber`): heuristic dòng label (separator, độ dài, tỷ lệ chữ/số)
- TXT/CSV: tách dòng/cột, nhận diện label

### 9.4 XLSX / XLS

- Dùng `parse_excel_rows` để **phân loại cấu trúc sheet** rồi parse theo chiến lược tương ứng
- Các layout chính:
  - `vertical_kv`: cột "Tên trường" / "Giá trị cần điền"
  - `two_column_kv`: 2 cột nhãn-giá trị không có header chuẩn
  - `horizontal_header`: bảng ngang có hàng header
  - `first_column_labels`: nhãn tập trung ở cột đầu
- Metadata parse lưu thêm: `excel_layout`, `excel_layout_confidence`, `excel_layout_candidates`

### 9.5 Word/Excel LLM parsing chuyên biệt (mới)

- **Word (`LLMWordFormService`)**
  - Trích cấu trúc tài liệu bằng `extract_structured_document` (giữ thứ tự paragraph + table grid)
  - Prompt ép JSON liệt kê toàn bộ field fillable; loại trừ block chữ ký cuối đơn
  - Có heuristic nhận diện mẫu **Sơ yếu lý lịch**:
    - Dùng schema fallback đầy đủ (`_fallback_syll_template_fields`)
    - Tùy chọn ghép thêm output LLM bằng `WORD_LLM_SYLL_TRY_LLM`
  - Hậu xử lý: lọc field không fillable, dedupe nhãn gần tương đương, chuẩn hóa section cho đơn một phần

- **Excel (`LLMExcelFormService`)**
  - Prompt thay đổi theo `excel_layout` để giảm sai cấu trúc
  - Dùng `sheet_to_text_preview` làm input cho LLM, merge lại với parser fields
  - Fallback theo ngưỡng `EXCEL_LLM_PARSE_MIN_FIELDS` và timeout `EXCEL_LLM_PARSE_TIMEOUT_SEC`

---

## 10) Thuật toán fuzzy mapping field với Excel tham chiếu

(`excel.py` — `_match_header_score`)

- Chuẩn hóa key: bỏ dấu, ký tự an toàn
- Điểm: **exact = 100**; **substring = max(70, 92 − |Δlen|)**; **token overlap** tối đa 95
- **Ngưỡng chấp nhận ghép cột: `best_score ≥ 55`**
- Họ và tên: ghép họ + đệm + tên nếu không có cột full name

---

## 11) Form replacement — Dot-Line vs Intelligent API

### Dot-Line Detector (đang hoạt động — legacy)

File: `dot_line_detector.py`

- Regex: `\.{2,}`, `_{2,}`, `-{2,}`, `─{2,}`
- Trích label, suy `field_type` từ keyword, sinh `field_name`, dedupe
- API: `/api/form-replacement/upload` (legacy), `/template/{id}/render-form`

### Intelligent Detector (API mới, implementation hiện tại là stub)

File: `intelligent_detector.py` — class có placeholder `detect_from_html/docx/pdf`; **chưa có** `parse_document` / `extract_field_list` trong mã nguồn hiện tại.

Route `/upload-with-intelligent-detection` **gọi** `IntelligentDetector.parse_document` — cần bổ sung implementation hoặc chuyển sang `FileParserFactory` + `DotLineDetector` để chạy ổn định.

**Luồng Word chính** (`/api/word/upload`) dùng `FileParserFactory` + orchestrator, **không** phụ thuộc IntelligentDetector stub.

---

## 12) Thuật toán AI Composer

### Provider failover

- Profile: `AI_PROFILE` (`auto` | `openrouter` | `openai` | `gemini`)
- `AI_FAILOVER_ENABLED`: thử provider/model kế tiếp khi lỗi
- OpenRouter/OpenAI: chuỗi model từ `OPENROUTER_FALLBACK_MODELS` / `OPENAI_FALLBACK_MODELS`

### Mode

- `continuation`, `rewrite` (phrase / sentence / document)
- Chuẩn hóa output JSON khi agent yêu cầu

### Local fallback

- Không có client hoặc tất cả provider lỗi: mock / rewrite rule cục bộ

---

## 13) Thuật toán export DOCX giữ layout template

(`word.py`)

- Duyệt paragraph body + bảng (`_iter_doc_paragraphs`)
- Chuẩn hóa dòng chữ ký ngày-tháng-năm (`DATE_TEMPLATE_RE`) khi có giá trị ngày
- Thay placeholder theo thứ tự field đã parse
- Trước export: `RagFormService.enrich_data_map` nếu `fill_missing_from_rag` và `RAG_FILL_ON_EXPORT`
- Fallback: xuất dạng `Nhãn: Giá trị`

---

## 14) Bộ nhớ thống nhất & API Memory

- `UserMemoryService`: ingest legacy `Entry` → `UserMemoryItem` (`POST /api/memory/ingest-legacy`)
- List/query memory: `GET /api/memory`
- Đồng bộ với pipeline autofill feedback (mục 4)

---

## 15) Đánh giá nhanh

### Ưu điểm

- Tách bạch rule-based và LLM; fallback deterministic khi LLM thất bại
- Tier ranking giải thích được thứ tự ưu tiên nguồn dữ liệu
- `RagFormService` gắn RAG xuyên suốt upload → gợi ý → export
- Hỗ trợ `.doc` qua converter đa chiến lược
- Failover API giúp composer/autofill ít bị “đứng”

### Hạn chế / rủi ro

- Heuristic parse vẫn sai trên mẫu biểu rất tự do
- RAG scan tuyến tính theo số chunk — cần vector index khi dữ liệu lớn
- `IntelligentDetector` chưa implement đầy đủ so với route form-replacement
- Embedding cần key OpenAI hoặc Gemini khi `RAG_ENABLED=true`

### Hướng nâng cấp

- Hoàn thiện `IntelligentDetector` hoặc thống nhất một parser cho form-replacement
- Vector index (pgvector, HNSW, …), benchmark recall/precision trên biểu mẫu thật
- Đưa ngưỡng scoring (Excel 55, RAG similarity) ra config UI/admin

---

## 16) Parse evaluation & experiment tracking (mới)

- `parse_dataset.py`: đọc metadata/ground-truth chuẩn và hỗ trợ skip format (`PARSE_EVAL_SKIP_FORMATS`)
- `parse_eval_logger.py`: ghi dự đoán parse, latency và log thực nghiệm
- `parse_experiment_run.py`: quản lý run folder theo từng experiment, xuất metrics/report và append kết quả vào README parse eval
- Scripts vận hành:
  - `backend/scripts/run_parse_batch.py`
  - `backend/scripts/evaluate_parse.py`
  - `backend/scripts/run_parse_experiment.py`
- Dữ liệu đầu ra:
  - Snapshot chung: `backend/data/parse_eval/`
  - Theo từng run: `backend/data/experiments/parse/`

---

## 17) Kết luận

Autofill AI kết hợp **lịch sử có cấu trúc**, **matching trường thông minh**, **pipeline autofill đa-agent (parser-first)**, **RAG embedding có tier ranking**, và **AI Composer có failover**. Kiến trúc hybrid cân bằng khả giải thích, chi phí vận hành và chất lượng gợi ý khi có LLM; `RagFormService` là lớp glue chính giữa memory semantic và các luồng Word/Excel/suggestions hiện tại.
