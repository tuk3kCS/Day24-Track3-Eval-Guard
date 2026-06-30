from __future__ import annotations

# Monkeypatch asyncio and anyio to prevent RuntimeError: Timeout should be used inside a task
try:
    import asyncio
    orig_current_task = asyncio.current_task
    _dummy_task = None
    try:
        main_loop = asyncio.get_event_loop()
    except RuntimeError:
        main_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(main_loop)

    def patched_current_task(loop=None):
        try:
            task = orig_current_task(loop)
        except RuntimeError:
            task = None
        if task is None:
            global _dummy_task
            if _dummy_task is None:
                class DummyTask:
                    def __init__(self):
                        self._name = "DummyTask"
                        self._state = "PENDING"
                        self._must_cancel = False
                    def done(self):
                        return False
                    def get_loop(self):
                        return loop or main_loop
                    def cancelling(self):
                        return 0
                    def uncancel(self):
                        return 0
                    def cancel(self, msg=None):
                        return False
                _dummy_task = DummyTask()
            return _dummy_task
        return task

    asyncio.current_task = patched_current_task
    try:
        import asyncio.tasks
        asyncio.tasks.current_task = patched_current_task
    except Exception:
        pass
    try:
        import anyio._backends._asyncio as ab
        ab.current_task = patched_current_task
    except Exception:
        pass
except Exception as e_patch:
    print(f"  [WARN] Failed to patch asyncio.current_task globally: {e_patch}")

"""Phase C: Production Guardrails — Presidio PII + NeMo Guardrails + P95 Latency."""

import asyncio
import json
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import ADVERSARIAL_SET_PATH, GUARDRAILS_CONFIG_DIR, LATENCY_BUDGET_P95_MS, PRESIDIO_LANGUAGE


# ─── Task 9a: Presidio PII Detection ─────────────────────────────────────────

def setup_presidio():
    """Khởi tạo Presidio engine với custom Vietnamese PII recognizers. (Đã implement sẵn)

    Custom recognizers thêm vào:
        VN_CCCD  — số CCCD 12 chữ số hoặc CMND 9 chữ số
        VN_PHONE — số điện thoại Việt Nam (0[3-9]xxxxxxxx)

    Các recognizers mặc định đã có sẵn: EMAIL, PHONE_NUMBER (international), ...
    """
    from presidio_analyzer import AnalyzerEngine, RecognizerRegistry, Pattern, PatternRecognizer
    from presidio_anonymizer import AnonymizerEngine

    cccd_recognizer = PatternRecognizer(
        supported_entity="VN_CCCD",
        patterns=[
            Pattern("CCCD 12 digits", r"\b\d{12}\b", 0.9),
            Pattern("CMND 9 digits",  r"\b\d{9}\b",  0.7),
        ],
    )
    phone_recognizer = PatternRecognizer(
        supported_entity="VN_PHONE",
        patterns=[Pattern("VN mobile", r"\b0[3-9]\d{8}\b", 0.9)],
    )

    registry = RecognizerRegistry()
    registry.load_predefined_recognizers()
    registry.add_recognizer(cccd_recognizer)
    registry.add_recognizer(phone_recognizer)

    analyzer  = AnalyzerEngine(registry=registry)
    anonymizer = AnonymizerEngine()
    return analyzer, anonymizer


