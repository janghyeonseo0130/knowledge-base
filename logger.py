"""
중앙 로거 설정 — 인덱싱 관련 이벤트를 data/index.log에 30일치 보관
"""
from __future__ import annotations
import logging
from logging.handlers import TimedRotatingFileHandler

from config import DATA_DIR

_FILE_HANDLER: TimedRotatingFileHandler | None = None


def _get_file_handler() -> TimedRotatingFileHandler:
    global _FILE_HANDLER
    if _FILE_HANDLER is None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        _FILE_HANDLER = TimedRotatingFileHandler(
            DATA_DIR / "index.log",
            when="midnight",
            backupCount=30,
            encoding="utf-8",
        )
        _FILE_HANDLER.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))
    return _FILE_HANDLER


def get_logger(name: str) -> logging.Logger:
    """파일 핸들러가 붙은 로거 반환. 같은 name으로 여러 번 호출해도 핸들러 중복 없음."""
    logger = logging.getLogger(name)
    if not any(isinstance(h, TimedRotatingFileHandler) for h in logger.handlers):
        logger.addHandler(_get_file_handler())
    if not logger.level:
        logger.setLevel(logging.INFO)
    return logger
