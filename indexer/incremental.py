"""증분 인덱싱 — SQLite로 파일 해시를 추적해 변경된 파일만 재처리"""
from __future__ import annotations
import hashlib, sqlite3
from datetime import datetime
from pathlib import Path
from config import DB_PATH


class IncrementalIndexer:
    def __init__(self):
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(DB_PATH))
        self._init_db()

    def _init_db(self) -> None:
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS file_index (
                file_path   TEXT PRIMARY KEY,
                file_hash   TEXT NOT NULL,
                indexed_at  TEXT NOT NULL,
                chunk_ids   TEXT NOT NULL,
                root_folder TEXT,
                modified_at TEXT
            )
        """)
        # 기존 DB 마이그레이션: modified_at 컬럼 없으면 추가
        try:
            self.conn.execute("ALTER TABLE file_index ADD COLUMN modified_at TEXT")
        except sqlite3.OperationalError:
            pass
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS bm25_tokens (
                chunk_id TEXT PRIMARY KEY,
                tokens   TEXT NOT NULL
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS metadata (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS file_run_log (
                file_path TEXT PRIMARY KEY,
                status    TEXT NOT NULL,
                logged_at TEXT NOT NULL
            )
        """)
        self.conn.commit()

    def needs_update(self, file_path: Path) -> bool:
        current = self._hash(file_path)
        row = self.conn.execute(
            "SELECT file_hash FROM file_index WHERE file_path = ?", (str(file_path),)
        ).fetchone()
        return row is None or row[0] != current

    def get_chunk_ids(self, file_path: Path) -> list[str]:
        row = self.conn.execute(
            "SELECT chunk_ids FROM file_index WHERE file_path = ?", (str(file_path),)
        ).fetchone()
        if row is None or not row[0]:
            return []
        return row[0].split(",")

    def save(self, file_path: Path, chunk_ids: list[str], root_folder: str, modified_at: str = "") -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO file_index (file_path, file_hash, indexed_at, chunk_ids, root_folder, modified_at) VALUES (?,?,?,?,?,?)",
            (str(file_path), self._hash(file_path), datetime.now().isoformat(), ",".join(chunk_ids), root_folder, modified_at),
        )
        self.conn.commit()

    def log_status(self, file_path: Path, status: str) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO file_run_log (file_path, status, logged_at) VALUES (?,?,?)",
            (str(file_path), status, datetime.now().isoformat())
        )
        self.conn.commit()

    def remove(self, file_path: Path) -> None:
        self.conn.execute("DELETE FROM file_index WHERE file_path = ?", (str(file_path),))
        self.conn.execute("DELETE FROM file_run_log WHERE file_path = ?", (str(file_path),))
        self.conn.commit()

    def all_indexed_paths_for_root(self, root_folder: str) -> set[str]:
        rows = self.conn.execute(
            "SELECT file_path FROM file_index WHERE root_folder = ?", (root_folder,)
        ).fetchall()
        return {r[0] for r in rows}

    def all_indexed_paths(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT file_path, root_folder, indexed_at, modified_at FROM file_index ORDER BY indexed_at DESC"
        ).fetchall()
        return [{"file_path": r[0], "root_folder": r[1], "indexed_at": r[2], "modified_at": r[3]} for r in rows]

    def stats(self) -> dict:
        total = self.conn.execute("SELECT COUNT(*) FROM file_index").fetchone()[0]
        last  = self.conn.execute("SELECT MAX(indexed_at) FROM file_index").fetchone()[0]
        return {"total_files": total, "last_indexed": last}

    def is_active(self, file_path: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM file_index WHERE file_path = ?", (file_path,)
        ).fetchone()
        return row is not None

    def close(self) -> None:
        self.conn.close()

    @staticmethod
    def _hash(file_path: Path) -> str:
        h = hashlib.sha256()
        with open(file_path, "rb") as f:
            for block in iter(lambda: f.read(65536), b""):
                h.update(block)
        return h.hexdigest()