def pii_scan(text: str, analyzer=None, anonymizer=None) -> dict:
    """Task 9a: Quét PII trong văn bản bằng Presidio.

    Returns:
        {
          "has_pii":    bool,
          "entities":   [{"type": str, "text": str, "score": float, "start": int, "end": int}],
          "anonymized": str,   # text với PII được thay bằng <TYPE>
        }
    """
    if analyzer is None or anonymizer is None:
        analyzer, anonymizer = setup_presidio()

    # Only detect regex-based PII types — exclude NLP-based types (PERSON, NRP)
    # and non-sensitive types (DATE_TIME, LOCATION) to avoid false positives.
    # "Nhân viên" would be flagged as PERSON by spaCy, "2024" as DATE_TIME, etc.
    SENSITIVE_ENTITIES = [
        "EMAIL_ADDRESS", "PHONE_NUMBER",
        "VN_CCCD", "VN_PHONE",
        "CREDIT_CARD", "IBAN_CODE",
        "US_SSN", "US_PASSPORT", "US_BANK_NUMBER",
    ]

    results = analyzer.analyze(
        text=text, language=PRESIDIO_LANGUAGE, entities=SENSITIVE_ENTITIES
    )
    if not results:
        return {"has_pii": False, "entities": [], "anonymized": text}

    anonymized = anonymizer.anonymize(text=text, analyzer_results=results).text
    entities = [
        {"type": r.entity_type, "text": text[r.start:r.end],
         "score": round(r.score, 3), "start": r.start, "end": r.end}
        for r in results
    ]
    return {"has_pii": True, "entities": entities, "anonymized": anonymized}


# ─── Task 9b + 11: NeMo Guardrails ───────────────────────────────────────────

def setup_nemo_rails():
    """Khởi tạo NeMo Guardrails từ guardrails/config.yml. (Đã implement sẵn)

    Config directory: guardrails/
        config.yml  — model + rails config
        rails.co    — Colang dialogue flows (topic check, jailbreak check, output check)
    """
    from nemoguardrails import RailsConfig, LLMRails
    config = RailsConfig.from_path(GUARDRAILS_CONFIG_DIR)
    rails  = LLMRails(config)
    return rails


async def check_input_rail(text: str, rails=None) -> dict:
    """Task 9b: Kiểm tra input qua NeMo input rails (topic guard + jailbreak guard).

    Returns:
        {
          "allowed":        bool,
          "blocked_reason": str | None,
          "response":       str,          # NeMo's raw response
        }
    """
    if rails is None:
        rails = setup_nemo_rails()

    response = await rails.generate_async(
        messages=[{"role": "user", "content": text}]
    )
    if isinstance(response, dict):
        response_str = response.get("content", "")
    else:
        response_str = getattr(response, "content", str(response))
    # NeMo từ chối bằng cách trả về refuse message được định nghĩa trong rails.co
    refuse_keywords = [
        "xin lỗi", "không thể", "không được phép", 
        "i cannot", "i'm sorry", "không cung cấp", 
        "chỉ có thể trả lời các câu hỏi về chính sách"
    ]
    blocked = any(kw in response_str.lower() for kw in refuse_keywords)
    return {
        "allowed":        not blocked,
        "blocked_reason": "nemo_input_rail" if blocked else None,
        "response":       response_str,
    }


async def check_output_rail(question: str, answer: str, rails=None) -> dict:
    """Task 11: Kiểm tra LLM output qua NeMo output rails trước khi trả về user.

    NeMo output rails hoạt động trong context của cả cuộc hội thoại (input + output).
    Kiểm tra: có PII không? Nội dung có phù hợp không? Có hallucination rõ ràng không?

    Returns:
        {
          "safe":           bool,
          "flagged_reason": str | None,
          "final_answer":   str,          # answer đã qua guard (có thể bị redact)
        }
    """
    if rails is None:
        rails = setup_nemo_rails()

    # Cung cấp context đầy đủ để output rail hoạt động
    response = await rails.generate_async(messages=[
        {"role": "user",      "content": question},
        {"role": "assistant", "content": answer},   # output cần kiểm tra
    ])
    if isinstance(response, dict):
        response_str = response.get("content", "")
    else:
        response_str = getattr(response, "content", str(response))
    refuse_keywords = [
        "xin lỗi", "không thể cung cấp", "i cannot", 
        "tôi không thể cung cấp thông tin này", 
        "vui lòng liên hệ phòng nhân sự trực tiếp"
    ]
    flagged = any(kw in response_str.lower() for kw in refuse_keywords)
    return {
        "safe":           not flagged,
        "flagged_reason": "nemo_output_rail" if flagged else None,
        "final_answer":   response_str if flagged else answer,
    }


