from pathlib import Path

from .pdf_parser    import PDFParser
from .office_parser import DocxParser, PptxParser, XlsxParser
from .hwp_parser    import HwpParser, HwpxParser
from .text_parser   import TextParser
from .apple_parser  import AppleParser
from .base          import ParsedDocument, ParsedTable  # re-export

_PARSERS = [
    PDFParser(),
    DocxParser(),
    PptxParser(),
    XlsxParser(),
    HwpParser(),
    HwpxParser(),
    TextParser(),
    AppleParser(),
]


def get_parser(file_path: Path):
    """파일 확장자에 맞는 파서 반환. 없으면 None."""
    for parser in _PARSERS:
        if parser.supports(file_path):
            return parser
    return None
