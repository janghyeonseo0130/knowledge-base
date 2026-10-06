from __future__ import annotations
import chromadb
from chromadb.config import Settings
from config import CHROMA_PATH


class VectorStore:
    def __init__(self):
        CHROMA_PATH.mkdir(parents=True, exist_ok=True)
        self.client = chromadb.PersistentClient(
            path=str(CHROMA_PATH),
            settings=Settings(anonymized_telemetry=False),
        )
        self.col = self.client.get_or_create_collection(
            name="knowledge_base",
            metadata={"hnsw:space": "cosine"},
        )

    def add(self, ids: list[str], embeddings: list[list[float]], documents: list[str], metadatas: list[dict], batch_size: int = 5000) -> None:
        for i in range(0, len(ids), batch_size):
            self.col.add(
                ids=ids[i:i+batch_size],
                embeddings=embeddings[i:i+batch_size],
                documents=documents[i:i+batch_size],
                metadatas=metadatas[i:i+batch_size],
            )

    def query(self, embedding: list[float], n_results: int = 50) -> dict:
        return self.col.query(
            query_embeddings=[embedding],
            n_results=min(n_results, self.col.count() or 1),
            include=["documents", "metadatas", "distances"],
        )

    def get_by_id(self, doc_id: str) -> dict | None:
        res = self.col.get(ids=[doc_id], include=["documents", "metadatas"])
        if res["ids"]:
            return {"id": res["ids"][0], "content": res["documents"][0], "metadata": res["metadatas"][0]}
        return None

    def get_by_source(self, source: str, offset: int = 0, limit: int = 20) -> list[dict]:
        """특정 파일의 청크를 chunk_index 순으로 반환 (페이지네이션 지원)"""
        res = self.col.get(
            where={"source": {"$eq": source}},
            include=["documents", "metadatas"],
        )
        if not res["ids"]:
            return []
        items = sorted(
            zip(res["ids"], res["documents"], res["metadatas"]),
            key=lambda x: x[2].get("chunk_index", 0),
        )
        return [
            {"id": id_, "content": doc, "metadata": meta}
            for id_, doc, meta in items[offset: offset + limit]
        ]

    def delete(self, ids: list[str]) -> None:
        if ids:
            self.col.delete(ids=ids)

    def count(self) -> int:
        return self.col.count()