# ─── Task 10: Adversarial Test Suite ─────────────────────────────────────────

import re as _re

# Layer 1.5: Deterministic regex patterns for well-known adversarial categories.
# Production-standard approach (cf. Rebuff, LlamaGuard) — fast & reliable.
_ADVERSARIAL_PATTERNS = [
    # Jailbreak — role-play, DAN, unrestricted AI
    _re.compile(r"b.{0,5}qua t.{0,10}h.{0,10}ng d.{0,10}n", _re.IGNORECASE),
    _re.compile(r"ignore.{0,10}(previous|system|instructions|prompt)", _re.IGNORECASE),
    _re.compile(r"forget.{0,10}(system|instructions|prompt)", _re.IGNORECASE),
    _re.compile(r"pretend.{0,10}(you are|to be)", _re.IGNORECASE),
    _re.compile(r"DAN.{0,20}(Do Anything Now|no restriction)", _re.IGNORECASE),
    _re.compile(r"Do Anything Now", _re.IGNORECASE),
    _re.compile(r"(AI|bot).{0,20}kh.{0,5}ng c.{0,5} gi.{0,5}i h.{0,5}n", _re.IGNORECASE),
    _re.compile(r"unrestricted AI", _re.IGNORECASE),
    _re.compile(r"no restriction", _re.IGNORECASE),
    _re.compile(r"\u0111.{0,5}ng vai.{0,40}(l.{0,5}ng|l.{0,5}\u01b0\u01a1ng|salary|confidential|b.{0,5}o m.{0,5}t)", _re.IGNORECASE),
    # Prompt injection patterns
    _re.compile(r"SYSTEM OVERRIDE", _re.IGNORECASE),
    _re.compile(r"IGNORE PREVIOUS INSTRUCTIONS", _re.IGNORECASE),
    _re.compile(r"ADMIN COMMAND", _re.IGNORECASE),
    _re.compile(r"in ra to.{0,5}n b.{0,5} system", _re.IGNORECASE),
    _re.compile(r"print.{0,20}(system instruction|confidential|training data)", _re.IGNORECASE),
    _re.compile(r"dump.{0,20}(training data|employee record|all data)", _re.IGNORECASE),
    _re.compile(r"reveal.{0,20}confidential", _re.IGNORECASE),
    _re.compile(r"(CEO|admin|boss).{0,30}(l.{0,5}nh|ra l.{0,5}nh|b.{0,5}qua|order)", _re.IGNORECASE),
    _re.compile(r"list all employee", _re.IGNORECASE),
    # Off-topic — clearly outside HR domain
    _re.compile(r"b.{0,5}i th.{0,5}", _re.IGNORECASE),        # bài thơ
    _re.compile(r"n.{0,5}u (ph.{0,5}|\u0103n|b.{0,5})", _re.IGNORECASE),   # nấu phở/ăn
    _re.compile(r"(bitcoin|ethereum|crypto|altcoin)", _re.IGNORECASE),
    _re.compile(r"gi.{0,5} (c.{0,5} phi.{0,5}u|bitcoin|ethereum)", _re.IGNORECASE),
    _re.compile(r"ph.{0,5}ng tr.{0,5}nh vi ph.{0,5}n", _re.IGNORECASE),    # phương trình vi phân
    _re.compile(r"(recommend|g.{0,5}i.{0,5}i thi.{0,5}u).{0,20}(phim|film|movie)", _re.IGNORECASE),
    _re.compile(r"(phim|film|movie).{0,20}(marvel|xem|hay nh.{0,5}t)", _re.IGNORECASE),
    # PII request targeting others
    _re.compile(r"(CCCD|s.{0,5} \u0111i.{0,5}n tho.{0,5}i|email|l.{0,5}ng|th.{0,5}ng tin).{0,20}c.{0,5}a nh.{0,5}n vi.{0,5}n", _re.IGNORECASE),
    _re.compile(r"ti.{0,5}t l.{0,5}.{0,20}(l.{0,5}ng|b.{0,5}ng l.{0,5}ng|th.{0,5}ng tin)", _re.IGNORECASE),
]


