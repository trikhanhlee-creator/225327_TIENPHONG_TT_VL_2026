# Thuật Toán Được Sử Dụng Trong Hệ Thống Autofill AI

## 1) Tổng quan

Hệ thống đang dùng kiến trúc **hybrid**:

- Thuật toán rule-based (lịch sử nhập, matching trường, fuzzy Excel)
- Heuristic parsing cho tài liệu biểu mẫu (Word/Excel/PDF/…)
- **Pipeline Autofill đa-agent** (LLM + fallback deterministic)
- **RAG tùy chọn**: embedding vector + cosine similarity trên chunks bộ nhớ
- AI Composer (`AIComposerService`) với failover provider/model cho soạn thảo và JSON tasks

Các khối logic chính (tham chiếu mã nguồn):

| Khu vực | File / thư mục |
|--------|----------------|
| Gợi ý theo field + cross-field | `backend/app/ai/rule_engine.py`, `backend/app/api/routes/suggestions.py` |
| Parse đa định dạng | `backend/app/services/file_parser.py` |
| Excel session + fuzzy map | `backend/app/api/routes/excel.py` |
| Word template + export | `backend/app/api/routes/word.py` |
| Soạn thảo AI + failover | `backend/app/services/ai_composer_service.py` |
| Autofill agent + orchestrator | `backend/app/services/autofill/` |
| API Autofill / Memory | `backend/app/api/routes/autofill.py`, `backend/app/api/routes/memory.py` |
| Cấu hình AI & RAG | `backend/app/core/config.py` |
| Model OpenRouter (API khác) | `backend/app/api/providers/config.py` |

---

## 2) Thuật toán gợi ý theo lịch sử nhập liệu (same-field suggestions)

### Mục tiêu

Gợi ý giá trị cho cùng một trường dựa trên lịch sử của người dùng.

### `RuleEngine` (service layer)

1. Lấy `Entry` theo `user_id + field_id`
2. Tính **tần suất** (`frequency`) và **thời gian dùng gần nhất** (`recency`) cho từng giá trị
3. Xếp hạng: `frequency` giảm dần; nếu hòa thì `recency` mới hơn đứng trước
4. Trả top-K; trong code có kiểm tra tối thiểu số entry khi gọi qua `SuggestionService`

### API `/api/suggestions` (FastAPI)

Một số endpoint lấy trực tiếp `Entry` và sort theo `(frequency, latest_time)` — hành vi có thể khác nhẹ so với `RuleEngine` (ví dụ ngưỡng số lần nhập). Về bản chất vẫn là **thống kê lịch sử + sort**.

### Đặc điểm

- Nhanh, không bắt buộc embedding
- Dễ giải thích kết quả

---

## 3) Thuật toán gợi ý liên thông giữa các trường (cross-field suggestions)

### Mục tiêu

Gợi ý cho trường hiện tại bằng dữ liệu từ các trường tương tự trên toàn lịch sử (Word/Excel/form).

### Pipeline (tóm tắt)

1. Chuẩn hóa tên trường (bỏ dấu, token hóa)
2. Similarity token-based (exact / containment / kết hợp recall, precision, Jaccard)
3. Phân loại category: `name`, `date`, `email`, `phone`, `identifier`, `general`
4. Lọc nhiễu và xung đột “persona” (ví dụ ngữ cảnh giảng viên vs sinh viên)
5. Xử lý họ tên: ghép họ + tên, tách full name khi cần
6. Xếp hạng đa tín hiệu: template hits, persona, similarity, frequency, `latest_time`

---

## 4) Pipeline Autofill đa-agent (parse → hiểu trường → truy xuất → quyết định)

Module: `backend/app/services/autofill/`, điều phối bởi `AutofillOrchestrator`.

### Luồng chuẩn bị form

1. **Parse file** → schema dạng chuẩn (`LLMFormParseAgent`)
2. **Hiểu trường** (aliases, kiểu, ngữ nghĩa bổ sung) → `LLMFieldUnderstandingAgent`
3. **Lưu** `FormInstance` + các `FormInstanceField` trong DB

### Luồng chạy autofill (theo từng field)

