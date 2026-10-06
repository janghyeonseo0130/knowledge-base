"""
EasyOCR 유틸리티 — lazy 초기화 + 이미지→텍스트 변환

- Reader는 첫 OCR 호출 시 한 번만 초기화 (모델 로드)
- OCR_ENABLED=False면 항상 빈 문자열 반환
- 실패 시 빈 문자열 반환 (인덱싱 중단 방지)
"""
from __future__ import annotations
import logging
import numpy as np
from config import OCR_ENABLED, OCR_GPU, OCR_LANGUAGES

log = logging.getLogger(__name__)

_reader = None


def _get_reader():
    global _reader
    if _reader is None:
        import easyocr
        log.info(f"[OCR] EasyOCR 초기화 (gpu={OCR_GPU}, languages={OCR_LANGUAGES})")
        _reader = easyocr.Reader(OCR_LANGUAGES, gpu=OCR_GPU)
        log.info("[OCR] EasyOCR 초기화 완료")
    return _reader


def ocr_image(image) -> str:
    """
    PIL Image 또는 numpy array → OCR 텍스트.
    OCR_ENABLED=False이거나 실패 시 빈 문자열 반환.
    """
    if not OCR_ENABLED:
        return ""
    try:
        from PIL import Image as PILImage
        if isinstance(image, PILImage.Image):
            image = np.array(image)
        reader  = _get_reader()
        results = reader.readtext(image, detail=0, paragraph=True)
        return "\n".join(results)
    except Exception as e:
        log.warning(f"[OCR] 이미지 OCR 실패: {e}")
        return ""


def ocr_pdf_page(page) -> str:
    """
    fitz(pymupdf) Page 객체 → OCR 텍스트.
    페이지를 150 DPI 이미지로 렌더링 후 OCR.
    """
    if not OCR_ENABLED:
        return ""
    try:
        pix = page.get_pixmap(dpi=150)
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
        if pix.n == 4:  # RGBA → RGB
            img = img[:, :, :3]
        return ocr_image(img)
    except Exception as e:
        log.warning(f"[OCR] PDF 페이지 OCR 실패: {e}")
        return ""
