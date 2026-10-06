from __future__ import annotations
from config import get_device, RERANKER_MODEL


class Reranker:
    def __init__(self):
        from FlagEmbedding import FlagReranker
        self.device = get_device()
        print(f"[Reranker] 디바이스: {self.device}  모델: {RERANKER_MODEL}")
        self.model = FlagReranker(
            RERANKER_MODEL,
            use_fp16=(self.device != "cpu"),
            device=self.device,
        )

    def rerank(self, query: str, candidates: list[dict], top_k: int = 10) -> list[dict]:
        if not candidates:
            return []
        pairs  = [[query, c["content"]] for c in candidates]
        scores = self.model.compute_score(pairs, normalize=True)
        for c, s in zip(candidates, scores):
            c["rerank_score"] = float(s)
        candidates.sort(key=lambda x: x["rerank_score"], reverse=True)
        return candidates[:top_k]
