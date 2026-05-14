# Thuật Toán Được Sử Dụng Trong Hệ Thống Autofill AI

## 1) Tổng quan

Hệ thống đang dùng kiến trúc **hybrid** gồm:
- Thuật toán rule-based (dễ kiểm soát, chạy nhanh, dễ debug)
- Heuristic parsing cho tài liệu biểu mẫu
- AI orchestration có failover để đảm bảo luôn có gợi ý

Các khối logic chính nằm trong:
- `backend/app/ai/rule_engine.py`
- `backend/app/api/routes/suggestions.py`
- `backend/app/services/file_parser.py`
- `backend/app/api/routes/excel.py`
- `backend/app/services/ai_composer_service.py`
- `backend/app/api/routes/word.py`

---

## 2) Thuật toán gợi ý theo lịch sử nhập liệu (same-field suggestions)

### Mục tiêu
Gợi ý giá trị cho cùng một trường dựa trên lịch sử của người dùng.

### Cách hoạt động
1. Lấy toàn bộ `Entry` theo `user_id + field_id`
2. Tính tần suất xuất hiện từng giá trị (`frequency`)
3. Tính thời gian sử dụng gần nhất (`recency`)
4. Xếp hạng theo:
   - `frequency` giảm dần
   - nếu bằng nhau thì `recency` mới hơn đứng trước
5. Trả về top-K gợi ý

### Đặc điểm
- Nhanh, ít tốn tài nguyên
- Không cần vector DB/embedding cho trường hợp cơ bản
- Dễ giải thích kết quả

---

## 3) Thuật toán gợi ý liên thông giữa các trường (cross-field suggestions)

### Mục tiêu
Gợi ý cho trường hiện tại bằng cách tận dụng dữ liệu từ các trường tương tự (không chỉ đúng một field_id).

### Pipeline
1. **Chuẩn hóa tên trường**
   - lowercase, bỏ dấu tiếng Việt, bỏ ký tự đặc biệt
2. **Tính độ tương đồng field**
   - exact match, containment, token overlap
3. **Phân loại loại trường**
   - `name`, `date`, `email`, `phone`, `identifier`, `general`
4. **Lọc giá trị nhiễu**
   - loại giá trị rác, sai định dạng theo category
5. **Xử lý đặc biệt cho họ tên**
   - có thể ghép `Họ + Tên` thành `Họ và tên`
6. **Xếp hạng đa tín hiệu**
   - similarity, frequency, latest_time, persona/template context

### Công thức similarity token-based
\[
score = 0.55 \cdot recall + 0.35 \cdot precision + 0.10 \cdot jaccard
\]

---

## 4) Thuật toán parse tài liệu đa định dạng

## 4.1 DOCX Parser
- Dùng regex phát hiện placeholder: `...`, `___`, `---`, checkbox, mẫu ngày-tháng-năm
- Tách heading và field bằng heuristic:
  - style, alignment, độ dài, từ khóa
- Loại các dòng không phải field (footer, câu mô tả dài)
- Trích label từ ngữ cảnh quanh placeholder
- Chuẩn hóa label -> tạo `field_name` dạng snake_case
- Loại trùng field (dedupe)

## 4.2 PDF/TXT/CSV Parser
- Chọn dòng có dấu hiệu label (`:`, `.`, `_`, `-`, ...)
- Lọc theo độ dài và tỷ lệ text hữu ích
- Chuẩn hóa và suy luận type field

## 4.3 XLSX/XLS Parser
- Tìm header row tốt nhất trong các dòng đầu
- Ưu tiên dòng có nhiều keyword field
- Fallback theo dòng có nhiều ô text nhất
- Hỗ trợ merged header bằng cách nhìn lên dòng trên
- Bỏ các dòng chỉ mang tính nhãn/đơn vị (ví dụ `%`)

---

## 5) Thuật toán fuzzy mapping field với Excel tham chiếu

### Mục tiêu
Map field từ form sang cột trong file Excel để sinh options tự động.

### Bước xử lý
1. Chuẩn hóa key field/header
2. Tính `match_score`
   - exact: 100
   - containment: cao (có penalty độ dài)
   - token overlap: trung bình-khá
3. Chọn header có điểm cao nhất (qua ngưỡng)
4. Trích danh sách giá trị phân biệt + row index
5. Đánh dấu `is_unique` để hỗ trợ UI/validation

### Xử lý tên đầy đủ
- Nếu có cột full name thì dùng trực tiếp
- Nếu chỉ có họ/đệm/tên thì ghép thành full name

---

## 6) Thuật toán Dot-Line Detector (legacy form)

- Nhận diện placeholder bằng regex:
  - `\.{2,}`, `_{2,}`, `-{2,}`, `─{2,}`
- Trích label từ phần text trước placeholder
- Suy luận `field_type` theo từ khóa (`ngày`, `email`, `điện thoại`, ...)
- Sinh `field_name` chuẩn hóa
- Bỏ trùng label

---

## 7) Thuật toán AI Composer (viết tiếp/viết lại)

### 7.1 Provider failover
- Theo profile: OpenRouter -> OpenAI -> Gemini (hoặc cấu hình khác)
- Nếu provider/model lỗi, tự động thử provider/model kế tiếp

### 7.2 Prompt theo mode
- `continuation`: gợi ý viết tiếp
- `rewrite`: viết lại phrase/sentence/document theo instruction

### 7.3 Parse và chuẩn hóa output
- Trích JSON từ phản hồi model
- Chuẩn hóa về `{text, confidence, reason}`
- Lọc duplicate, lọc câu lặp context, lọc gợi ý kém chất lượng

### 7.4 Local fallback
Khi AI provider lỗi toàn bộ:
- Hệ thống dùng luật local để tạo gợi ý fallback
- Vẫn đảm bảo trả kết quả cho người dùng

---

## 8) Thuật toán export DOCX giữ layout template

### Mục tiêu
Điền dữ liệu nhưng vẫn giữ định dạng tài liệu gốc.

### Cách làm
1. Duyệt paragraph trong body và table
2. Nhận diện placeholder và thay lần lượt theo dữ liệu submit
3. Với dòng ngày-tháng-năm, parse ngày để thay đúng format
4. Nếu không thể thay theo template, fallback export dạng `Label: Value`

---

## 9) Đánh giá nhanh

### Ưu điểm
- Tốc độ tốt, dễ vận hành production
- Độ ổn định cao nhờ nhiều lớp fallback
- Dễ audit logic vì phần lớn là rule/heuristic minh bạch

### Hạn chế
- Heuristic có thể giảm độ chính xác với biểu mẫu dị biệt
- Chưa dùng semantic embedding đồng nhất cho toàn bộ pipeline mapping

### Hướng nâng cấp
- Tách ngưỡng scoring ra config để tuning
- Bổ sung benchmark dataset cho parsing/matching
- Thêm semantic reranker để tăng chất lượng gợi ý khó

---

## 10) Kết luận

Hệ thống Autofill AI đang áp dụng mô hình thuật toán **hybrid thực dụng**:  
rule-based cho độ ổn định + AI cho chất lượng ngôn ngữ + failover cho độ sẵn sàng dịch vụ.  
Thiết kế này phù hợp cho giai đoạn scale nhanh và vẫn giữ đường nâng cấp rõ ràng lên semantic/ML sau này.