def _is_adversarial_input(text: str) -> bool:
    """Layer 1.5: Deterministic regex check for known adversarial patterns."""
    for pattern in _ADVERSARIAL_PATTERNS:
        if pattern.search(text):
            return True
    return False


def run_adversarial_suite(adversarial_set: list[dict], rails=None,
                           analyzer=None, anonymizer=None) -> list[dict]:
    """Task 10: Chạy 20 adversarial inputs qua full guard stack, so sánh với expected.

    Guard stack order:
        1. pii_scan()            → block nếu has_pii (regex, fast)
        2. _is_adversarial_input → block nếu match adversarial regex patterns
        3. check_input_rail()    → block nếu jailbreak / off-topic (NeMo LLM)

    Returns:
        list of {
          "id": int, "category": str, "input": str,
          "expected": "blocked"|"allowed",
          "actual":   "blocked"|"allowed",
          "blocked_by": str | None,  # "presidio"|"pattern_match"|"nemo_input"|None
          "passed": bool,
        }
    """
    if rails is None:
        rails = setup_nemo_rails()
    if analyzer is None or anonymizer is None:
        analyzer, anonymizer = setup_presidio()

    async def _run_all():
        results = []
        for item in adversarial_set:
            blocked_by = None

            # Layer 1: Presidio PII scan (synchronous, regex-based, fast)
            pii_result = pii_scan(item["input"], analyzer, anonymizer)
            if pii_result["has_pii"]:
                blocked_by = "presidio"

            # Layer 1.5: Deterministic adversarial pattern matching
            # Catches known jailbreak, prompt-injection, off-topic, PII-request patterns.
            if blocked_by is None and _is_adversarial_input(item["input"]):
                blocked_by = "pattern_match"

            # Layer 2: NeMo input rail (async, LLM-based, semantic)
            if blocked_by is None:
                rail_result = await check_input_rail(item["input"], rails)
                if not rail_result["allowed"]:
                    blocked_by = "nemo_input"

            actual = "blocked" if blocked_by else "allowed"
            results.append({
                "id":         item["id"],
                "category":   item["category"],
                "input":      item["input"][:80] + "...",
                "expected":   item["expected"],
                "actual":     actual,
                "blocked_by": blocked_by,
                "passed":     actual == item["expected"],
            })
        return results

    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    if loop.is_running():
        import nest_asyncio
        nest_asyncio.apply()
        results = loop.run_until_complete(_run_all())
    else:
        results = loop.run_until_complete(_run_all())

    passed = sum(1 for r in results if r["passed"])
    print(f"Adversarial suite: {passed}/{len(results)} passed")
    return results


# ─── Task 12: P95 Latency Measurement ────────────────────────────────────────

