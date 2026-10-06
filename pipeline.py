"""
인덱싱 파이프라인 (증분 방식)

흐름:
  폴더 스캔 → 변경 감지 → 파싱 → 청킹(글자 단위 + 컨텍스트 프리픽스)
  → 임베딩 → 저장 / 삭제된 파일 → 기존 청크 제거

외부에서 embedder/vector_store/bm25를 주입하면 기존 인스턴스를 재사용한다.
(서버에서 호출 시 모델 중복 로드 방지)
rebuild_bm25=False로 호출하면 BM25S rebuild를 건너뜀.
(서버는 debounce 후 직접 rebuild_and_swap()을 호출한다)
"""
from __future__ import annotations
import uuid
from pathlib import Path
from tqdm import tqdm

from config import SUPPORTED_EXTENSIONS, MAX_CHUNKS_PER_FILE, TABLE_FULL_INDEX_CHUNK_LIMIT
from parsers import get_parser
from indexer import (
    Embedder, VectorStore, BM25Index, IncrementalIndexer,
    build_metadata, infer_title, chunk, chunk_table_data,
)
from logger import get_logger

log = get_logger("pipeline")


def run(
    folder_path: str,
    embedder:     Embedder | None     = None,
    vector_store: VectorStore | None  = None,
    bm25:         BM25Index | None    = None,
    rebuild_bm25: bool                = True,
) -> dict:
    folder = Path(folder_path)
    if not folder.exists():
        raise FileNotFoundError(f"폴더 없음: {folder_path}")

    _own_embedder = embedder is None
    embedder     = embedder     or Embedder()
    vector_store = vector_store or VectorStore()
    bm25         = bm25         or BM25Index()
    incremental  = IncrementalIndexer()

    all_files     = [
        f for f in folder.rglob("*")
        if f.suffix.lower() in SUPPORTED_EXTENSIONS and not f.name.startswith("~$")
    ]
    current_paths = {str(f) for f in all_files}

    # ── 삭제된 파일 처리 ───────────────────────────────────────────────────────
    deleted_paths = incremental.all_indexed_paths_for_root(str(folder)) - current_paths
    for path_str in deleted_paths:
        old_ids = incremental.get_chunk_ids(Path(path_str))
        if old_ids:
            vector_store.delete(old_ids)
            bm25.remove(old_ids)
        incremental.remove(Path(path_str))
        print(f"[삭제] {Path(path_str).name}")

    # ── 변경/신규 파일 처리 ────────────────────────────────────────────────────
    to_update = [f for f in all_files if incremental.needs_update(f)]
    print(f"\n전체 {len(all_files)}개 | 업데이트 필요 {len(to_update)}개\n")

    stats = {"success": 0, "skip": 0, "error": 0}

    for file_path in tqdm(to_update, desc="인덱싱"):
        parser = get_parser(file_path)
        if parser is None:
            print(f"\n[스킵] {file_path.name}: 지원하지 않는 파서\n       경로: {file_path}")
            log.info(f"[스킵] 지원하지 않는 파서: {file_path}")
            stats["skip"] += 1
            incremental.log_status(file_path, 'skip')
            continue

        try:
            parsed   = parser.parse(file_path)
            metadata = build_metadata(file_path, parsed.metadata)

            if not metadata.get("title"):
                metadata["title"] = infer_title(parsed.content, file_path)

            has_content = bool(parsed.tables) or bool(parsed.content and parsed.content.strip())
            if not has_content:
                print(f"\n[스킵] {file_path.name}: 인덱싱 가능한 텍스트 없음\n       경로: {file_path}")
                log.info(f"[스킵] 인덱싱 가능한 텍스트 없음: {file_path}")
                stats["skip"] += 1
                incremental.log_status(file_path, 'skip')
                continue

            ext = file_path.suffix.lower()
            if ext in {".xlsx", ".xls", ".csv"} and parsed.tables:
                raw_chunks = chunk_table_data(parsed.tables, metadata,
                                              max_chunks=MAX_CHUNKS_PER_FILE,
                                              table_full_index_chunk_limit=TABLE_FULL_INDEX_CHUNK_LIMIT)
            else:
                raw_chunks = chunk(parsed.content, metadata)
            chunks = [c for c in raw_chunks if c.strip()]

            if not chunks:
                print(f"\n[스킵] {file_path.name}: 청크 생성 실패\n       경로: {file_path}")
                log.info(f"[스킵] 청크 생성 실패: {file_path}")
                stats["skip"] += 1
                incremental.log_status(file_path, 'skip')
                continue

            # max_chunks 한도 도달 시 스킵
            if MAX_CHUNKS_PER_FILE is not None and len(chunks) >= MAX_CHUNKS_PER_FILE:
                n_rows = sum(len(t.rows) for t in parsed.tables) if parsed.tables else 0
                msg = (f"[LARGE_TABLE 스킵] {file_path.name}: "
                       f"chunks={len(chunks):,}, rows={n_rows:,}, limit={MAX_CHUNKS_PER_FILE:,}")
                log.warning(msg)
                print(f"\n{msg}\n       경로: {file_path}")
                stats["skip"] += 1
                incremental.log_status(file_path, 'skip')
                continue

            # 기존 청크 삭제
            old_ids = incremental.get_chunk_ids(file_path)
            if old_ids:
                vector_store.delete(old_ids)
                bm25.remove(old_ids)

            # 배치 임베딩 & 저장 (500청크 단위 → 메모리 누적 방지)
            _BATCH = 500
            chunk_ids = []
            for _b in range(0, len(chunks), _BATCH):
                _batch      = chunks[_b:_b + _BATCH]
                _batch_ids  = [str(uuid.uuid4()) for _ in _batch]
                _batch_emb  = embedder.encode(_batch)
                _batch_meta = [
                    {**metadata, "chunk_index": _b + i, "total_chunks": len(chunks)}
                    for i in range(len(_batch))
                ]
                vector_store.add(_batch_ids, _batch_emb, _batch, _batch_meta)
                bm25.add(_batch_ids, _batch)
                chunk_ids.extend(_batch_ids)

            incremental.save(file_path, chunk_ids, str(folder), metadata.get("modified_at", ""))
            incremental.log_status(file_path, 'success')

            stats["success"] += 1

        except Exception as e:
            import traceback
            print(f"\n[오류] {file_path.name}: {e}\n       경로: {file_path}")
            traceback.print_exc()
            log.error(f"[오류] {file_path}: {e}", exc_info=True)
            stats["error"] += 1
            incremental.log_status(file_path, 'error')

    # 마지막 인덱싱 결과 저장 (대시보드용)
    from datetime import datetime
    incremental.conn.executemany(
        "INSERT OR REPLACE INTO metadata (key, value) VALUES (?, ?)",
        [
            ("last_index_success", str(stats["success"])),
            ("last_index_skip",    str(stats["skip"])),
            ("last_index_error",   str(stats["error"])),
        ]
    )
    incremental.conn.commit()
    incremental.close()

    if rebuild_bm25:
        bm25.rebuild_and_swap()

    print(f"\n완료 - 성공: {stats['success']}  스킵: {stats['skip']}  오류: {stats['error']}")
    return stats


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("사용법: python pipeline.py <폴더경로>")
        sys.exit(1)
    run(sys.argv[1])
