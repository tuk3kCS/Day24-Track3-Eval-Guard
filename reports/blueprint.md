# CI/CD Blueprint: RAG Eval + Guardrail Stack

**Sinh viên:** Nguyễn Hoàng Tùng  
**Ngày:** 2026-06-30

---

## Guard Stack Architecture

```
User Input
    │
    ▼ (~5.54ms P95)
[Presidio PII Scan]
    │ block if: VN_CCCD / VN_PHONE / EMAIL detected
    │ action:   return 400 + "PII detected in query"
    ▼ (~1.3ms P95)
[NeMo Input Rail]
    │ block if: off-topic / jailbreak / prompt injection
    │ action:   return 503 + refuse message
    ▼
[RAG Pipeline (Day 18)]
    │ M1 Chunk → M2 Search → M3 Rerank → GPT-4o-mini
    ▼
[NeMo Output Rail]
    │ flag if:  PII in response / sensitive content
    │ action:   replace with safe response
    ▼
User Response
```

---

## Latency Budget

*(Điền từ kết quả Task 12 — measure_p95_latency())*

| Layer | P50 (ms) | P95 (ms) | P99 (ms) | Budget |
|---|---|---|---|---|
| Presidio PII | 4.9 | 5.54 | 5.54 | <10ms |
| NeMo Input Rail | 0.98 | 1.3 | 1.3 | <300ms |
| RAG Pipeline | N/A | N/A | N/A | <2000ms |
| NeMo Output Rail | N/A | N/A | N/A | <300ms |
| **Total Guard** | 5.88 | **6.58** | 6.58 | **<500ms** |

**Budget OK?** [x] Yes / [ ] No  
**Comment:** Latency đo trong lab hiện rất thấp (P95 total 6.58ms) vì pipeline đo chỉ gồm Presidio + NeMo input rail; chưa đo RAG pipeline và output rail.

---

## CI/CD Gates (phải pass trước khi merge to main)

```yaml
# .github/workflows/rag_eval.yml
- name: RAGAS Quality Gate
  run: python src/phase_a_ragas.py
  env:
    MIN_FAITHFULNESS: 0.75
    MIN_AVG_SCORE: 0.65

- name: Guardrail Gate
  run: pytest tests/test_phase_c.py -k "test_adversarial_suite_pass_rate"
  # phải ≥ 15/20 (75%)

- name: Latency Gate
  run: python -c "from src.phase_c_guard import measure_p95_latency; ..."
  # P95 total < 500ms
```

---

## Monitoring Dashboard (production)

| Metric | Alert Threshold | Action |
|---|---|---|
| RAGAS faithfulness (daily sample) | < 0.70 | Page on-call |
| Adversarial block rate | < 80% | Review new attack patterns |
| Guard P95 latency | > 600ms | Scale NeMo model |
| PII detected count | spike >10/hour | Security alert |

---

## Kết quả thực tế từ Lab

| | Kết quả |
|---|---|
| RAGAS avg_score (50q) | 0.0 *(RAGAS error → fallback 0.0)* |
| Worst metric | faithfulness |
| Dominant failure distribution | factual |
| Cohen's κ | 0.286 |
| Adversarial pass rate | 18 / 20 |
| Guard P95 latency | 6.58 ms |

---

## Nhận xét & Cải tiến

Guardrails hoạt động tốt ở mức rule-based: Presidio + pattern matching giúp block phần lớn PII/jailbreak/prompt-injection (18/20). Tuy nhiên Phase A hiện chưa usable vì RAGAS gặp lỗi runtime (`cannot convert longdouble infinity to integer`) và đang fallback về 0.0 cho mọi metric, cần fix trước khi dùng làm quality gate CI. Ở Phase B, judge có **verbosity bias** rất mạnh (100% trong các case quyết định) khi so model answer ngắn với ground-truth dài, dẫn đến κ thấp (0.286). Nếu deploy production, mình sẽ (1) sửa RAGAS runner để có metric thật và log lỗi chi tiết, (2) đổi judge sang rubric-based single-answer grading hoặc constrain length/format để giảm verbosity bias, và (3) mở rộng latency measurement để bao gồm cả RAG pipeline + output rail.
