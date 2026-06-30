# Failure Cluster Analysis — Phase A

**Sinh viên:** Nguyễn Hoàng Tùng  
**Ngày:** 2026-06-30

---

## 1. Aggregate RAGAS Scores theo Distribution

| Metric | factual | multi_hop | adversarial |
|---|---|---|---|
| faithfulness | 0.0 | 0.0 | 0.0 |
| answer_relevancy | 0.0 | 0.0 | 0.0 |
| context_precision | 0.0 | 0.0 | 0.0 |
| context_recall | 0.0 | 0.0 | 0.0 |
| **avg_score** | 0.0 | 0.0 | 0.0 |

Ghi chú: Chạy `python src/phase_a_ragas.py` hiện báo lỗi RAGAS runtime (`cannot convert longdouble infinity to integer`) nên `evaluate_ragas()` fallback toàn bộ metrics về `0.0`. Vì vậy các phân tích dưới đây chủ yếu dựa vào distribution + nội dung câu hỏi bottom-10, chưa phản ánh đúng chất lượng pipeline.

---

## 2. Bottom 10 Questions

| Rank | Distribution | Question | avg_score | worst_metric |
|---|---|---|---|---|
| 1 | factual | Nhân viên được nghỉ bao nhiêu ngày khi kết hôn? | 0.0 | faithfulness |
| 2 | factual | Bảo hiểm sức khỏe PVI có hạn mức bao nhiêu cho nhân viên? | 0.0 | faithfulness |
| 3 | factual | Phụ cấp ăn trưa hàng tháng là bao nhiêu? | 0.0 | faithfulness |
| 4 | factual | Mentor và buddy của nhân viên mới có thể là cùng một người không? Quản lý trực tiếp có thể làm mentor không? | 0.0 | faithfulness |
| 5 | factual | Muốn mua thiết bị trị giá 55 triệu cần ai phê duyệt? | 0.0 | faithfulness |
| 6 | factual | Thông tin lương thuộc cấp độ phân loại dữ liệu nào? | 0.0 | faithfulness |
| 7 | factual | Nghỉ phép không lương 20 ngày cần ai phê duyệt? | 0.0 | faithfulness |
| 8 | factual | Nhân viên được nghỉ bao nhiêu ngày khi cha hoặc mẹ mất? | 0.0 | faithfulness |
| 9 | factual | Nam nhân viên được nghỉ bao nhiêu ngày khi vợ sinh con? | 0.0 | faithfulness |
| 10 | factual | Nhân viên chính thức được phép làm việc từ xa tối đa bao nhiêu ngày một tuần? | 0.0 | faithfulness |

---

## 3. Failure Cluster Matrix

*(Mỗi ô = số câu có worst_metric = row, thuộc distribution = col)*

| worst_metric | factual | multi_hop | adversarial | Total |
|---|---|---|---|---|
| faithfulness | 20 | 20 | 10 | 50 |
| answer_relevancy | 0 | 0 | 0 | 0 |
| context_precision | 0 | 0 | 0 | 0 |
| context_recall | 0 | 0 | 0 | 0 |

---

## 4. Dominant Failure Analysis

**Dominant distribution:** factual  
**Dominant metric:** faithfulness

**Lý do phân tích:**

Trong report hiện tại, tất cả metrics đều bằng 0.0 do lỗi khi chạy RAGAS nên `worst_metric` mặc định rơi về `faithfulness` (tie-break theo thứ tự key). Nhìn theo nội dung bottom-10 (đa số là factual), đây là nhóm câu hỏi dễ nhưng nhạy với policy versioning (ví dụ phép năm v2023 vs v2024, ngưỡng phê duyệt 50 triệu) và các câu định nghĩa ngắn (phụ cấp, số ngày nghỉ đặc biệt) nơi model rất dễ “tự điền” nếu retrieval/context không đủ rõ. Khi RAGAS hoạt động bình thường, các lỗi kiểu này thường bộc lộ dưới `faithfulness` (hallucination/khẳng định không có trong context) và `answer_relevancy` (trả lệch policy/version).

---

## 5. Suggested Fixes

| Metric yếu | Root cause | Suggested fix |
|---|---|---|
| faithfulness | LLM tự suy diễn khi context không đủ rõ / conflict version | Bắt buộc trích dẫn theo chunk, giảm temperature, thêm “chỉ trả lời từ context”, và ưu tiên policy mới bằng metadata/version filter |
| context_recall | Missing relevant chunks | Tăng recall: hybrid search (BM25+dense), tune top_k, cải thiện chunking (child size), thêm query expansion theo từ khóa policy |
| context_precision | Too many irrelevant chunks | Rerank mạnh hơn, lọc metadata (loại policy cũ), giảm top_k sau rerank, cấm đưa context không liên quan vào prompt |
| answer_relevancy | Answer doesn't match question | Cải thiện prompt template: restate question, checklist phải trả lời đủ sub-questions; thêm judge/validator kiểm tra coverage trước khi trả về |

---

## 6. Nhận xét về Adversarial Distribution

Vì Phase A đang fallback metrics về 0.0, chưa thể so sánh `avg_score` giữa `adversarial` vs `factual` vs `multi_hop` một cách có ý nghĩa. Bottom-10 hiện toàn là factual (IDs 1-10), nhưng đây cũng là nhóm câu hỏi dễ bị “version conflict” (ví dụ phép năm 12 vs 15 ngày, ngưỡng phê duyệt 50 triệu). Khi RAGAS chạy đúng, kỳ vọng `adversarial` sẽ có `avg_score` thấp hơn do bẫy phủ định và bẫy policy cũ; cần fix lỗi RAGAS và chạy lại để xác nhận.
