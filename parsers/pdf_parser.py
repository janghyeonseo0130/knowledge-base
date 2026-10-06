from pathlib import Path
import re
import fitz  # pymupdf
from .base import BaseParser, ParsedDocument
from .ocr_utils import ocr_pdf_page
from config import OCR_ENABLED, OCR_MIN_TEXT_CHARS
from logger import get_logger

log = get_logger("parsers.pdf")

# PDF 폰트 매핑 실패로 생기는 PUA(U+E000~U+F8FF) 문자 제거 패턴
_PUA_RE = re.compile(r'[\ue000-\uf8ff]')


def _fix_pdf_meta(text: str) -> str:
    """PDF 메타데이터 인코딩 깨짐 처리.

    한국어 Windows에서 생성된 PDF는 CP949 바이트가 latin-1로 잘못 해석되는 경우가 있음.
    U+0080~U+00FF 범위 문자가 있으면 CP949 복원 시도. 실패 시 빈 문자열 반환
    → pipeline.py의 infer_title()이 파일명을 대신 사용.
    """
    if not text:
        return ""
    if any('\x80' <= c <= '\xff' for c in text):
        try:
            return text.encode('latin-1').decode('cp949')
        except (UnicodeEncodeError, UnicodeDecodeError):
            return ""
    return text


def _parse_pdf_date(d: str) -> str:
    """PDF 날짜 형식(D:YYYYMMDDHHmmSS...)을 ISO 날짜 문자열로 변환"""
    if not d:
        return ""
    m = re.match(r"D:(\d{4})(\d{2})(\d{2})", d)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return ""


class PDFParser(BaseParser):
    def supports(self, file_path: Path) -> bool:
        return file_path.suffix.lower() == ".pdf"

    def parse(self, file_path: Path) -> ParsedDocument:
        doc = None
        try:
            doc = fitz.open(str(file_path))

            # 페이지별 텍스트 추출 — 일부 페이지 실패해도 나머지는 유지
            pages_text = []
            for page in doc:
                try:
                    text = _PUA_RE.sub('', page.get_text())
                    if OCR_ENABLED and len(text.strip()) < OCR_MIN_TEXT_CHARS:
                        ocr_text = ocr_pdf_page(page)
                        if ocr_text.strip():
                            text = ocr_text
                    pages_text.append(text)
                except Exception as e:
                    log.warning(f"OCR 실패 (페이지 {page.number}, {file_path.name}): {e}")
                    pages_text.append("")
            content = "\n".join(pages_text)

            # 메타데이터 — 실패해도 본문 추출 결과는 유지
            try:
                meta = doc.metadata or {}
            except Exception:
                meta = {}

            metadata = {
                "page_count": doc.page_count,
                "title":   _fix_pdf_meta(meta.get("title", "")),
                "author":  _fix_pdf_meta(meta.get("author", "")),
                "subject": _fix_pdf_meta(meta.get("subject", "")),
            }
            date = (_parse_pdf_date(meta.get("modDate", ""))
                    or _parse_pdf_date(meta.get("creationDate", "")))
            if date:
                metadata["modified_at"] = date

            return ParsedDocument(content=content, metadata=metadata, file_path=str(file_path))

        except Exception:
            return ParsedDocument(content="", metadata={}, file_path=str(file_path))

        finally:
            if doc:
                try:
                    doc.close()
                except Exception:
                    pass
