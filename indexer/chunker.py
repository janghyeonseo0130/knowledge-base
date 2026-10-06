"""
한국어 문서 청킹 — 4단계 우선순위 경계 인식

경계 우선순위 (높을수록 먼저 처리):
  STRONG_STRUCTURE > PARAGRAPH > SENTENCE > LINE

설계 원칙:
  - SENTENCE는 구두점(.!?。！？)만 사용. 한국어 종결어미 사전 없음.
  - 개조식 표현(완료/예정/불가 등)은 LINE(\\n)이 처리.
  - STRONG_STRUCTURE는 줄 시작에서만 검사, 제한된 패턴만 사용.

파일 유형 라우팅 (pipeline.py에서 결정):
  xlsx / xls / csv → chunk_table_data()
  나머지           → chunk_unstructured_text()
"""
from __future__ import annotations
import re
from dataclasses import dataclass
from enum import IntEnum
from config import CHUNK_SIZE, CHUNK_OVERLAP, STRUCTURE_SPLIT_MIN_SIZE, MIN_BODY_SIZE


# ── 경계 유형 ──────────────────────────────────────────────────────────────────

class BoundaryType(IntEnum):
    START            = 0
    STRONG_STRUCTURE = 1
    PARAGRAPH        = 2
    SENTENCE         = 3
    LINE             = 4


@dataclass
class Segment:
    text: str
    boundary_before: BoundaryType


# ── 경계 패턴 ──────────────────────────────────────────────────────────────────
# 단일 정규식으로 왼쪽부터 평가. 겹침 없음.

BOUNDARY_PATTERN = re.compile(
    r"""
    (?P<PARAGRAPH>\n{2,})
    |
    (?P<STRONG_STRUCTURE>
        (?:(?<=\n)|\A)
        (?=
            \s*(?:
                제\s*\d+\s*(?:장|절|조|항)
                | \d+(?:\.\d+)*[.)]\s+
                | [가-힣][.)]\s+
                | \[[^\]\n]{1,50}\]
                | 【[^】\n]{1,50}】
                | [■□◆◇▶▷①②③④⑤⑥⑦⑧⑨⑩]\s*
                | [-*•·]\s+
            )
        )
    )
    |
    (?P<SENTENCE>
        [.!?。！？]
        (?=\s|$)
    )
    |
    (?P<LINE>\n)
    """,
    re.MULTILINE | re.VERBOSE,
)


def _get_boundary_type(m: re.Match) -> BoundaryType:
    return BoundaryType[m.lastgroup]


# ── 프리픽스 ───────────────────────────────────────────────────────────────────

def _build_prefix(meta: dict) -> str:
    """[날짜 | 제목 | 작성자 | 파일명] 형태의 컨텍스트 프리픽스"""
    parts = []
    date = (meta.get("modified_at") or meta.get("created_at") or "")[:7]
    if date:
        parts.append(date)
    if meta.get("title"):
        parts.append(str(meta["title"])[:40])
    if meta.get("author"):
        parts.append(str(meta["author"])[:20])
    if meta.get("filename"):
        parts.append(meta["filename"])
    return "[" + " | ".join(parts) + "] " if parts else ""


# ── 세그먼트 빌딩 ──────────────────────────────────────────────────────────────

def _segment_text(text: str) -> list[Segment]:
    """텍스트를 경계 기준으로 Segment 리스트로 분리."""
    segments: list[Segment] = []
    prev_end = 0
    prev_boundary = BoundaryType.START

    for m in BOUNDARY_PATTERN.finditer(text):
        btype = _get_boundary_type(m)

        if btype == BoundaryType.STRONG_STRUCTURE:
            # zero-width 경계: 구조 줄 시작 직전에서 앞 세그먼트를 자름
            cut_pos = m.start()
        else:
            # 종결자·줄바꿈은 앞 세그먼트에 포함
            cut_pos = m.end()

        seg_text = text[prev_end:cut_pos]
        if seg_text.strip():
            segments.append(Segment(seg_text, prev_boundary))

        prev_boundary = btype
        prev_end = m.end()

    # 마지막 세그먼트
    tail = text[prev_end:]
    if tail.strip():
        segments.append(Segment(tail, prev_boundary))

    return segments


# ── overlap ────────────────────────────────────────────────────────────────────

def _make_overlap(text: str, overlap_chars: int, max_shift: int = 70) -> str:
    """
    텍스트 끝에서 overlap_chars 기준으로 시작점을 정하되,
    max_shift 범위 내 첫 자연 경계(\\n → . ? ! → 공백)로 보정.
    overlap이 지나치게 짧아지는 것을 방지하기 위해 탐색 범위를 제한.
    """
    if overlap_chars <= 0:
        return ""
    text = text.strip()
    if len(text) <= overlap_chars:
        return text

    start = len(text) - overlap_chars
    search_end = min(len(text), start + max_shift)

    boundary_positions = []
    for sep in ("\n", ". ", "? ", "! ", " "):
        pos = text.find(sep, start, search_end)
        if pos != -1:
            boundary_positions.append(pos + len(sep))

    if boundary_positions:
        start = min(boundary_positions)

    return text[start:].strip()


