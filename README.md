# 공용 지식 플랫폼

팀원들의 업무 문서를 하나의 서버에 모아두고, AI 에이전트가 자연어로 검색·인용할 수 있게 하는 시스템.

| 항목 | 내용 |
|------|------|
| 대상 규모 | 팀원 20명 이상 |
| 문서 규모 | 1인 평균 400GB, 총 ~8TB |
| 서버 | Ubuntu (i9-14900KF · RAM 62GB · RTX 4060 Ti 8GB · NVMe 1.8TB) |

---

## 전체 흐름

```
팀원 PC
  └─ rclone (5분 주기 자동 업로드)
        ↓
  Ubuntu 서버 uploads/
        ↓
  watchdog (파일 변경 감지)
        ↓
  pipeline: 파싱 → 청킹 → 임베딩
        ↓
  ┌─────────────┬─────────────┐
  ChromaDB      BM25S 인덱스
  (벡터 검색)    (키워드 검색)
  └─────────────┴─────────────┘
        ↓ RRF 병합
  MCP Server (HTTP/SSE, :8000)
        ↓
  Claude / AI 에이전트
```

---

## 주요 기능

- **하이브리드 검색** — 벡터(BGE-M3) + BM25S 키워드 검색을 RRF로 병합. 의미 기반 + 키워드 매칭 동시 적용
- **한국어 특화** — Kiwi 형태소 분석, 글자 단위 청킹(1,200자), 컨텍스트 프리픽스 자동 삽입
- **다중 사용자** — HTTP/SSE 기반 MCP 서버, 동시 접속 지원
- **증분 인덱싱** — SHA-256 해시 기반 변경 감지. 변경된 파일만 재처리
- **스프레드시트 처리** — field-aware greedy packing으로 wide table ~30% 청크 감소. 초대형 표(예상 청크 > 10,000)는 스키마 요약 청크로 자동 전환
- **OCR** — 스캔본 PDF, docx/pptx 삽입 이미지 텍스트 추출 (EasyOCR, GPU 실행)
- **메모리 효율** — BM25S sparse matrix 구조로 76만 청크 기준 ~3GB (기존 대비 약 10배 절감)

---

## 검색 파이프라인 (8단계)

| # | 단계 | 내용 |
|---|------|------|
| 1 | 벡터 검색 | 쿼리 임베딩 후 ChromaDB에서 유사 청크 50개 |
| 2 | BM25 검색 | Kiwi 형태소 분석 후 키워드 매칭 청크 50개 |
| 3 | RRF 병합 | 순위 기반 합산으로 두 결과 통합 |
| 4 | 필터 | folder / 파일명 / 날짜 범위 조건 적용 |
| 5 | 문서 단위 dedup | 같은 파일의 청크 중 최고점 1개만 |
| 6 | 최신 버전 필터 | `latest_only=True` 시 비슷한 이름 중 최신 파일만 |
| 7 | 유령 청크 제거 | 삭제된 파일의 잔존 청크 제거 |
| 8 | Reranker | (선택) 쿼리-문서 쌍 직접 비교로 최종 순위 재정렬 |

---

## MCP 도구 (5개)

| 도구 | 설명 |
|------|------|
| `search_knowledge` | 하이브리드 검색. `folder`, `file_filter`, `date_from`, `date_to`, `latest_only` 필터 지원 |
| `get_document` | 파일 경로로 전체 문서 조회 (청크 페이지네이션) |
| `list_documents` | 인덱싱된 문서 목록 조회. 확장자·경로·날짜순 필터 지원 |
| `reindex` | 지정 폴더 증분 업데이트 |
| `get_stats` | 인덱스 현황 통계 |

```python
search_knowledge("1분기 사업 목표가 뭐야?")
search_knowledge("계약 리스크 검토", folder="a사업", date_from="2024-01-01")
search_knowledge("사업계획서", file_filter="최종", latest_only=True)
```

---

## 지원 파일 형식

| 카테고리 | 확장자 | 비고 |
|----------|--------|------|
| 문서 | `.hwp` `.hwpx` `.pdf` `.docx` `.rtf` | 스캔본 PDF → OCR 자동 적용 |
| 프레젠테이션 | `.pptx` `.key` | 슬라이드 텍스트 + 삽입 이미지 OCR |
| 스프레드시트 | `.xlsx` `.xls` `.csv` `.numbers` | 시트별 텍스트 추출 |
| 텍스트/웹 | `.txt` `.md` `.html` `.htm` | 인코딩 자동 감지 |
| Apple iWork | `.pages` `.numbers` `.key` | ZIP 기반 XML 파싱 |

---

## 설치 및 실행

```bash
git clone https://github.com/janghyeonseo0130/knowledge-base.git
cd knowledge-base
pip install -r requirements.txt

python main.py index <폴더경로>   # 인덱싱
python main.py mcp                # MCP 서버 시작
python main.py search "검색어"    # 검색 테스트
python main.py delete <경로>      # 인덱스에서 삭제
```

---

## 운영

```bash
sudo systemctl status knowledge-base-mcp
sudo systemctl status knowledge-watcher

journalctl -u knowledge-base-mcp -f
tail -f ~/knowledge-base/data/index.log
```

관리자 대시보드: `http://서버IP:8000/dashboard`  
표시 항목: 인덱싱 성공률 · 팀원별 파일 수 · 대기 파일 수 · 확장자별 분포 · 총 청크 수
