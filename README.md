# Knowledge Base

업무 문서를 공유 서버에 인덱싱하고 AI 에이전트(MCP)를 통해 검색하는 팀 공용 지식 데이터베이스.

## 주요 기능

- **하이브리드 검색** — 벡터 검색(BGE-M3) + BM25 키워드 검색을 RRF(Reciprocal Rank Fusion)로 병합
- **한국어 특화** — Kiwi 형태소 분석, 글자 단위 청킹, 컨텍스트 프리픽스(`[날짜 | 제목 | 작성자 | 파일명]`)
- **다중 사용자** — HTTP/SSE 기반 MCP 서버, 동시 접속 지원
- **자동 인덱싱** — rclone으로 팀원 PC → 서버 동기화, watchdog으로 변경 감지 후 자동 증분 인덱싱
- **대형 표 처리** — wide table은 field-aware greedy packing, 초대형 표는 structural chunk(스키마 요약)로 자동 전환
- **OCR** — 스캔본 PDF, docx/pptx 삽입 이미지 텍스트 추출 (EasyOCR)
- **관리자 대시보드** — `http://서버:8000/dashboard` 에서 인덱싱 현황 실시간 확인

## 지원 파일 형식

| 형식 | 확장자 |
|------|--------|
| 문서 | `.pdf` `.docx` `.pptx` `.hwp` `.hwpx` `.rtf` |
| 스프레드시트 | `.xlsx` `.xls` `.csv` |
| 텍스트 | `.txt` `.md` `.html` `.htm` |
| Apple | `.pages` `.numbers` `.key` |

## 아키텍처

```
팀원 PC
  └─ rclone (5분 주기) ──→ 서버 uploads/
                                  │
                              watchdog
                                  │ 변경 감지
                                  ↓
                            pipeline.py
                         파싱 → 청킹 → 임베딩
                                  │
                    ┌─────────────┴─────────────┐
                ChromaDB                    BM25S
              (벡터 검색)               (키워드 검색)
                    └─────────────┬─────────────┘
                                RRF 병합
                                  │
                            MCP Server (SSE)
                                  │
                          Claude / AI 에이전트
```

## 설치

```bash
git clone https://github.com/janghyeonseo0130/knowledge-base.git
cd knowledge-base
pip install -r requirements.txt
```

> **모델 사전 다운로드 필요** — 인터넷 차단 환경에서는 `BAAI/bge-m3` 모델을 USB 등으로 미리 복사 후 `HF_HUB_OFFLINE=1` 설정

## 실행

```bash
# 폴더 인덱싱
python main.py index <폴더경로>

# MCP 서버 시작 (watcher 포함)
python main.py mcp

# 검색 테스트
python main.py search "검색어" [top_k] [--folder <경로>] [--file <파일명>]

# 특정 파일/폴더 인덱스에서 삭제
python main.py delete <경로>
```

### systemd 서비스 등록 (서버 재부팅 시 자동 시작)

```bash
sudo systemctl enable knowledge-base-mcp
sudo systemctl start knowledge-base-mcp
```

## MCP 도구 (5개)

| 도구 | 설명 |
|------|------|
| `search_knowledge` | 하이브리드 검색. `folder`, `file_filter`, `date_from`, `date_to`, `latest_only` 필터 지원 |
| `get_document` | 파일 경로로 전체 문서 조회 (청크 페이지네이션) |
| `list_documents` | 인덱싱된 문서 목록 조회. 확장자·경로·날짜순 필터 지원 |
| `reindex` | 지정 폴더 증분 업데이트 (변경된 파일만 재처리) |
| `get_stats` | 인덱스 현황 통계 (파일 수, 총 청크 수, 마지막 업데이트) |

## 설정 (`config.yaml`)

| 항목 | 기본값 | 설명 |
|------|--------|------|
| `chunking.size` | 1200 | 청크 크기 (글자 수) |
| `chunking.overlap` | 150 | 청크 오버랩 (글자 수) |
| `chunking.max_chunks_per_file` | null | 파일당 최대 청크 수 (안전 fuse) |
| `table.full_index_chunk_limit` | 10000 | 이 값 초과 예상 시 structural chunk로 전환 |
| `search.vector_top_k` | 50 | 벡터 검색 후보 수 |
| `search.bm25_top_k` | 50 | BM25 검색 후보 수 |
| `reranker.enabled` | false | Reranker 활성화 여부 |
| `mcp.port` | 8000 | MCP 서버 포트 |
| `watcher.debounce_seconds` | 30 | 파일 변경 감지 후 인덱싱 대기 시간 |
| `ocr.enabled` | true | OCR 활성화 여부 |