# ── P4 hard split ──────────────────────────────────────────────────────────────

def _hard_split(text: str, chunk_size: int, overlap: int) -> list[str]:
    """
    단일 세그먼트가 chunk_size 초과 시 강제 분할 (P4).
    chunk_size 근처에서 뒤로 탐색: .!?\\s → \\n → 공백 순으로 자연 경계 우선.
    없으면 문자 위치에서 절단.
    마지막 조각은 호출부에서 buf로 유지해 다음 세그먼트와 병합.
    """
    chunks: list[str] = []
    start = 0
    search_window = max(chunk_size // 6, 100)  # 1200자 기준 약 200자 탐색

    while start < len(text):
        end = min(start + chunk_size, len(text))

        if end < len(text):
            window_start = max(start + chunk_size - search_window, start + 1)
            cut = None
            for pattern in (r"[.!?]\s", r"\n", r" "):
                last_match = None
                for m in re.finditer(pattern, text[window_start:end]):
                    last_match = m
                if last_match is not None:
                    cut = window_start + last_match.end()
                    break
            if cut:
                end = cut

        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)

        if end >= len(text):
            break

        next_start = max(start + 1, end - overlap)
        # overlap 시작점도 공백 기준으로 정렬
        space = text.find(" ", next_start, min(next_start + 20, len(text)))
        start = space + 1 if space != -1 else next_start

    return chunks


# ── 일반 텍스트 청킹 ────────────────────────────────────────────────────────────

def chunk_unstructured_text(text: str, metadata: dict) -> list[str]:
    """
    PDF / HWP / DOCX / TXT / HTML 등 일반 비정형 문서 청킹.
    4단계 경계(STRONG_STRUCTURE > PARAGRAPH > SENTENCE > LINE) 기반.
    """
    if not text or not text.strip():
        return []

    prefix = _build_prefix(metadata)
    effective_size = max(CHUNK_SIZE - len(prefix), MIN_BODY_SIZE)

    segments = _segment_text(text)
    chunks: list[str] = []
    buf = ""

    for seg in segments:
        # STRONG_STRUCTURE 경계: 버퍼가 충분히 쌓였으면 조기 emit (overlap 없음)
        if seg.boundary_before == BoundaryType.STRONG_STRUCTURE:
            if len(buf) >= STRUCTURE_SPLIT_MIN_SIZE and buf.strip():
                chunks.append(prefix + buf.strip())
                buf = ""

        # P4: 단일 세그먼트 자체가 effective_size 초과 → 강제 분할
        if len(seg.text) > effective_size:
            if buf.strip():
                chunks.append(prefix + buf.strip())
            parts = _hard_split(seg.text, effective_size, CHUNK_OVERLAP)
            for part in parts[:-1]:
                chunks.append(prefix + part)
            buf = parts[-1] if parts else ""
            continue

        # 일반 greedy: segment 추가 시 effective_size 초과
        if len(buf) + len(seg.text) > effective_size:
            if buf.strip():
                chunks.append(prefix + buf.strip())
            buf = _make_overlap(buf, CHUNK_OVERLAP)

        buf = (buf + seg.text) if buf else seg.text

    if buf.strip():
        chunks.append(prefix + buf.strip())

    return chunks


# ── 표 청킹 (xlsx / xls / csv) ─────────────────────────────────────────────────

_ESTIMATE_SAMPLE_ROWS = 10


def _estimate_table_chunks(rows: list, headers: list, body_limit: int) -> int:
    """샘플 행으로 예상 청크 수 추정 (TABLE_FULL_INDEX_CHUNK_LIMIT 판단용).

    처음 _ESTIMATE_SAMPLE_ROWS 행의 평균 직렬화 크기를 기준으로 전체 추정.
    """
    sample = rows[:_ESTIMATE_SAMPLE_ROWS]
    if not sample:
        return 0
    total_chars = 0
    for row in sample:
        for h, v in zip(headers, row):
            if v is None:
                continue
            v_str = str(v).strip()
            if not v_str or v_str.lower() == "nan":
                continue
            total_chars += len(f"{h}: {v_str}") + 1  # +1 for newline
    avg = total_chars / len(sample)
    if avg == 0:
        return 0
    return max(1, int(len(rows) * avg / body_limit))


def _make_structural_chunk(filename: str, table, prefix: str) -> str:
    """대형 시트용 구조 요약 청크 (LLM 없음, deterministic).

    전체 행 인덱싱 대신 파일명·시트명·행수·컬럼 목록만 저장.
    """
    col_list = "\n".join(str(h) for h in table.headers if h)
    body = (
        f"[파일] {filename}\n"
        f"[시트: {table.sheet_name}]\n"
        f"[크기] {len(table.rows):,}행 × {len(table.headers)}열\n"
        f"\n[컬럼]\n{col_list}"
    )
    return prefix + body


