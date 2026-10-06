"""
인코딩 진단 스크립트

Ubuntu 서버에서 실행해 한글 깨짐 원인을 레이어별로 확인.

사용법:
  cd ~/knowledge-base
  python test/diagnose_encoding.py
  python test/diagnose_encoding.py "검색쿼리"  # 특정 쿼리로 테스트
"""
from __future__ import annotations
import sys, os, locale

# ── 1. 환경 정보 ──────────────────────────────────────────────────────────────
print("=" * 60)
print("[1] 환경 정보")
print("=" * 60)
print(f"  Python         : {sys.version}")
print(f"  sys.stdout.encoding : {sys.stdout.encoding}")
print(f"  sys.getdefaultencoding : {sys.getdefaultencoding()}")
print(f"  locale.getpreferredencoding : {locale.getpreferredencoding()}")
print(f"  LANG           : {os.environ.get('LANG', '(없음)')}")
print(f"  LC_ALL         : {os.environ.get('LC_ALL', '(없음)')}")
print(f"  PYTHONIOENCODING : {os.environ.get('PYTHONIOENCODING', '(없음)')}")

# stdout에 한글 직접 출력 테스트
print("\n[stdout 한글 직접 출력 테스트]")
try:
    sys.stdout.write("  테스트: 가나다라마바사\n")
    sys.stdout.flush()
    print("  → stdout 출력 성공")
except Exception as e:
    print(f"  → stdout 출력 실패: {e}")

# ── 2. ChromaDB 저장 데이터 확인 ─────────────────────────────────────────────
print("\n" + "=" * 60)
print("[2] ChromaDB 저장 데이터 확인")
print("=" * 60)

try:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from indexer import VectorStore
    vs = VectorStore()
    total = vs.count()
    print(f"  총 청크 수: {total}")

    # 첫 번째 청크 샘플
    sample = vs.col.get(limit=3, include=["documents", "metadatas"])
    for i, (doc, meta) in enumerate(zip(sample["documents"], sample["metadatas"])):
        print(f"\n  [샘플 {i+1}]")
        print(f"    source   : {meta.get('source', '')[:80]}")
        print(f"    title    : {meta.get('title', '')[:50]}")
        print(f"    내용(앞 80자): {doc[:80]!r}")
        print(f"    내용 출력: {doc[:80]}")
except Exception as e:
    print(f"  ChromaDB 확인 실패: {e}")

# ── 3. 검색 결과 인코딩 확인 ─────────────────────────────────────────────────
print("\n" + "=" * 60)
print("[3] 검색 결과 인코딩 확인")
print("=" * 60)

query = sys.argv[1] if len(sys.argv) > 1 else "보고서"
print(f"  쿼리: {query!r}")

try:
    from indexer import Embedder, VectorStore, BM25Index
    from search import HybridSearch

    embedder = Embedder()
    vs       = VectorStore()
    bm25     = BM25Index()
    hs       = HybridSearch(embedder, vs, bm25)
    results  = hs.search(query, top_k=3)

    if not results:
        print("  검색 결과 없음")
    else:
        for i, r in enumerate(results, 1):
            meta    = r.get("metadata", {})
            content = r["content"][:200]
            print(f"\n  [결과 {i}]")
            print(f"    title repr : {meta.get('title', '')!r}")
            print(f"    title 출력 : {meta.get('title', '')}")
            print(f"    source     : {meta.get('source', '')[:80]}")
            print(f"    내용 repr  : {content!r}")
            print(f"    내용 출력  : {content}")
except Exception as e:
    print(f"  검색 실패: {e}")

# ── 4. MCP 툴 반환값 확인 ────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("[4] MCP 툴 반환값 → JSON 직렬화 테스트")
print("=" * 60)

try:
    import json
    sample_text = "검색 결과: 가나다라 테스트 문서입니다."

    # ensure_ascii=True (Python 기본값) — mcp SDK가 이 방식 사용하면 \uXXXX 이스케이프
    json_ascii   = json.dumps({"text": sample_text}, ensure_ascii=True)
    # ensure_ascii=False — 원본 UTF-8 그대로
    json_unicode = json.dumps({"text": sample_text}, ensure_ascii=False)

    print(f"  ensure_ascii=True  : {json_ascii}")
    print(f"  ensure_ascii=False : {json_unicode}")
    print(f"  → 복원(ascii)  : {json.loads(json_ascii)['text']}")
    print(f"  → 복원(unicode): {json.loads(json_unicode)['text']}")

    # bytes 확인
    print(f"\n  UTF-8 바이트(ensure_ascii=False): {json_unicode.encode('utf-8')[:80]}")
except Exception as e:
    print(f"  JSON 테스트 실패: {e}")

# ── 5. FastMCP SSE 응답 헤더 확인 ─────────────────────────────────────────────
print("\n" + "=" * 60)
print("[5] FastMCP SSE Content-Type 헤더 확인")
print("=" * 60)
print("  MCP 서버가 실행 중이라면 별도 터미널에서:")
print("  curl -s -I http://localhost:8000/sse | grep -i content-type")
print("  → 'text/event-stream; charset=utf-8' 이어야 함")
print("  → charset 없으면 일부 클라이언트가 latin-1로 해석해 한글 깨질 수 있음")

print("\n" + "=" * 60)
print("진단 완료")
print("=" * 60)
