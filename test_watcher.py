"""
watcher.py 동작 검증 테스트
- pipeline.run()을 3초 슬립으로 모킹
- 테스트 폴더에 .txt 파일을 생성/수정하면서 무한루프 없이 종료되는지 확인
"""
import sys
import time
import threading
import tempfile
import os
from pathlib import Path
from unittest.mock import patch

# pipeline 모킹: 3초 슬립 (인덱싱 시뮬레이션)
run_count = 0
run_lock = threading.Lock()

def mock_pipeline_run(folder_path):
    global run_count
    with run_lock:
        run_count += 1
        current = run_count
    print(f"  [mock pipeline] 실행 #{current} 시작 (3초 대기)")
    time.sleep(3)
    print(f"  [mock pipeline] 실행 #{current} 완료")

# watcher 임포트 전에 pipeline 모킹
import unittest.mock as mock
import importlib

# sys.modules에 가짜 pipeline 등록
fake_pipeline = mock.MagicMock()
fake_pipeline.run = mock_pipeline_run
sys.modules['pipeline'] = fake_pipeline

# config도 모킹
fake_config = mock.MagicMock()
fake_config.SUPPORTED_EXTENSIONS = {'.txt', '.md', '.pdf', '.docx'}
fake_config.WATCHER_DEBOUNCE = 2  # 테스트용 2초 debounce
sys.modules['config'] = fake_config

# 이제 watcher 임포트
import watcher
importlib.reload(watcher)

from watcher import AutoIndexHandler
from watchdog.observers import Observer


def run_test():
    with tempfile.TemporaryDirectory() as tmpdir:
        print(f"\n[테스트] 감시 폴더: {tmpdir}")
        print("[테스트] debounce: 2초, mock pipeline 소요: 3초\n")

        handler = AutoIndexHandler(tmpdir, debounce=2)
        observer = Observer()
        observer.schedule(handler, tmpdir, recursive=True)
        observer.start()

        # --- 시나리오 1: 파일 1개 생성 → 인덱싱 1회 후 종료 ---
        print("=== 시나리오 1: 파일 1개 생성 ===")
        test_file = Path(tmpdir) / "test1.txt"
        test_file.write_text("테스트 문서입니다.")
        print(f"  파일 생성: {test_file.name}")

        time.sleep(8)  # debounce 2초 + pipeline 3초 + 여유 3초
        print(f"  → 총 실행 횟수: {run_count}  (예상: 1)\n")

        # --- 시나리오 2: 인덱싱 중 파일 여러 번 수정 → 완료 후 1회만 추가 실행 ---
        print("=== 시나리오 2: 인덱싱 중 파일 연속 수정 ===")
        before = run_count

        # 파일 수정 → 2초 후 인덱싱 시작
        test_file.write_text("수정1")
        print(f"  파일 수정 #1")
        time.sleep(2.5)  # 인덱싱 시작됨 (pipeline 3초 소요)

        # 인덱싱 중 추가 수정 3회
        for i in range(2, 5):
            test_file.write_text(f"수정{i}")
            print(f"  파일 수정 #{i} (인덱싱 중)")
            time.sleep(0.3)

        time.sleep(10)  # 첫 번째 완료(3초) + 두 번째 완료(3초) + 여유
        added = run_count - before
        print(f"  → 추가 실행 횟수: {added}  (예상: 2 — 1회 + 변경 감지 1회)\n")

        observer.stop()
        observer.join()

        # 결과
        print("=== 결과 ===")
        print(f"총 pipeline.run() 호출: {run_count}회")
        if run_count <= 3:
            print("✓ 무한루프 없음 — 정상")
        else:
            print("✗ 예상보다 많이 실행됨 — 재확인 필요")


if __name__ == "__main__":
    run_test()