def chunk_table_data(tables: list, metadata: dict,
                     max_chunks: int | None = None,
                     table_full_index_chunk_limit: int | None = None) -> list[str]:
    """
    ParsedTable 리스트를 청크 리스트로 변환.

    field-aware greedy packing:
      header:value 필드를 atomic unit으로 취급, 필드 단위로 greedy 병합.
      행 경계를 넘어 pack 가능 (짧은 행 여러 개 → 1 청크).
      mid-row overflow 시 같은 행 컨텍스트([시트: X | 행: N])를 새 청크에 반복.

    large-table structural chunk:
      시트 예상 청크 > table_full_index_chunk_limit 이면
      행 인덱싱 skip, 구조 요약 chunk 1개 emit.
      (max_chunks에 도달해 중간에 자르는 방식과 다름 — 부분 인덱싱 방지)
    """
    prefix = _build_prefix(metadata)
    filename = metadata.get("filename", "")
    effective_size = max(CHUNK_SIZE - len(prefix), MIN_BODY_SIZE)
    chunks: list[str] = []

    for table in tables:
        if max_chunks is not None and len(chunks) >= max_chunks:
            break

        headers = table.headers
        rows = table.rows
        if not rows:
            continue

        table_ctx = f"[파일] {filename}  [시트] {table.sheet_name}"
        body_limit = max(effective_size - len(table_ctx) - 2, MIN_BODY_SIZE // 2)

        # ── Large-table structural mode ────────────────────────────────────
        if table_full_index_chunk_limit is not None:
            estimated = _estimate_table_chunks(rows, headers, body_limit)
            if estimated > table_full_index_chunk_limit:
                chunks.append(_make_structural_chunk(filename, table, prefix))
                continue

        # ── Field-aware greedy packing ─────────────────────────────────────
        buf = ""
        row_in_chunk = False   # 현재 행의 컨텍스트가 buf에 기록됐는가
        cur_row_ctx = ""

        for i, row in enumerate(rows, 1):
            if max_chunks is not None and len(chunks) >= max_chunks:
                break

            fields = [
                f"{h}: {str(v).strip()}"
                for h, v in zip(headers, row)
                if v is not None
                and str(v).strip()
                and str(v).strip().lower() != "nan"
            ]
            if not fields:
                continue

            cur_row_ctx = f"[시트: {table.sheet_name} | 행: {i}]"
            row_in_chunk = False  # 새 행 → 이 행의 컨텍스트는 아직 buf에 없음

            for field in fields:
                if max_chunks is not None and len(chunks) >= max_chunks:
                    break

                # P4: 단일 필드 값 자체가 body_limit 초과 → hard split
                if len(field) > body_limit:
                    if buf:
                        chunks.append(prefix + table_ctx + "\n\n" + buf)
                        buf = ""
                        row_in_chunk = False
                    parts = _hard_split(field, body_limit, CHUNK_OVERLAP) or [""]
                    n = len(parts)
                    for j, part in enumerate(parts[:-1], 1):
                        if max_chunks is not None and len(chunks) >= max_chunks:
                            break
                        frag_ctx = f"[시트: {table.sheet_name} | 행: {i} | 분할: {j}/{n}]"
                        chunks.append(prefix + table_ctx + "\n\n" + frag_ctx + "\n" + part)
                    last = parts[-1]
                    if last:
                        frag_ctx = f"[시트: {table.sheet_name} | 행: {i} | 분할: {n}/{n}]"
                        buf = frag_ctx + "\n" + last
                        row_in_chunk = True
                    continue

                # 일반 greedy packing
                if not row_in_chunk:
                    # 이 행의 첫 필드: 행 컨텍스트와 함께 추가
                    new_entry = f"{cur_row_ctx}\n{field}"
                    candidate = (buf + "\n\n" + new_entry) if buf else new_entry
                    if len(candidate) <= body_limit:
                        buf = candidate
                        row_in_chunk = True
                    else:
                        # 현재 buf에 맞지 않으면 flush 후 새 청크로 시작
                        if buf:
                            chunks.append(prefix + table_ctx + "\n\n" + buf)
                        buf = new_entry
                        row_in_chunk = True
                else:
                    # 같은 행 계속: 필드만 추가
                    candidate = buf + "\n" + field
                    if len(candidate) <= body_limit:
                        buf = candidate
                    else:
                        # mid-row overflow: flush 후 같은 행 컨텍스트로 새 청크 시작
                        if buf:
                            chunks.append(prefix + table_ctx + "\n\n" + buf)
                        buf = f"{cur_row_ctx}\n{field}"
                        row_in_chunk = True

        if buf and (max_chunks is None or len(chunks) < max_chunks):
            chunks.append(prefix + table_ctx + "\n\n" + buf)

    return chunks


# ── 공용 진입점 ────────────────────────────────────────────────────────────────

def chunk(text: str, metadata: dict) -> list[str]:
    """일반 문서용 진입점. pipeline.py에서 직접 호출."""
    return chunk_unstructured_text(text, metadata)
