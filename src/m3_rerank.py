from __future__ import annotations

"""Module 3: Reranking — Cross-encoder top-20 → top-3 + latency benchmark."""

import os, sys, time
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import RERANK_TOP_K


@dataclass
class RerankResult:
    text: str
    original_score: float
    rerank_score: float
    metadata: dict
    rank: int


class CrossEncoderReranker:
    def __init__(self, model_name: str = "BAAI/bge-reranker-v2-m3"):
        self.model_name = model_name
        self._model = None

    def _load_model(self):
        if self._model is None:
            from sentence_transformers import CrossEncoder
            import socket
            orig_timeout = socket.getdefaulttimeout()
            socket.setdefaulttimeout(10.0)
            try:
                self._model = CrossEncoder(self.model_name)
            except Exception as e:
                print(f"  ⚠️  Failed to load cross-encoder model '{self.model_name}': {e}")
                fallback_model = "cross-encoder/ms-marco-MiniLM-L-6-v2"
                print(f"  🔄 Falling back to smaller model '{fallback_model}'...")
                try:
                    self._model = CrossEncoder(fallback_model)
                except Exception as fe:
                    print(f"  ⚠️  Failed to load fallback model '{fallback_model}': {fe}")
                    socket.setdefaulttimeout(orig_timeout)
                    raise fe
            finally:
                socket.setdefaulttimeout(orig_timeout)
        return self._model

    def rerank(self, query: str, documents: list[dict], top_k: int = RERANK_TOP_K) -> list[RerankResult]:
        """Rerank documents: top-20 → top-k."""
        if not documents:
            return []
            
        model = None
        try:
            model = self._load_model()
        except Exception as e:
            print(f"  ⚠️  Could not load any cross-encoder model: {e}")
            print("  🔄 Falling back to rule-based reranking (keyword match)...")
            
        if model is not None:
            pairs = [(query, doc["text"]) for doc in documents]
            scores = model.predict(pairs)
            import numpy as np
            if isinstance(scores, (int, float)):
                scores = [scores]
            elif isinstance(scores, np.ndarray):
                scores = scores.tolist()
            scored = sorted(zip(scores, documents), key=lambda x: x[0], reverse=True)
        else:
            # Rule-based fallback: rank based on simple overlap of query words in doc text
            query_words = set(query.lower().split())
            scored = []
            for doc in documents:
                text_words = doc["text"].lower().split()
                overlap = len(query_words.intersection(text_words))
                # Add a tiny fraction of original score to preserve retriever order for ties
                score = float(overlap) + 0.01 * float(doc.get("score", 0.0))
                scored.append((score, doc))
            scored.sort(key=lambda x: x[0], reverse=True)
            
        return [
            RerankResult(
                text=doc["text"],
                original_score=float(doc.get("score", 0.0)),
                rerank_score=float(score),
                metadata=doc.get("metadata", {}),
                rank=i
            )
            for i, (score, doc) in enumerate(scored[:top_k])
        ]


class FlashrankReranker:
    """Lightweight alternative (<5ms). Optional."""
    def __init__(self):
        self._model = None

    def rerank(self, query: str, documents: list[dict], top_k: int = RERANK_TOP_K) -> list[RerankResult]:
        if not documents:
            return []
        from flashrank import Ranker, RerankRequest
        if self._model is None:
            self._model = Ranker()
        passages = [{"id": i, "text": d["text"]} for i, d in enumerate(documents)]
        request = RerankRequest(query=query, passages=passages)
        results = self._model.rerank(request)
        scored_results = []
        for i, res in enumerate(results[:top_k]):
            orig_idx = res["id"]
            doc = documents[orig_idx]
            scored_results.append(RerankResult(
                text=doc["text"],
                original_score=float(doc.get("score", 0.0)),
                rerank_score=float(res["score"]),
                metadata=doc.get("metadata", {}),
                rank=i
            ))
        return scored_results


def benchmark_reranker(reranker, query: str, documents: list[dict], n_runs: int = 5) -> dict:
    """Benchmark latency over n_runs. (Đã implement sẵn)"""
    times = []
    for _ in range(n_runs):
        start = time.perf_counter()
        reranker.rerank(query, documents)
        elapsed = (time.perf_counter() - start) * 1000
        times.append(elapsed)
    return {"avg_ms": sum(times) / len(times), "min_ms": min(times), "max_ms": max(times)}


if __name__ == "__main__":
    query = "Nhân viên được nghỉ phép bao nhiêu ngày?"
    docs = [
        {"text": "Nhân viên được nghỉ 12 ngày/năm.", "score": 0.8, "metadata": {}},
        {"text": "Mật khẩu thay đổi mỗi 90 ngày.", "score": 0.7, "metadata": {}},
        {"text": "Thời gian thử việc là 60 ngày.", "score": 0.75, "metadata": {}},
    ]
    reranker = CrossEncoderReranker()
    for r in reranker.rerank(query, docs):
        print(f"[{r.rank}] {r.rerank_score:.4f} | {r.text}")
