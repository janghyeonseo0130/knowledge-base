# 공용 지식 플랫폼

팀원들의 업무 문서를 하나의 서버에 모아두고, AI 에이전트가 자연어로 검색·인용할 수 있게 하는 시스템.

| 항목 | 내용 |
|------|------|
| 대상 규모 | 팀원 20명 이상 |
| 문서 규모 | 1인 평균 400GB, 총 ~8TB |
| 서버 | Ubuntu (i9-14900KF · RAM 62GB · RTX 4060 Ti 8GB · NVMe 1.8TB) |

---

## 무엇을 해결하는가

파일 서버에 문서를 올려두면 파일명으로만 검색 가능하고, 수천 개가 넘으면 사람이 직접 찾는 것도 한계가 생긴다.

이 시스템은 모든 문서의 내용을 미리 인덱싱해두고, _"작년 A사업 계약 관련 리스크 검토한 거 있어?"_ 같은 자연어 질문으로 관련 문서를 즉시 찾아준다. Claude 같은 AI 에이전트가 검색 결과를 직접 인용해서 답변하는 RAG(Retrieval-Augmented Generation) 구조다.

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

## 검색 파이프라인 (8단계)

| # | 단계 | 내용 |
|---|------|------|
| 1 | 벡터 검색 | 쿼리를 임베딩 후 ChromaDB에서 유사 청크 50개 |
| 2 | BM25 검색 | Kiwi 형태소 분석 후 키워드 매칭 청크 50개 |
| 3 | RRF 병합 | 점수 대신 순위 기반으로 합산 → 점수 스케일 차이 제거 |
| 4 | 필터 | folder / 파일명 / 날짜 범위 조건 적용 |
| 5 | 문서 단위 dedup | 같은 파일의 청크 중 최고점 1개만 남김 |
| 6 | 최신 버전 필터 | `latest_only=True` 시 비슷한 이름 중 최신 파일만 |
| 7 | 유령 청크 제거 | 삭제된 파일의 청크가 BM25 rebuild 전 잔존하는 문제 제거 |
| 8 | Reranker | (선택) 쿼리-문서 쌍 직접 비교로 최종 순위 재정렬 |

---

## 주요 기술 선택과 이유

### 임베딩 — BAAI/bge-m3
- 다국어 임베딩 벤치마크(MTEB) 한국어 최상위권
- 최대 8,192 토큰 처리 → 긴 보고서 청크도 잘림 없음
- 로컬 GPU 실행 (외부 API 불필요, 인터넷 차단 환경 대응)
- FP16 실행 시 VRAM ~1.2GB → RTX 4060 Ti 8GB에서 여유 있음

### BM25S (기존 rank-bm25 대비)

| 항목 | rank-bm25 | BM25S |
|------|-----------|-------|
| 메모리 (76만 청크 기준) | 30~38 GB | ~3 GB |
| 서버 재시작 시 | 전체 재빌드 (수십 분) | 덤프 파일에서 즉시 로드 (수 초) |

### Kiwi 형태소 분석
"보고서를", "보고서가", "보고서에서" → 조사 제거 후 "보고서"로 통일 인덱싱.  
단순 공백 분리 시 세 단어가 전혀 다른 단어로 처리되는 문제 해결.

### BM25 rebuild 전략
파일 추가마다 즉시 rebuild하면 대량 인덱싱 시 수십 번 반복된다.  
마지막 변경 후 **5분 동안 추가 변경이 없을 때 한 번만 rebuild** → 비효율 제거.

### OOM 문제 해결
초기에 watcher와 MCP 서버가 별도 프로세스로 BGE-M3 모델을 각각 로드 → VRAM 2배 사용 → OOM.  
현재는 watcher가 파일 감지만 담당하고, 인덱싱은 MCP 서버 내부에서 실행 → **모델 1벌로 유지**.

### 한국어 청킹
- 글자 단위 (1,200자, 오버랩 150자) → 한국어 어절 길이 편차 문제 없음
- 구두점 기반 문장 경계 인식 → 문장 중간 절단 방지
- 컨텍스트 프리픽스 자동 삽입: `[2024-03 | 1분기 사업계획서 | 홍길동 | 파일명.hwp]`
- 스프레드시트: field-aware greedy packing으로 wide table 처리 (~30% 청크 감소)
- 초대형 표 (예상 청크 > 10,000): 전체 인덱싱 대신 스키마 요약 청크 1개로 전환

### OCR
스캔본 PDF, docx/pptx 삽입 이미지에서 텍스트 추출 (EasyOCR, GPU 실행).  
100×100px 미만 이미지(로고, 아이콘)는 자동 제외.

---

## MCP 도구 (5개)

| 도구 | 설명 |
|------|------|
| `search_knowledge` | 하이브리드 검색. `folder`, `file_filter`, `date_from`, `date_to`, `latest_only` 필터 지원 |
| `get_document` | 파일 경로로 전체 문서 조회 (청크 페이지네이션) |
| `list_documents` | 인덱싱된 문서 목록 조회. 확장자·경로·날짜순 필터 지원 |
| `reindex` | 지정 폴더 증분 업데이트 (변경 파일만 재처리) |
| `get_stats` | 인덱스 현황 통계 |

```python
# 사용 예시
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

열려있는 Word/Excel 임시 파일(`~$*.docx`), 이미지·동영상·실행파일은 자동 제외.

---

## 파일 동기화

rclone + SFTP로 팀원 PC → 서버 5분 주기 단방향 복사.  
팀원은 평소처럼 파일을 저장하기만 하면 된다.

- **단방향**: PC에서 삭제해도 서버 파일은 유지됨 (실수 방지 의도적 설계)
- **Mac NFD 대응**: Mac 한글 파일명(자모 분리)을 NFC로 자동 변환 후 업로드

---

## 설치 및 실행

```bash
git clone https://github.com/janghyeonseo0130/knowledge-base.git
cd knowledge-base
pip install -r requirements.txt

# 폴더 인덱싱
python main.py index <폴더경로>

# MCP 서버 시작 (watcher 포함)
python main.py mcp

# 검색 테스트
python main.py search "검색어"

# 특정 파일/폴더 인덱스에서 삭제
python main.py delete <경로>
```

> 인터넷 차단 환경: `BAAI/bge-m3` 모델을 미리 로컬에 복사 후 `HF_HUB_OFFLINE=1` 설정

---

## 운영

```bash
# 서비스 상태 확인
sudo systemctl status knowledge-base-mcp
sudo systemctl status knowledge-watcher

# 실시간 로그
journalctl -u knowledge-base-mcp -f
tail -f ~/knowledge-base/data/index.log

# 관리자 대시보드
http://서버IP:8000/dashboard
```

대시보드 표시 항목: 인덱싱 성공률 · 팀원별 파일 수 · 대기 파일 수 · 확장자별 분포 · 총 청크 수
