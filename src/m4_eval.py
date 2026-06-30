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

"""Module 4: RAGAS Evaluation — 4 metrics + failure analysis."""

import os, sys, json
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import TEST_SET_PATH


@dataclass
class EvalResult:
    question: str
    answer: str
    contexts: list[str]
    ground_truth: str
    faithfulness: float
    answer_relevancy: float
    context_precision: float
    context_recall: float


def load_test_set(path: str = TEST_SET_PATH) -> list[dict]:
    """Load test set from JSON. (Đã implement sẵn)"""
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def evaluate_ragas(questions: list[str], answers: list[str],
                   contexts: list[list[str]], ground_truths: list[str]) -> dict:
    """Run RAGAS evaluation."""
    from config import OPENAI_API_KEY
    if not OPENAI_API_KEY:
        print("  [WARN] No OPENAI_API_KEY found, skipping actual RAGAS evaluation (returning fallback 0.0)")
        return {
            "faithfulness": 0.0,
            "answer_relevancy": 0.0,
            "context_precision": 0.0,
            "context_recall": 0.0,
            "per_question": [
                EvalResult(question=q, answer=a, contexts=c, ground_truth=gt,
                           faithfulness=0.0, answer_relevancy=0.0, context_precision=0.0, context_recall=0.0)
                for q, a, c, gt in zip(questions, answers, contexts, ground_truths)
            ]
        }

    try:
        import asyncio
        import nest_asyncio
        try:
            main_loop = asyncio.get_event_loop()
        except RuntimeError:
            main_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(main_loop)
        nest_asyncio.apply()

        from ragas import evaluate
        from ragas.metrics import faithfulness, answer_relevancy, context_precision, context_recall
        from datasets import Dataset
        from ragas.llms import LangchainLLMWrapper
        from ragas.embeddings import LangchainEmbeddingsWrapper
        from langchain_openai import ChatOpenAI, OpenAIEmbeddings

        eval_llm = LangchainLLMWrapper(ChatOpenAI(model="gpt-4o-mini", model_kwargs={"response_format": {"type": "json_object"}}))
        eval_embeddings = LangchainEmbeddingsWrapper(OpenAIEmbeddings())

        # Explicitly bind to prevent AttributeError on default instantiation
        faithfulness.llm = eval_llm
        answer_relevancy.llm = eval_llm
        answer_relevancy.embeddings = eval_embeddings
        context_precision.llm = eval_llm
        context_precision.embeddings = eval_embeddings
        context_recall.llm = eval_llm
        context_recall.embeddings = eval_embeddings

        dataset = Dataset.from_dict({
            "question": questions,
            "answer": answers,
            "contexts": contexts,
            "ground_truth": ground_truths,
        })
        from ragas import RunConfig
        run_config = RunConfig(max_workers=10)
        result = evaluate(dataset, metrics=[faithfulness, answer_relevancy,
                                            context_precision, context_recall],
                          run_config=run_config)
        df = result.to_pandas()
        per_question = [
            EvalResult(
                question=row.get("question", row.get("user_input", "")),
                answer=row.get("answer", row.get("response", "")),
                contexts=row.get("contexts", row.get("retrieved_contexts", [])),
                ground_truth=row.get("ground_truth", row.get("reference", "")),
                faithfulness=float(row.get("faithfulness", 0.0)),
                answer_relevancy=float(row.get("answer_relevancy", 0.0)),
                context_precision=float(row.get("context_precision", 0.0)),
                context_recall=float(row.get("context_recall", 0.0))
            )
            for _, row in df.iterrows()
        ]
        if hasattr(result, "_repr_dict"):
            scores = result._repr_dict
        else:
            scores = getattr(result, "scores", {})
            if not isinstance(scores, dict):
                try:
                    scores = dict(result)
                except Exception:
                    scores = {}

        return {
            "faithfulness": float(scores.get("faithfulness", 0.0)),
            "answer_relevancy": float(scores.get("answer_relevancy", 0.0)),
            "context_precision": float(scores.get("context_precision", 0.0)),
            "context_recall": float(scores.get("context_recall", 0.0)),
            "per_question": per_question
        }
    except Exception as e:
        print(f"  [ERROR] RAGAS evaluation failed: {e}")
        return {
            "faithfulness": 0.0,
            "answer_relevancy": 0.0,
            "context_precision": 0.0,
            "context_recall": 0.0,
            "per_question": [
                EvalResult(question=q, answer=a, contexts=c, ground_truth=gt,
                           faithfulness=0.0, answer_relevancy=0.0, context_precision=0.0, context_recall=0.0)
                for q, a, c, gt in zip(questions, answers, contexts, ground_truths)
            ]
        }


def failure_analysis(eval_results: list[EvalResult], bottom_n: int = 10) -> list[dict]:
    """Analyze bottom-N worst questions using Diagnostic Tree."""
    diagnostic_tree = {
        "faithfulness": ("LLM hallucinating", "Tighten prompt, lower temperature"),
        "context_recall": ("Missing relevant chunks", "Improve chunking or add BM25"),
        "context_precision": ("Too many irrelevant chunks", "Add reranking or metadata filter"),
        "answer_relevancy": ("Answer doesn't match question", "Improve prompt template"),
    }
    
    analyzed = []
    for r in eval_results:
        metrics = {
            "faithfulness": r.faithfulness,
            "answer_relevancy": r.answer_relevancy,
            "context_precision": r.context_precision,
            "context_recall": r.context_recall
        }
        avg_score = sum(metrics.values()) / 4.0
        worst_metric = min(metrics, key=metrics.get)
        diagnosis, suggested_fix = diagnostic_tree[worst_metric]
        
        analyzed.append({
            "question": r.question,
            "answer": r.answer,
            "contexts": r.contexts,
            "ground_truth": r.ground_truth,
            "avg_score": avg_score,
            "worst_metric": worst_metric,
            "score": metrics[worst_metric],
            "diagnosis": diagnosis,
            "suggested_fix": suggested_fix
        })
        
    analyzed.sort(key=lambda x: x["avg_score"])
    return analyzed[:bottom_n]


def save_report(results: dict, failures: list[dict], path: str = "ragas_report.json"):
    """Save evaluation report to JSON. (Đã implement sẵn)"""
    report = {
        "aggregate": {k: v for k, v in results.items() if k != "per_question"},
        "num_questions": len(results.get("per_question", [])),
        "failures": failures,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"Report saved to {path}")


if __name__ == "__main__":
    test_set = load_test_set()
    print(f"Loaded {len(test_set)} test questions")
    print("Run pipeline.py first to generate answers, then call evaluate_ragas().")
