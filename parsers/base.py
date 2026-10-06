from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ParsedTable:
    sheet_name: str
    headers: list[str]
    rows: list[list[Any]]


@dataclass
class ParsedDocument:
    content: str = ""
    metadata: dict = field(default_factory=dict)
    file_path: str = ""
    tables: list[ParsedTable] = field(default_factory=list)


class BaseParser(ABC):
    @abstractmethod
    def supports(self, file_path: Path) -> bool:
        """이 파서가 해당 파일을 처리할 수 있는지 반환"""

    @abstractmethod
    def parse(self, file_path: Path) -> ParsedDocument:
        """파일을 파싱해 ParsedDocument 반환"""
