"""
대시보드 통계 수집 — /api/stats 엔드포인트용
server.py에서 get_dashboard_stats(indexing_lock, search_obj)로 호출
"""
from __future__ import annotations
import os
import sqlite3
import subprocess
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import DB_PATH, UPLOADS_DIR, SUPPORTED_EXTENSIONS
from indexer import VectorStore, IncrementalIndexer


def get_dashboard_stats(indexing_lock, search_obj) -> dict:
    incremental = IncrementalIndexer()
    db_stats    = incremental.stats()
    incremental.close()

    vs          = VectorStore()
    chunk_count = vs.count()

    last_rebuild = last_success = last_skip = last_error = None
    try:
        conn = sqlite3.connect(str(DB_PATH))
        row = conn.execute(
            "SELECT value FROM metadata WHERE key='last_rebuild_at'"
        ).fetchone()
        conn.close()
        last_rebuild = row[0] if row else None
    except Exception:
        pass

    # 파일별 현재 상태 — 디스크에 존재하는 파일만 카운트 (삭제된 파일 자동 제외)
    try:
        conn2 = sqlite3.connect(str(DB_PATH))
        run_s = run_sk = run_e = 0
        for row in conn2.execute("SELECT file_path, status FROM file_run_log"):
            if Path(row[0]).exists():
                if row[1] == 'success':
                    run_s += 1
                elif row[1] == 'skip':
                    run_sk += 1
                elif row[1] == 'error':
                    run_e += 1
        conn2.close()
        if run_s + run_sk + run_e > 0:
            last_success = run_s
            last_skip    = run_sk
            last_error   = run_e
    except Exception:
        pass

    bm25_loaded = search_obj is not None and search_obj.bm25._bm25 is not None

    pending_files = None
    try:
        if UPLOADS_DIR.exists():
            from datetime import datetime
            conn_fi = sqlite3.connect(str(DB_PATH))
            indexed = {row[0]: row[1] for row in conn_fi.execute(
                "SELECT file_path, indexed_at FROM file_index"
            )}
            conn_fi.close()

            pending_files = 0
            for root, _, files in os.walk(UPLOADS_DIR):
                for f in files:
                    fp = os.path.join(root, f)
                    if Path(fp).suffix.lower() not in SUPPORTED_EXTENSIONS or f.startswith("~$"):
                        continue
                    fp_str = str(Path(fp))
                    if fp_str not in indexed:
                        pending_files += 1
                    else:
                        try:
                            indexed_ts = datetime.fromisoformat(indexed[fp_str]).timestamp()
                            if os.path.getmtime(fp) > indexed_ts:
                                pending_files += 1
                        except Exception:
                            pass
    except Exception:
        pass

    ext_counts = {}
    try:
        conn2 = sqlite3.connect(str(DB_PATH))
        for row in conn2.execute("SELECT file_path FROM file_index"):
            ext = Path(row[0]).suffix.lower()
            ext_counts[ext] = ext_counts.get(ext, 0) + 1
        conn2.close()
    except Exception:
        pass

    user_counts = {}
    try:
        uploads_str = str(UPLOADS_DIR).rstrip("/\\")
        conn3 = sqlite3.connect(str(DB_PATH))
        for row in conn3.execute("SELECT file_path FROM file_index"):
            p = Path(row[0])
            try:
                rel = p.relative_to(uploads_str)
                user = rel.parts[0]
                user_counts[user] = user_counts.get(user, 0) + 1
            except ValueError:
                pass
        conn3.close()
    except Exception:
        pass

    watcher_active = None
    try:
        result = subprocess.run(
            ["systemctl", "is-active", "knowledge-watcher"],
            capture_output=True, text=True, timeout=2,
        )
        watcher_active = result.stdout.strip() == "active"
    except Exception:
        pass

    return {
        "total_files":    db_stats["total_files"],
        "total_chunks":   chunk_count,
        "last_indexed":   db_stats["last_indexed"],
        "last_rebuild":   last_rebuild,
        "bm25_loaded":    bm25_loaded,
        "indexing_now":   indexing_lock.locked(),
        "last_success":   last_success,
        "last_skip":      last_skip,
        "last_error":     last_error,
        "pending_files":  pending_files,
        "ext_counts":     ext_counts,
        "user_counts":    user_counts,
        "watcher_active": watcher_active,
    }
