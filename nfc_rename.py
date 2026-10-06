#!/usr/bin/env python3
"""
NFD -> NFC 파일명 일괄 변환
Mac rclone으로 동기화된 파일의 깨진 파일명을 복원합니다.

사용법:
    python3 nfc_rename.py [대상폴더]
    python3 nfc_rename.py  # 기본값: uploads/
"""
import os
import sys
import unicodedata


def nfc_rename(root: str) -> None:
    renamed_files = 0
    renamed_dirs = 0
    errors = 0

    # 1단계: 파일 rename
    for dirpath, dirnames, filenames in os.walk(root, topdown=True):
        for name in filenames:
            nfc = unicodedata.normalize('NFC', name)
            if name != nfc:
                old = os.path.join(dirpath, name)
                new = os.path.join(dirpath, nfc)
                try:
                    os.rename(old, new)
                    renamed_files += 1
                except Exception as e:
                    print(f"[파일 오류] {old!r}: {e}")
                    errors += 1

    # 2단계: 디렉토리 rename — 깊은 곳부터
    dirs_to_rename = []
    for dirpath, dirnames, filenames in os.walk(root, topdown=True):
        for name in dirnames:
            nfc = unicodedata.normalize('NFC', name)
            if name != nfc:
                old = os.path.join(dirpath, name)
                new = os.path.join(dirpath, nfc)
                dirs_to_rename.append((old, new))

    # 경로 깊이 내림차순 (깊은 것 먼저 rename)
    dirs_to_rename.sort(key=lambda x: x[0].count('/'), reverse=True)

    for old, new in dirs_to_rename:
        try:
            os.rename(old, new)
            renamed_dirs += 1
        except Exception as e:
            print(f"[폴더 오류] {old!r}: {e}")
            errors += 1

    print(f"\n완료 — 파일 {renamed_files}개, 폴더 {renamed_dirs}개 변환, 오류 {errors}개")


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "uploads"
    print(f"대상: {target}")
    nfc_rename(target)
