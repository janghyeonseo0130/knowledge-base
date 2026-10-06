"""
BM25 인덱스 (BM25S + 한국어 형태소 분석)

변경 이유:
  - rank_bm25: Python 리스트로 전체 코퍼스 RAM 적재 (30~38GB)
  - BM25S: sparse matrix 기반 (~3GB), dump/load 지원
  - 토큰은 SQLite bm25_tokens 테이블에 저장 (watcher 재시작 후 누락 방지)
  - timestamp 기반 시작 로직: BM25S 파일 없거나 토큰 변경 시 자동 rebuild
  - RWLock: 검색끼리 동시 진행, swap 순간만 쓰기 잠금
"""
from __future__ import annotations
import contextlib
import gc
import json
import logging
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

from config import BM25S_PATH, DB_PATH

log = logging.getLogger(__name__)

# Kiwi 태그 중 의미 있는 것만 추출
_KIWI_KEEP_TAGS = ("NN", "VV", "VA", "XR", "SL", "SH", "MM")
_STOP_SINGLE    = set("을를이가은는의에서로부터까지도만이나과와")
_BATCH_SIZE     = 5000

_kiwi = None


def _get_kiwi():
    global _kiwi
    if _kiwi is None:
        try:
            from kiwipiepy import Kiwi
            _kiwi = Kiwi()
        except ImportError:
            _kiwi = False
    return _kiwi if _kiwi else None


def _tokenize(text: str) -> list[str]:
    kiwi = _get_kiwi()
    if kiwi is not None:
        tokens = [t.form for t in kiwi.tokenize(text)
                  if any(t.tag.startswith(tag) for tag in _KIWI_KEEP_TAGS)]
        return tokens if tokens else text.split()
    return [t for t in text.split() if not (len(t) == 1 and t in _STOP_SINGLE)]


def _tokenize_batch(texts: list[str]) -> list[list[str]]:
    kiwi = _get_kiwi()
    if kiwi is not None:
        result = []
        for i in range(0, len(texts), _BATCH_SIZE):
            batch = texts[i : i + _BATCH_SIZE]
            for text, token_list in zip(batch, kiwi.tokenize(batch)):
                tokens = [t.form for t in token_list
                          if any(t.tag.startswith(tag) for tag in _KIWI_KEEP_TAGS)]
                result.append(tokens if tokens else text.split())
        return result
    return [
        [t for t in text.split() if not (len(t) == 1 and t in _STOP_SINGLE)]
        for text in texts
    ]


# ── RWLock ────────────────────────────────────────────────────────────────────

class _RWLock:
    """읽기 공유, 쓰기 배타 Lock (stdlib 미지원 → 직접 구현)"""

    def __init__(self):
        self._read_count      = 0
        self._read_count_lock = threading.Lock()
        self._write_lock      = threading.Lock()

    @contextlib.contextmanager
    def read(self):
        with self._read_count_lock:
            self._read_count += 1
            if self._read_count == 1:
                self._write_lock.acquire()
        try:
            yield
        finally:
            with self._read_count_lock:
                self._read_count -= 1
                if self._read_count == 0:
                    self._write_lock.release()

    @contextlib.contextmanager
    def write(self):
        self._write_lock.acquire()
        try:
            yield
        finally:
            self._write_lock.release()


# ── BM25Index ─────────────────────────────────────────────────────────────────

