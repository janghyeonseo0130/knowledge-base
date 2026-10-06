"""BGE-M3 로컬 임베딩 (1024D, API 불필요)"""
from __future__ import annotations
from config import get_device, EMBEDDING_MODEL, EMBEDDING_BATCH_SIZE


class Embedder:
    def __init__(self):
        from FlagEmbedding import BGEM3FlagModel
        self.device = get_device()
        print(f"[Embedder] 디바이스: {self.device}  모델: {EMBEDDING_MODEL}")
        self.model = BGEM3FlagModel(
            EMBEDDING_MODEL,
            use_fp16=(self.device != "cpu"),
            device=self.device,
        )

    def encode(self, texts: list[str]) -> list[list[float]]:
        out = self.model.encode(
            texts,
            batch_size=EMBEDDING_BATCH_SIZE,
            max_length=8192,
            return_dense=True,
        )
        return out["dense_vecs"].tolist()

    def encode_query(self, query: str) -> list[float]:
        out = self.model.encode(
            [query],
            batch_size=1,
            max_length=8192,
            return_dense=True,
        )
        return out["dense_vecs"][0].tolist()
