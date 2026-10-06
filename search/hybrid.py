"""
하이브리드 검색: 벡터 + BM25 → RRF 병합 → 문서 단위 중복 제거 → Reranker

변경 이유:
  - 기존: 0.6×벡터 + 0.4×BM25 가중합 → 점수 스케일 차이에 취약
  - 신규: RRF (Reciprocal Rank Fusion) → 순위 기반 병합, 스케일 독립적
  - 문서 단위 중복 제거: 같은 파일의 청크가 여러 개 올라오지 않도록
"""
from __future__ import annotations
import re
from pathlib import Path
from config import TOP_K_VECTOR, TOP_K_BM25, RRF_K, USE_RERANKER
from indexer import Embedder, VectorStore, BM25Index


class HybridSearch:
    def __init__(self, embedder: Embedder, vector_store: VectorStore, bm25_index: BM25Index, reranker=None):
        self.embedder     = embedder
        self.vector_store = vector_store
        self.bm25         = bm25_index
        self.reranker     = reranker

    @staticmethod
    def _normalize_filename(filename: str) -> str:
        stem = Path(filename).stem
        stem = re.sub(r'[_\s\-]?v\d+', '', stem, flags=re.IGNORECASE)
        stem = re.sub(r'[_\s\-]?(최종|수정본?|final|rev\d*|draft|임시|temp)', '', stem, flags=re.IGNORECASE)
        stem = re.sub(r'[_\s\-]\d+$', '', stem)
        return stem.strip().lower()

    @staticmethod
    def _apply_latest_only(results: list[dict]) -> list[dict]:
        """같은 폴더 내 유사 이름 문서 중 modified_at 기준 최신 파일만 유지."""
        best: dict[tuple, tuple] = {}
        for item in results:
            meta = item.get("metadata", {})
            source = meta.get("source", "")
            filename = meta.get("filename", Path(source).name if source else "")
            folder = str(Path(source).parent) if source else ""
            key = (folder, HybridSearch._normalize_filename(filename))
            modified = (meta.get("modified_at") or "")[:10]
            if key not in best or modified > best[key][0]:
                best[key] = (modified, source)

        best_sources = {v[1] for v in best.values()}
        seen: set[tuple] = set()
        filtered = []
        for item in results:
            meta = item.get("metadata", {})
            source = meta.get("source", "")
            filename = meta.get("filename", Path(source).name if source else "")
            folder = str(Path(source).parent) if source else ""
            key = (folder, HybridSearch._normalize_filename(filename))
            if source in best_sources and key not in seen:
                seen.add(key)
                filtered.append(item)
        return filtered

    @staticmethod
    def _path_match(source: str, folder: str) -> bool:
        return folder.replace("\\", "/") in source.replace("\\", "/")

    @staticmethod
    def _date_match(meta: dict, date_from: str | None, date_to: str | None) -> bool:
        modified = (meta.get("modified_at") or "")[:10]
        if not modified:
            return True
        if date_from and modified < date_from:
            return False
        if date_to and modified > date_to:
            return False
        return True

    def search(
        self,
        query: str,
        top_k: int = 5,
        folder: str | None = None,
        file_filter: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        latest_only: bool = False,
    ) -> list[dict]:
        # ── 1. 벡터 검색 ────────────────────────────────────────────────────────
        q_vec = self.embedder.encode_query(query)
        v_res = self.vector_store.query(q_vec, n_results=TOP_K_VECTOR)

        v_items: dict[str, dict] = {}
        if v_res["ids"]:
            for doc_id, doc, meta, dist in zip(
                v_res["ids"][0], v_res["documents"][0],
                v_res["metadatas"][0], v_res["distances"][0],
            ):
                if folder and not self._path_match(meta.get("source", ""), folder):
                    continue
                if file_filter and file_filter not in meta.get("filename", ""):
                    continue
                if not self._date_match(meta, date_from, date_to):
                    continue
                v_items[doc_id] = {
                    "id": doc_id, "content": doc,
                    "metadata": meta, "vector_score": 1.0 - float(dist),
                }

        # ── 2. BM25 검색 ────────────────────────────────────────────────────────
        bm25_results = self.bm25.search(query, top_k=TOP_K_BM25)

        # ── 3. RRF 병합 ─────────────────────────────────────────────────────────
        rrf: dict[str, float] = {}
        for rank, doc_id in enumerate(sorted(v_items, key=lambda x: v_items[x]["vector_score"], reverse=True)):
            rrf[doc_id] = rrf.get(doc_id, 0.0) + 1.0 / (RRF_K + rank + 1)
        for rank, (doc_id, _) in enumerate(bm25_results):
            rrf[doc_id] = rrf.get(doc_id, 0.0) + 1.0 / (RRF_K + rank + 1)

        # ── 4. 결과 목록 구성 ────────────────────────────────────────────────────
        combined: list[dict] = []
        for doc_id, rrf_score in sorted(rrf.items(), key=lambda x: x[1], reverse=True):
            if doc_id in v_items:
                item = dict(v_items[doc_id])
            else:
                fetched = self.vector_store.get_by_id(doc_id)
                if fetched is None:
                    continue
                meta = fetched.get("metadata", {})
                if folder and not self._path_match(meta.get("source", ""), folder):
                    continue
                if file_filter and file_filter not in meta.get("filename", ""):
                    continue
                if not self._date_match(meta, date_from, date_to):
                    continue
                item = fetched
            item["rrf_score"] = rrf_score
            combined.append(item)

        # ── 5. 문서 단위 중복 제거 (소스 파일 기준 최고점 청크만 유지) ──────────
        seen: dict[str, dict] = {}
        for item in combined:
            source = item.get("metadata", {}).get("source", item["id"])
            if source not in seen or item["rrf_score"] > seen[source]["rrf_score"]:
                seen[source] = item
        deduped = sorted(seen.values(), key=lambda x: x["rrf_score"], reverse=True)

        # ── 6. latest_only 필터 ──────────────────────────────────────────────────
        if latest_only:
            deduped = self._apply_latest_only(deduped)

        # ── 7. active 필터 (BM25 rebuild 전 삭제된 청크 제거) ────────────────────
        deduped = self._filter_active(deduped)

        # ── 8. Reranker ──────────────────────────────────────────────────────────
        if USE_RERANKER and self.reranker is not None:
            return self.reranker.rerank(query, deduped, top_k=top_k)

        return deduped[:top_k]

    @staticmethod
    def _filter_active(items: list[dict]) -> list[dict]:
        """file_index에 없는 source(삭제된 파일)를 결과에서 제거"""
        if not items:
            return items
        import sqlite3
        from config import DB_PATH
        sources = list({item.get("metadata", {}).get("source", "") for item in items})
        sources = [s for s in sources if s]
        if not sources:
            return items
        try:
            conn = sqlite3.connect(str(DB_PATH))
            ph   = ",".join("?" * len(sources))
            active = {r[0] for r in conn.execute(
                f"SELECT file_path FROM file_index WHERE file_path IN ({ph})", sources
            ).fetchall()}
            conn.close()
            return [item for item in items
                    if item.get("metadata", {}).get("source", "") in active]
        except Exception:
            return items  # DB 오류 시 필터 스킵