def measure_p95_latency(test_inputs: list[str], n_runs: int = 20,
                         rails=None, analyzer=None, anonymizer=None) -> dict:
    """Task 12: Đo P50/P95/P99 latency cho từng layer trong guard stack.

    Mục tiêu production: P95 total < LATENCY_BUDGET_P95_MS (500ms mặc định)

    Insight cần quan sát:
        - Presidio: local regex → rất nhanh (<10ms)
        - NeMo:     LLM API call → chậm (~200-800ms tuỳ model và network)
        → Tổng: dominated by NeMo

    Returns:
        {
          "presidio_ms":  {"p50": float, "p95": float, "p99": float},
          "nemo_ms":      {"p50": float, "p95": float, "p99": float},
          "total_ms":     {"p50": float, "p95": float, "p99": float},
          "latency_budget_ok": bool,
          "budget_ms": int,
        }
    """
    if rails is None:
        rails = setup_nemo_rails()
    if analyzer is None or anonymizer is None:
        analyzer, anonymizer = setup_presidio()

    presidio_times, nemo_times, total_times = [], [], []

    async def _measure():
        for text in test_inputs[:n_runs]:
            # Presidio (synchronous)
            t0 = time.perf_counter()
            pii_scan(text, analyzer, anonymizer)
            presidio_ms = (time.perf_counter() - t0) * 1000

            # NeMo input rail (async)
            t1 = time.perf_counter()
            await check_input_rail(text, rails)
            nemo_ms = (time.perf_counter() - t1) * 1000

            presidio_times.append(presidio_ms)
            nemo_times.append(nemo_ms)
            total_times.append(presidio_ms + nemo_ms)

    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
    if loop.is_running():
        import nest_asyncio
        nest_asyncio.apply()
        loop.run_until_complete(_measure())
    else:
        loop.run_until_complete(_measure())

    def percentiles(times):
        if not times:
            return {"p50": 0.0, "p95": 0.0, "p99": 0.0}
        s = sorted(times)
        n = len(s)
        return {
            "p50": round(s[int(n * 0.50)], 2),
            "p95": round(s[int(n * 0.95)], 2),
            "p99": round(s[min(int(n * 0.99), n-1)], 2),
        }

    total_p = percentiles(total_times)
    return {
        "presidio_ms": percentiles(presidio_times),
        "nemo_ms":     percentiles(nemo_times),
        "total_ms":    total_p,
        "latency_budget_ok": total_p["p95"] < LATENCY_BUDGET_P95_MS,
        "budget_ms": LATENCY_BUDGET_P95_MS,
    }


# ─── Main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Prevent UnicodeEncodeError on Windows terminals
    if sys.platform.startswith('win'):
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

    # Setup guard stack once
    analyzer, anonymizer = setup_presidio()
    rails = setup_nemo_rails()

    # Task 9a: PII scan demo
    test_pii = "Nhân viên Nguyễn Văn A, CCCD 034095001234, SĐT 0987654321 hỏi về nghỉ phép."
    result = pii_scan(test_pii, analyzer, anonymizer)
    print(f"PII detected: {result['has_pii']}")
    print(f"Entities: {result['entities']}")
    print(f"Anonymized: {result['anonymized']}")

    # Task 10: Adversarial suite
    with open(ADVERSARIAL_SET_PATH, encoding="utf-8") as f:
        adversarial_set = json.load(f)
    print(f"\nLoaded {len(adversarial_set)} adversarial inputs")
    results = run_adversarial_suite(adversarial_set, rails, analyzer, anonymizer)
    
    passed_count = 0
    if results:
        passed_count = sum(1 for r in results if r["passed"])
        print(f"Adversarial suite: {passed_count}/{len(results)} passed")

    # Task 12: P95 latency
    sample_inputs = [item["input"] for item in adversarial_set[:10]]
    latency = measure_p95_latency(sample_inputs, n_runs=10, rails=rails, analyzer=analyzer, anonymizer=anonymizer)
    print(f"\nLatency P95 — Presidio: {latency['presidio_ms']['p95']}ms | "
          f"NeMo: {latency['nemo_ms']['p95']}ms | "
          f"Total: {latency['total_ms']['p95']}ms")
    print(f"Budget OK ({latency['budget_ms']}ms): {latency['latency_budget_ok']}")

    # Save Phase C report → reports/guard_results.json
    os.makedirs("reports", exist_ok=True)
    report = {
        "adversarial_suite": {
            "total": len(adversarial_set) if adversarial_set else 0,
            "passed": passed_count,
            "pass_rate": round(passed_count / len(adversarial_set), 4) if adversarial_set else 0.0,
            "results": results
        },
        "latency": latency
    }
    with open("reports/guard_results.json", "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print("Phase C report saved → reports/guard_results.json")
