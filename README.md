# Knowledge Base

업무 문서를 공유 서버에 인덱싱하고 AI 에이전트(MCP)를 통해 검색하는 팀 공용 지식 데이터베이스.

## 주요 기능

- **하이브리드 검색** — 벡터 검색(BGE-M3) + BM25 키워드 검색을 RRF로 병합
- **한국어 특화** — Kiwi 형태소 분석, 글자 단위 청킹, 컨텍스트 프리픽스
- **다중 사용자** — HTTP/SSE 기반 MCP 서버, 동시 접속 지원
- **자동 인덱싱** — rclone으로 팀원 PC → 서버 동기화, watchdog으로 변경 감지 후 자동 인덱싱
- **OCR** — 스캔본 PDF, docx/pptx 삽입 이미지 텍스트 추출
- **지원 형식** — PDF, DOCX, PPTX, XLSX, XLS, CSV, HWP, HWPX, TXT, MD, HTML 등

## 실행

```bash
# 인덱싱
python main.py index <폴더경로>

# MCP 서버 + watcher 시작
python main.py mcp

# 검색 테스트
python main.py search "검색어"
```

## 설정

`config.yaml`에서 청킹 크기, 검색 파라미터, MCP 포트 등 전체 설정 관리.