Đối với mỗi ô trong form đã parse:

1. **Truy xuất ứng viên** — `LLMMemoryRetrievalAgent.retrieve_for_field` (xem mục 5)
2. **Quyết định giá trị** — `LLMAutofillDecisionAgent.decide`:
   - Gọi LLM (qua `LLMClient` → `AIComposerService`) để chọn `{value, confidence, reason, source_index}` trong danh sách ứng viên
   - Nếu JSON không hợp lệ: **fallback deterministic** — lấy ứng viên đầu sau khi sort
3. **Ghi nhận** `AutofillRun` / `AutofillSuggestion` và thời gian chạy

### Học từ phản hồi người dùng

`LLMFeedbackLearningAgent`: cập nhật điểm / bản ghi `UserMemoryItem` khi user **accept/edit/reject**, và có thể kích hoạt **reindex chunk** cho RAG (xem `MemoryChunkIndexer`).

---

## 5) Thuật toán truy xuất bộ nhớ (unified memory + legacy + RAG)

Thực hiện trong `LLMMemoryRetrievalAgent`.

### Nguồn 1: `UserMemoryItem` (bộ nhớ thống nhất)

- Lọc theo `user_id` và `field_key` khớp
- Thứ tự ưu tiên: `is_confirmed` giảm dần → `score` giảm dần → `updated_at` mới nhất  
- Ghép vào danh sách `MemoryCandidate` (value, score, confidence, metadata)

### Nguồn 2: Legacy `Entry` + `Field`

- So khớp tên field: `field.field_name` chữ thường, space → `_`, phải bằng `field_key` của ô đang điền
- Cho mỗi giá trị: đếm tần suất + thời gian gần nhất
- **Điểm heuristic**:  
  \(\text{score} = \text{freq} \times 1.5 + \frac{\max(0,\, 30 - \text{recency_days})}{30}\)  
  `confidence` tăng theo tần suất (có clamp)

### Nguồn 3: Gợi ý nhẹ từ hoạt động (`UserActivity`)

- Nếu `activity_count > 20`: cộng tối đa **+0.05** vào `confidence` của mọi ứng viên hiện có (tín hiệu “user tích cực”).

### Nguồn 4: RAG ngữ nghĩa (khi `RAG_ENABLED`)

1. Tạo chuỗi truy vấn từ `field_key`, `label`, `field_type`, `aliases`
2. **Embedding** đồng bộ batch qua OpenAI (`embed_texts_sync` → model `RAG_EMBEDDING_MODEL`, cần API key OpenAI phù hợp trong config)
3. `MemoryChunkIndexer.semantic_search_for_user`:
   - Lấy tối đa `RAG_MAX_CHUNKS_SCAN` chunk gần đây của user
   - Tính **cosine similarity** giữa vector truy vấn và vector lưu trong `embedding_json`
   - Top-K theo similarity; đưa vào candidates với `memory_type="rag"`, score ~ `similarity × 5`

### Hậu xử lý chung

- Loại candidate rỗng
- Sort theo `(score, confidence)` giảm dần
- **Dedupe** theo giá trị (lower-case), giữ top `top_k`

**Lưu ý**: RAG trong repo hiện **quét và chấm cosine trong Python** trên một tập chunk giới hạn — phù hợp pilot; production lớn thường chuyển sang vector DB / ANN.

---

## 6) Index chunk & embedding (chuẩn bị RAG)

`MemoryChunkIndexer`:

- Chia `value_text` của `UserMemoryItem` thành các đoạn (giới hạn ký tự + overlap — `RAG_CHUNK_CHAR_LIMIT`, `RAG_CHUNK_OVERLAP`)
- Prefix chunk bằng `field_key:` để giữ ngữ cảnh trường
- Gọi `embed_texts_sync`, lưu `UserMemoryChunk` (text, embedding JSON, model, dimension)
- Cosine similarity dùng công thức chuẩn dot-product / (norms)

Biến môi trường chính (xem `backend/app/core/config.py`):  
`RAG_ENABLED`, `RAG_EMBEDDING_MODEL`, `RAG_SEMANTIC_TOP_K`, `RAG_MAX_CHUNKS_SCAN`, `RAG_CHUNK_*`, `RAG_EMBED_BATCH_SIZE`.

