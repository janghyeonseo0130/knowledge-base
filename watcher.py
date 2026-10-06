"""
파일 변경 감지 → /reindex POST (watchdog 기반)

변경 이유:
  - 기존: pipeline.run() 직접 호출 → Embedder + BM25 중복 로드 → OOM
  - 신규: 파일 감지만 담당, 인덱싱은 MCP 서버(/reindex)에 위임

사용법:
  python watcher.py <폴더경로>
  python main.py watch <폴더경로>
"""
from __future__ import annotations
import json
import logging
import threading
import urllib.request
from pathlib import Path

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from config import SUPPORTED_EXTENSIONS, WATCHER_DEBOUNCE, MCP_HOST, MCP_PORT

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

_REINDEX_URL = f"http://{MCP_HOST}:{MCP_PORT}/reindex"


def _post_reindex(folder_path: str, retry: bool = True) -> bool:
    """POST /reindex. 실패 시 retry=True면 30초 후 1회 재시도."""
    try:
        data = json.dumps({"folder_path": folder_path}).encode()
        req  = urllib.request.Request(
            _REINDEX_URL, data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=5)
        log.info(f"[watcher] /reindex 요청 완료: {folder_path}")
        return True
    except Exception as e:
        log.warning(f"[watcher] /reindex 요청 실패: {e}")
        if retry:
            log.info("[watcher] 30초 후 재시도")
            threading.Timer(30, _post_reindex, args=(folder_path, False)).start()
        return False


class AutoIndexHandler(FileSystemEventHandler):
    def __init__(self, folder_path: str, debounce: float = WATCHER_DEBOUNCE):
        self.folder_path = folder_path
        self.debounce    = debounce
        self._timer:    threading.Timer | None = None
        self._lock      = threading.Lock()
        self._pending   = False  # 요청 중 변경 감지 시 완료 후 1회 재실행 플래그
        self._running   = False

    def _is_relevant(self, path: str) -> bool:
        p = Path(path)
        return p.suffix.lower() in SUPPORTED_EXTENSIONS and not p.name.startswith("~$")

    def on_created(self, event):
        if not event.is_directory and self._is_relevant(event.src_path):
            self._schedule()

    def on_modified(self, event):
        if not event.is_directory and self._is_relevant(event.src_path):
            self._schedule()

    def on_moved(self, event):
        if not event.is_directory and self._is_relevant(event.dest_path):
            self._schedule()

    def _schedule(self):
        with self._lock:
            if self._running:
                self._pending = True
                log.info("변경 감지 (요청 중) — 완료 후 재실행 예약")
                return
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self.debounce, self._run)
            self._timer.start()
        log.info(f"변경 감지 — {self.debounce}초 후 /reindex 요청")

    def _run(self):
        with self._lock:
            self._running = True
            self._pending = False

        try:
            _post_reindex(self.folder_path)
        finally:
            with self._lock:
                self._running = False
                if self._pending:
                    self._pending = False
                    self._timer   = threading.Timer(self.debounce, self._run)
                    self._timer.start()
                    log.info(f"요청 중 변경 감지됨 — {self.debounce}초 후 재실행")


def watch(folder_path: str):
    folder = Path(folder_path)
    if not folder.exists():
        raise FileNotFoundError(f"폴더 없음: {folder_path}")

    # startup scan: 서비스 재시작 중 누락된 파일 처리
    _startup_scan(folder_path)

    handler  = AutoIndexHandler(str(folder))
    observer = Observer()
    observer.schedule(handler, str(folder), recursive=True)
    observer.start()
    log.info(f"감시 시작: {folder}  (debounce: {WATCHER_DEBOUNCE}초)")

    try:
        while observer.is_alive():
            observer.join(timeout=1)
    except KeyboardInterrupt:
        log.info("감시 중단")
    finally:
        observer.stop()
        observer.join()


def _startup_scan(folder_path: str) -> None:
    """watcher 재시작 중 누락된 파일이 있으면 /reindex 요청"""
    try:
        from indexer.incremental import IncrementalIndexer
        folder    = Path(folder_path)
        all_files = [
            f for f in folder.rglob("*")
            if f.suffix.lower() in SUPPORTED_EXTENSIONS and not f.name.startswith("~$")
        ]
        inc    = IncrementalIndexer()
        missed = [f for f in all_files if inc.needs_update(f)]
        inc.close()

        if missed:
            log.info(f"[startup scan] 누락 파일 {len(missed)}개 감지 → /reindex 요청")
            _post_reindex(folder_path)
        else:
            log.info("[startup scan] 누락 파일 없음")
    except Exception as e:
        log.warning(f"[startup scan] 오류: {e}")


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    watch(sys.argv[1])
