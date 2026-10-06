"""
CLI 진입점

사용법:
  python main.py index  <폴더경로>
  python main.py search <쿼리> [top_k] [--folder <경로>] [--file <파일명>]
  python main.py delete <파일절대경로>
  python main.py mcp
"""
from __future__ import annotations
import sys

# HTTP/SSE transport 사용으로 stdout은 프로토콜 통신에 쓰이지 않음 → 인코딩 설정 안전
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def cmd_delete(target: str) -> None:
    from pathlib import Path
    from indexer import VectorStore, BM25Index
    from indexer.incremental import IncrementalIndexer

    target_path = target.rstrip("/").rstrip("\\")
    inc = IncrementalIndexer()
    all_rows = inc.all_indexed_paths()

    # 폴더면 하위 파일 전체, 파일이면 해당 파일만
    targets = [
        Path(r["file_path"]) for r in all_rows
        if r["file_path"] == target_path or r["file_path"].startswith(target_path + "/") or r["file_path"].startswith(target_path + "\\")
    ]

    if not targets:
        print(f"인덱스에 없습니다: {target}")
        inc.close()
        return

    all_chunk_ids = []
    for fp in targets:
        all_chunk_ids.extend(inc.get_chunk_ids(fp))

    VectorStore().delete(all_chunk_ids)

    bm25 = BM25Index()
    bm25.remove(all_chunk_ids)
    bm25.rebuild_and_swap()

    for fp in targets:
        inc.remove(fp)
    inc.close()
    print(f"삭제 완료: {len(targets)}개 파일  ({len(all_chunk_ids)}개 청크)")


def cmd_index(folder_path: str) -> None:
    import pipeline
    pipeline.run(folder_path)


def cmd_search(query: str, top_k: int = 5, folder: str | None = None, file_filter: str | None = None) -> None:
    from config import USE_RERANKER
    from indexer import Embedder, VectorStore, BM25Index
    from search  import HybridSearch

    embedder = Embedder()
    vs       = VectorStore()
    bm25     = BM25Index()
    reranker = None
    if USE_RERANKER:
        from search import Reranker
        reranker = Reranker()

    hs      = HybridSearch(embedder, vs, bm25, reranker)
    results = hs.search(query, top_k=top_k, folder=folder, file_filter=file_filter)

    if not results:
        print("검색 결과 없음")
        return

    score_key = "rerank_score" if USE_RERANKER else "rrf_score"
    for i, r in enumerate(results, 1):
        meta    = r.get("metadata", {})
        title   = meta.get("title") or meta.get("filename", "?")
        source  = meta.get("source", "")
        snippet = r["content"][:300].replace("\n", " ")
        print(f"\n[{i}] {title}  (score={r.get(score_key, 0):.4f})")
        print(f"    경로: {source}")
        print(f"    {snippet}...")


def cmd_mcp() -> None:
    from config import MCP_HOST, MCP_PORT
    from mcp_server.server import mcp, _get_search
    print(f"[MCP] 검색 컴포넌트 초기화 중... (모델 로딩)", flush=True)
    _get_search()  # 클라이언트 연결 전에 미리 로딩 완료
    print(f"[MCP] 초기화 완료. SSE 서버 시작: http://{MCP_HOST}:{MCP_PORT}", flush=True)
    mcp.run(transport="sse")


def main() -> None:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(0)

    cmd = args[0].lower()

    if cmd == "delete" and len(args) >= 2:
        cmd_delete(args[1])

    elif cmd == "index" and len(args) >= 2:
        cmd_index(args[1])

    elif cmd == "search" and len(args) >= 2:
        query  = args[1]
        top_k  = int(args[2]) if len(args) >= 3 and args[2] not in ("--folder", "--file") else 5
        folder = None
        file_filter = None
        if "--folder" in args:
            fi = args.index("--folder")
            folder = args[fi + 1] if fi + 1 < len(args) else None
        if "--file" in args:
            fi = args.index("--file")
            file_filter = args[fi + 1] if fi + 1 < len(args) else None
        cmd_search(query, top_k, folder, file_filter)

    elif cmd == "watch" and len(args) >= 2:
        from watcher import watch
        watch(args[1])

    elif cmd == "mcp":
        cmd_mcp()

    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
