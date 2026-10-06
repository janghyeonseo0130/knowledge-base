"""
MCP 서버 (HTTP/SSE transport — 다중 사용자 Ubuntu 서버용)

노출 도구 (5개):
  search_knowledge  — 하이브리드 검색 (RRF + 문서 단위 중복 제거 + Reranker)
  get_document      — 파일 경로로 전체 문서 조회 (청크 페이지네이션)
  list_documents    — 인덱싱된 문서 목록 조회 (메타데이터 필터 지원)
  reindex           — 지정 폴더 증분 업데이트
  get_stats         — 인덱스 통계

HTTP 엔드포인트:
  POST /reindex       — watcher가 파일 변경 감지 시 호출 (background thread 처리)
  POST /rebuild-bm25  — BM25S 수동 즉시 rebuild
"""
from __future__ import annotations
import logging
import os
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

from mcp.server.fastmcp import FastMCP
from config import USE_RERANKER, MCP_HOST, MCP_PORT, REBUILD_DEBOUNCE_MINUTES
from indexer import Embedder, VectorStore, BM25Index, IncrementalIndexer
from dashboard import get_dashboard_stats
from search import HybridSearch
from starlette.requests import Request
from starlette.responses import JSONResponse
import pipeline

log = logging.getLogger(__name__)

mcp = FastMCP("knowledge-base", host=MCP_HOST, port=MCP_PORT)

_search:     HybridSearch | None = None
_search_lock = threading.Lock()

_indexing_lock    = threading.Lock()          # 동시 인덱싱 방지
_rebuild_timer:    threading.Timer | None = None
_rebuild_timer_lock = threading.Lock()


# ── 검색 컴포넌트 초기화 ───────────────────────────────────────────────────────

def _get_search() -> HybridSearch:
    global _search
    if _search is not None:
        return _search
    with _search_lock:
        if _search is None:
            embedder = Embedder()
            vs       = VectorStore()
            bm25     = BM25Index()
            reranker = None
            if USE_RERANKER:
                from search import Reranker
                reranker = Reranker()
            _search = HybridSearch(embedder, vs, bm25, reranker)
    return _search


# ── rebuild 스케줄링 ──────────────────────────────────────────────────────────

def _schedule_rebuild() -> None:
    """debounce: 마지막 /reindex 후 REBUILD_DEBOUNCE_MINUTES분 뒤 rebuild"""
    global _rebuild_timer
    with _rebuild_timer_lock:
        if _rebuild_timer is not None:
            _rebuild_timer.cancel()
        _rebuild_timer = threading.Timer(
            REBUILD_DEBOUNCE_MINUTES * 60, _do_rebuild
        )
        _rebuild_timer.daemon = True
        _rebuild_timer.start()
    log.info(f"[BM25S] rebuild 예약 ({REBUILD_DEBOUNCE_MINUTES}분 후)")


def _do_rebuild() -> None:
    hs = _get_search()
    log.info("[BM25S] debounce 완료 → rebuild 시작")
    hs.bm25.rebuild_and_swap()


# ── HTTP 엔드포인트 ───────────────────────────────────────────────────────────

@mcp.custom_route("/api/stats", methods=["GET"])
async def stats_endpoint(request: Request) -> JSONResponse:
    """관리자 대시보드용 통계 JSON"""
    return JSONResponse(get_dashboard_stats(_indexing_lock, _search))


_DASHBOARD_HTML = (Path(__file__).parent / "dashboard.html").read_text(encoding="utf-8")


@mcp.custom_route("/dashboard", methods=["GET"])
async def dashboard_endpoint(request: Request):
    """관리자 대시보드 HTML 페이지"""
    from starlette.responses import HTMLResponse
    return HTMLResponse(_DASHBOARD_HTML)


@mcp.custom_route("/reindex", methods=["POST"])
async def reindex_endpoint(request: Request) -> JSONResponse:
    """
    watcher가 파일 변경 감지 시 호출.
    background thread에서 인덱싱 후 rebuild debounce 타이머 리셋.
    """
    try:
        data = await request.json()
    except Exception:
        data = {}
    folder_path = data.get("folder_path", "")
    if not folder_path:
        return JSONResponse({"status": "error", "message": "folder_path required"}, status_code=400)

    def _run():
        with _indexing_lock:
            try:
                hs = _get_search()
                pipeline.run(
                    folder_path,
                    embedder=hs.embedder,
                    vector_store=hs.vector_store,
                    bm25=hs.bm25,
                    rebuild_bm25=False,
                )
                _schedule_rebuild()
            except Exception as e:
                log.error(f"[/reindex] 오류: {e}", exc_info=True)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return JSONResponse({"status": "accepted"})


@mcp.custom_route("/rebuild-bm25", methods=["POST"])
async def rebuild_bm25_endpoint(request: Request) -> JSONResponse:
    """BM25S 수동 즉시 rebuild"""
    def _run():
        _do_rebuild()

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return JSONResponse({"status": "accepted"})


# ── MCP Tools ─────────────────────────────────────────────────────────────────