class BM25Index:
    def __init__(self):
        self._bm25:     object        = None
        self._doc_ids:  list[str]     = []
        self._rwlock                  = _RWLock()
        self._start()

    # ── 내부 헬퍼 ────────────────────────────────────────────────────────────

    def _open_db(self) -> sqlite3.Connection:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS bm25_tokens (
                chunk_id TEXT PRIMARY KEY,
                tokens   TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS metadata (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        conn.commit()
        return conn

    def _get_timestamps(self) -> tuple[str | None, str | None]:
        try:
            conn = self._open_db()
            last_token  = conn.execute(
                "SELECT value FROM metadata WHERE key='last_token_update'"
            ).fetchone()
            last_rebuild = conn.execute(
                "SELECT value FROM metadata WHERE key='last_rebuild_at'"
            ).fetchone()
            conn.close()
            return (last_token[0] if last_token else None,
                    last_rebuild[0] if last_rebuild else None)
        except Exception:
            return None, None

    # ── 시작 로직 (timestamp 기반) ────────────────────────────────────────────

    def _start(self) -> None:
        last_token, last_rebuild = self._get_timestamps()
        doc_ids_file = BM25S_PATH / "doc_ids.json"
        bm25s_exists = BM25S_PATH.exists() and doc_ids_file.exists()

        if not bm25s_exists:
            log.info("[BM25S] 덤프 없음 → rebuild")
            self.rebuild_and_swap()
        elif last_token and last_rebuild and last_token > last_rebuild:
            log.info("[BM25S] 토큰 변경 감지 → rebuild")
            self.rebuild_and_swap()
        else:
            log.info("[BM25S] 덤프 로드")
            self._load()

    def _load(self) -> None:
        import bm25s
        try:
            bm25_obj = bm25s.BM25.load(str(BM25S_PATH), load_corpus=False)
            with open(BM25S_PATH / "doc_ids.json", encoding="utf-8") as f:
                doc_ids = json.load(f)
            with self._rwlock.write():
                self._bm25    = bm25_obj
                self._doc_ids = doc_ids
            log.info(f"[BM25S] 로드 완료 ({len(doc_ids):,}개 청크)")
        except Exception as e:
            log.warning(f"[BM25S] 로드 실패: {e} → rebuild")
            self.rebuild_and_swap()

    # ── rebuild ───────────────────────────────────────────────────────────────

    def rebuild_and_swap(self) -> None:
        """전체 토큰 로드 → BM25S 빌드 → 덤프 저장 → atomic swap"""
        import bm25s

        conn = self._open_db()
        rows = conn.execute(
            "SELECT chunk_id, tokens FROM bm25_tokens ORDER BY rowid"
        ).fetchall()
        conn.close()

        if not rows:
            log.info("[BM25S] 토큰 없음, rebuild 스킵")
            with self._rwlock.write():
                old          = self._bm25
                self._bm25   = None
                self._doc_ids = []
            del old
            gc.collect()
            return

        new_doc_ids  = [r[0] for r in rows]
        corpus_strs  = [r[1] if r[1] else "" for r in rows]

        log.info(f"[BM25S] rebuild 시작 ({len(new_doc_ids):,}개 청크)")
        new_bm25 = bm25s.BM25()
        new_bm25.index(
            bm25s.tokenize(corpus_strs, token_pattern=r"\S+",
                           stopwords=None, show_progress=False)
        )

        # 덤프 저장
        BM25S_PATH.mkdir(parents=True, exist_ok=True)
        new_bm25.save(str(BM25S_PATH))
        with open(BM25S_PATH / "doc_ids.json", "w", encoding="utf-8") as f:
            json.dump(new_doc_ids, f)

        # timestamp 갱신
        conn = self._open_db()
        conn.execute(
            "INSERT OR REPLACE INTO metadata (key, value) VALUES ('last_rebuild_at', ?)",
            (datetime.now().isoformat(),)
        )
        conn.commit()
        conn.close()

        # atomic swap
        with self._rwlock.write():
            old_bm25      = self._bm25
            self._bm25    = new_bm25
            self._doc_ids = new_doc_ids

        del old_bm25
        gc.collect()
        log.info("[BM25S] rebuild 완료")

    # ── CRUD ─────────────────────────────────────────────────────────────────

    def add(self, chunk_ids: list[str], texts: list[str]) -> None:
        if not chunk_ids:
            return
        tokens_list = _tokenize_batch(texts)
        conn = self._open_db()
        conn.executemany(
            "INSERT OR REPLACE INTO bm25_tokens (chunk_id, tokens) VALUES (?, ?)",
            [(cid, " ".join(toks)) for cid, toks in zip(chunk_ids, tokens_list)]
        )
        conn.execute(
            "INSERT OR REPLACE INTO metadata (key, value) VALUES ('last_token_update', ?)",
            (datetime.now().isoformat(),)
        )
        conn.commit()
        conn.close()

    def remove(self, chunk_ids: list[str]) -> None:
        if not chunk_ids:
            return
        conn = self._open_db()
        conn.executemany(
            "DELETE FROM bm25_tokens WHERE chunk_id = ?",
            [(cid,) for cid in chunk_ids]
        )
        conn.execute(
            "INSERT OR REPLACE INTO metadata (key, value) VALUES ('last_token_update', ?)",
            (datetime.now().isoformat(),)
        )
        conn.commit()
        conn.close()

    # ── 검색 ─────────────────────────────────────────────────────────────────

    def search(self, query: str, top_k: int = 50) -> list[tuple[str, float]]:
        with self._rwlock.read():
            if self._bm25 is None or not self._doc_ids:
                return []
            import bm25s
            tokens = _tokenize(query)
            if not tokens:
                return []
            query_tokens = bm25s.tokenize(
                [" ".join(tokens)], token_pattern=r"\S+",
                stopwords=None, show_progress=False
            )
            k       = min(top_k, len(self._doc_ids))
            results, scores = self._bm25.retrieve(
                query_tokens, corpus=self._doc_ids, k=k, show_progress=False
            )
            return [
                (doc_id, float(score))
                for doc_id, score in zip(results[0], scores[0])
                if float(score) > 0
            ]

    def save(self) -> None:
        """호환성용 — BM25S는 rebuild_and_swap에서 자동 저장됨"""
        pass
