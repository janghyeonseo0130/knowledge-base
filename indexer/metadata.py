"""메타데이터 추출 (파일시스템 + 문서 내장 속성 + 첫 줄 패턴)"""
from __future__ import annotations
import re
from datetime import datetime
from pathlib import Path


def _extract_date_from_filename(filename: str) -> str:
    """파일명에서 날짜 패턴 추출. YYYY-MM-DD 형식으로 반환."""
    # YYYYMMDD (예: 20240115_보고서.hwp)
    m = re.search(r'(2\d{3})(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])', filename)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    # YYYY-MM-DD / YYYY.MM.DD / YYYY/MM/DD
    m = re.search(r'(2\d{3})[-./](0[1-9]|1[0-2])[-./](0[1-9]|[12]\d|3[01])', filename)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    # YYYY년 MM월 DD일
    m = re.search(r'(2\d{3})년\s*(0?[1-9]|1[0-2])월\s*(0?[1-9]|[12]\d|3[01])일', filename)
    if m:
        return f"{m.group(1)}-{m.group(2).zfill(2)}-{m.group(3).zfill(2)}"
    return ""


def build_metadata(file_path: Path, doc_meta: dict | None = None) -> dict:
    stat = file_path.stat()
    meta: dict = {
        "source":      str(file_path),
        "filename":    file_path.name,
        "extension":   file_path.suffix.lower(),
        "size_bytes":  stat.st_size,
        "created_at":  datetime.fromtimestamp(stat.st_ctime).isoformat(),
        "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
    }
    # 파일명 날짜가 있으면 mtime보다 우선 (서버 전송 시 타임스탬프 초기화 보완)
    fname_date = _extract_date_from_filename(file_path.name)
    if fname_date:
        meta["modified_at"] = fname_date
    # 문서 내부 메타데이터가 있으면 최우선 적용
    if doc_meta:
        for k, v in doc_meta.items():
            if str(v).strip():
                meta[k] = str(v).strip()
    return meta


def infer_title(content: str, file_path: Path) -> str:
    if not content:
        return file_path.stem
    for line in content.splitlines()[:15]:
        line = line.strip()
        m = re.match(r"^#+\s+(.+)", line)
        if m:
            return m.group(1)
        if line and len(line) <= 120:
            return line
    return file_path.stem