---

## 7) LLM Client cho agent (JSON)

`LLMClient` không tự triển khai provider riêng: **tái sử dụng** `AIComposerService.get_text_suggestions` (mode rewrite, ép trả JSON), rồi parse/extract JSON từ chuỗi trả về.

---

## 8) Thuật toán parse tài liệu đa định dạng

### 8.1 DOCX

- Regex placeholder (`...`, `___`, checkbox, template ngày-tháng-năm, …)
- Heuristic heading vs field (style, alignment, độ dài, tiền tố)
- Loại footer / đoạn dài không phải ô nhập
- Chuẩn hóa label → `field_name` snake_case, dedupe

### 8.2 PDF / TXT / CSV

- Heuristic dòng label (separator, độ dài, tỷ lệ chữ/số)

### 8.3 XLSX / XLS

- Chọn header row trong ~10 dòng đầu (ưu tiên keyword trường phổ biến)
- Hỗ trợ ô header merge (nhìn dòng trên)
- Bỏ hàng chỉ chứa nhãn phụ (vd. `%`)

---

## 9) Thuật toán fuzzy mapping field với Excel tham chiếu

- Chuẩn hóa key (không phân biệt dấu, ký tự an toàn)
- Điểm match: exact 100; substring với penalty độ dài; overlap token có trần điểm
- Ngưỡng chấp nhận ghép cột (trong code có ngưỡng tối thiểu)
- Trường hợp **họ và tên**: ghép họ + đệm + tên nếu không có một cột full name

---

## 10) Thuật toán Dot-Line Detector (form replacement legacy)

- Regex: `\.{2,}`, `_{2,}`, `-{2,}`, `─{2,}`
- Trích label, suy luận `field_type` từ keyword, sinh `field_name`, dedupe label

---

## 11) Thuật toán AI Composer (viết tiếp / viết lại)

### Provider failover

- Thứ tự thử provider theo `AI_PROFILE` / `AI_PROVIDER` (OpenRouter, OpenAI, Gemini tùy cấu hình)
- Với OpenRouter / OpenAI: có thể thử **chuỗi model** fallback khi model trước lỗi

### Mode

- `continuation`, `rewrite` (phrase / sentence / document)
- Chuẩn hóa output JSON khi được yêu cầu

### Local fallback

- Khi không có client hoặc tất cả provider lỗi: gợi ý mock / rewrite cục bộ theo rule

---

## 12) Thuật toán export DOCX giữ layout template

- Duyệt paragraph trong body và bảng
- Chuẩn hóa dòng chữ ký ngày-tháng-năm (nếu có giá trị ngày)
- Thay placeholder theo thứ tự field đã parse
- Fallback: xuất dạng `Nhãn: Giá trị`

---

## 13) Đánh giá nhanh

### Ưu điểm

- Tách bạch rule-based và LLM; có fallback deterministic khi LLM thất bại
- Có lớp bộ nhớ thống nhất + tùy chọn RAG embedding
- Failover API giúp composer/autofill ít bị “đứng” khi một model lỗi

### Hạn chế

- Heuristic parse vẫn có thể sai trên mẫu biểu rất tự do
- RAG hiện scan giới hạn chunk — độ phức tạp tuyến tính với số chunk quét; cần nâng cấp nếu dữ liệu lớn
- Embedding RAG đang qua OpenAI trong `embedding_service`; cần key và quota phù hợp khi `RAG_ENABLED=true`

### Hướng nâng cấp

- Vector index (HNSW, pgvector, …), tách scoring threshold ra config
- Benchmark parse + match + RAG recall/precision trên bộ biểu mẫu thật

---

## 14) Kết luận

Autofill AI kết hợp **lịch sử có cấu trúc**, **matching trường thông minh**, **pipeline autofill đa-agent**, và **tùy chọn RAG embedding**, cùng lớp **AI Composer có failover**. Kiến trúc **hybrid** giúp cân bằng khả giải thích, chi phí vận hành và chất lượng gợi ý khi có LLM.
