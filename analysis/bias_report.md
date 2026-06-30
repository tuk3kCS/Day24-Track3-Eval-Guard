# LLM Judge Bias Report — Phase B

**Sinh viên:** Nguyễn Hoàng Tùng  
**Ngày:** 2026-06-30  
**Judge model:** gpt-4o-mini

---

## 1. Pairwise Judge Results

*(Chạy pairwise_judge() trên ít nhất 5 cặp answers)*

| # | Question (tóm tắt) | Winner | Reasoning tóm tắt |
|---|---|---|---|
| 1 | Nghỉ khi kết hôn | B | B bổ sung điều kiện “không trừ phép năm” nên đầy đủ hơn |
| 2 | Mua thiết bị 55 triệu ai duyệt | B | A sai cấp phê duyệt; B nêu đúng CEO (trên 50 triệu) |
| 3 | Thưởng Tết tối thiểu (>=6 tháng) | B | B nêu đầy đủ cả case <6 tháng pro-rata; A thiếu |
| 4 | Senior 9 năm: phép + range lương | tie | Cả hai đều đúng; B có giải thích/công thức rõ hơn nhưng pass2 cho tie |
| 5 | Hoàn trả khóa học 25 triệu (nghỉ sau 8 tháng) | B | B giải thích điều kiện cam kết 1 năm và nêu rõ 100% hoàn trả |

---

## 2. Swap-and-Average Results

*(Chạy swap_and_average() trên cùng các cặp)*

| # | Pass 1 Winner | Pass 2 Winner | Final | Position Consistent? |
|---|---|---|---|---|
| 1 | B | B | B | Yes |
| 2 | B | B | B | Yes |
| 3 | B | B | B | Yes |
| 4 | B | tie | tie | No |
| 5 | B | B | B | Yes |

**Position bias rate:** 20% (= 2/10 cases NOT consistent)

---

## 3. Cohen's κ Analysis

**Human labels:** `human_labels_10q.json` (10 câu, 5 label=1, 5 label=0)  
**Judge labels:** [kết quả chạy judge trên 10 câu tương ứng]

| Question ID | Human Label | Judge Label | Agree? |
|---|---|---|---|
| 1 | 1 | 0 | No |
| 5 | 0 | 0 | Yes |
| 12 | 1 | 0 | No |
| 21 | 1 | 1 | Yes |
| 23 | 1 | 0 | No |
| 29 | 0 | 0 | Yes |
| 33 | 1 | 0 | No |
| 41 | 0 | 0 | Yes |
| 46 | 1 | 1 | Yes |
| 50 | 0 | 0 | Yes |

**Cohen's κ:** 0.286  
**Interpretation:** fair

---

## 4. Verbosity Bias

Trong các case có winner rõ ràng (không phải tie):
- A thắng + A dài hơn B: 0 / 8 cases
- B thắng + B dài hơn A: 8 / 8 cases  
- **Verbosity bias rate:** 100%

**Kết luận:** Judge có xu hướng chọn câu trả lời dài hơn (ở đây B là ground-truth thường dài hơn và có thêm chi tiết/giải thích), dẫn tới việc “phạt” các câu trả lời đúng nhưng ngắn gọn. Điều này làm κ thấp và khiến judge không phản ánh đúng tiêu chí “súc tích” nếu rubric không được thiết kế chặt.

---

## 5. Nhận xét chung

κ=0.286 (<0.6) nên judge chưa đủ tin cậy để làm gate tự động nếu dùng setup so sánh model answer ngắn với ground-truth dài; kết quả bị chi phối mạnh bởi verbosity bias. Position bias rate 20% là chấp nhận được, và swap-and-average giúp phát hiện 2 case không ổn định (final chuyển thành tie). Trong production, nên dùng judge theo rubric dạng chấm điểm single-answer (accuracy/coverage/conciseness tách riêng), hoặc chuẩn hóa độ dài/format (bullet + giới hạn token) trước khi chấm để giảm verbosity bias. Ngoài ra nên audit judge bằng κ định kỳ và kiểm soát drift theo distribution (factual/multi-hop/adversarial).