@mcp.tool()
def search_knowledge(query: str, top_k: int = 5, folder: str = "", file_filter: str = "", date_from: str = "", date_to: str = "", latest_only: bool = False) -> str:
    """
    지식베이스 하이브리드 검색 (벡터 + BM25 + RRF 병합 + 문서 단위 중복 제거).

    Args:
        query:       검색 쿼리
        top_k:       반환할 결과 수 (기본 5)
        folder:      경로에 이 문자열이 포함된 문서만 검색 (예: "a사업")
        file_filter: 파일명에 이 문자열이 포함된 문서만 검색 (예: "기획서")
        date_from:   이 날짜 이후 문서만 검색 (예: "2024-01-01")
        date_to:     이 날짜 이전 문서만 검색 (예: "2024-12-31")
        latest_only: True면 유사 이름 문서 중 최신 파일만 반환 (버전 중복 제거)
    """
    hs = _get_search()
    results = hs.search(
        query, top_k=top_k,
        folder=folder or None, file_filter=file_filter or None,
        date_from=date_from or None, date_to=date_to or None,
        latest_only=latest_only,
    )

    if not results:
        return "검색 결과가 없습니다."

    lines = []
    for i, r in enumerate(results, 1):
        meta    = r.get("metadata", {})
        title   = meta.get("title") or meta.get("filename", "제목 없음")
        score   = r.get("rerank_score", r.get("rrf_score", 0.0))
        snippet = r["content"][:500].replace("\n", " ")
        lines += [
            f"[{i}] {title}",
            f"    파일   : {meta.get('filename', '')}",
            f"    경로   : {meta.get('source', '')}",
            f"    날짜   : {meta.get('modified_at', '')[:10]}",
            f"    점수   : {score:.4f}",
            f"    내용   : {snippet}...",
            "",
        ]
    return "\n".join(lines)


@mcp.tool()
def get_document(file_path: str, chunk_offset: int = 0, chunk_limit: int = 20) -> str:
    """
    파일 경로로 문서 전체 내용 조회 (청크 페이지네이션 지원).

    Args:
        file_path:    문서 절대 경로
        chunk_offset: 시작 청크 번호 (기본 0)
        chunk_limit:  가져올 청크 수 (기본 20, 긴 문서는 여러 번 호출)
    """
    vs     = _get_search().vector_store
    chunks = vs.get_by_source(file_path, offset=chunk_offset, limit=chunk_limit)

    if not chunks:
        return f"문서를 찾을 수 없습니다: {file_path}"

    meta  = chunks[0]["metadata"]
    total = meta.get("total_chunks", "?")
    lines = [
        f"파일   : {file_path}",
        f"제목   : {meta.get('title', '')}",
        f"날짜   : {meta.get('modified_at', '')[:10]}",
        f"청크   : {chunk_offset + 1}~{chunk_offset + len(chunks)} / {total}",
        "",
    ]
    for c in chunks:
        lines.append(c["content"])
        lines.append("")
    return "\n".join(lines)


@mcp.tool()
def list_documents(folder: str = "", extension: str = "", sort_by: str = "", limit: int = 50) -> str:
    """
    인덱싱된 문서 목록을 조회합니다.

    Args:
        folder:    경로에 이 문자열이 포함된 문서만 (예: "a사업")
        extension: 확장자 필터 (예: ".pdf", ".hwp")
        sort_by:   정렬 기준 ("recent": 최근 인덱싱 순, 기본값: 경로 알파벳순)
        limit:     최대 반환 수 (기본 50, 0이면 전체)
    """
    incremental = IncrementalIndexer()
    rows = incremental.all_indexed_paths()
    incremental.close()

    if folder:
        rows = [r for r in rows if folder.replace("\\", "/") in r["file_path"].replace("\\", "/")]
    if extension:
        ext = extension if extension.startswith(".") else "." + extension
        rows = [r for r in rows if r["file_path"].lower().endswith(ext)]

    if sort_by == "recent":
        rows = sorted(rows, key=lambda r: (r["modified_at"] or r["indexed_at"] or "")[:10], reverse=True)
    else:
        rows = sorted(rows, key=lambda r: r["file_path"])

    total_count = len(rows)
    if limit > 0:
        rows = rows[:limit]

    if not rows:
        return "조건에 맞는 문서가 없습니다."

    header = f"총 {total_count}개 문서"
    if limit > 0 and total_count > limit:
        header += f" (최근 {limit}개 표시)" if sort_by == "recent" else f" (상위 {limit}개 표시)"
    lines = [header + "\n"]
    for r in rows:
        name = Path(r["file_path"]).name
        date = (r["modified_at"] or r["indexed_at"] or "")[:10]
        lines.append(f"  [{date}] {name}")
        lines.append(f"           {r['file_path']}")
    return "\n".join(lines)


@mcp.tool()
def reindex(folder_path: str) -> str:
    """
    지정한 폴더를 증분 업데이트합니다 (변경된 파일만 재처리).

    Args:
        folder_path: 업데이트할 폴더 절대 경로
    """
    with _indexing_lock:
        hs = _get_search()
        stats = pipeline.run(
            folder_path,
            embedder=hs.embedder,
            vector_store=hs.vector_store,
            bm25=hs.bm25,
            rebuild_bm25=False,
        )
    _schedule_rebuild()
    return f"업데이트 완료: 성공 {stats['success']}  스킵 {stats['skip']}  오류 {stats['error']}"


@mcp.tool()
def get_stats() -> str:
    """인덱스 현황 통계를 반환합니다."""
    incremental = IncrementalIndexer()
    db_stats = incremental.stats()
    incremental.close()

    vs = VectorStore()
    chunk_count = vs.count()

    lines = [
        f"인덱싱된 파일 수 : {db_stats['total_files']}",
        f"총 청크 수       : {chunk_count}",
        f"마지막 업데이트  : {(db_stats['last_indexed'] or '-')[:19]}",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run(transport="sse", host=MCP_HOST, port=MCP_PORT)
