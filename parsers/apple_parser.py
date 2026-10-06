"""Apple iWork 포맷 파서 (Pages / Numbers / Keynote) — ZIP 기반 XML"""
from pathlib import Path
import zipfile
import xml.etree.ElementTree as ET
from .base import BaseParser, ParsedDocument

_TEXT_TAGS = {"p", "t", "string", "text", "sf:p", "sf:t", "key:p", "key:t"}


class AppleParser(BaseParser):
    SUPPORTED = {".pages", ".numbers", ".key"}

    def supports(self, file_path: Path) -> bool:
        return file_path.suffix.lower() in self.SUPPORTED

    def parse(self, file_path: Path) -> ParsedDocument:
        texts = []
        try:
            with zipfile.ZipFile(str(file_path), "r") as zf:
                for xml_file in (n for n in zf.namelist() if n.endswith(".xml")):
                    try:
                        root = ET.fromstring(zf.read(xml_file))
                        for elem in root.iter():
                            local = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
                            if local in _TEXT_TAGS and elem.text and elem.text.strip():
                                texts.append(elem.text.strip())
                    except ET.ParseError:
                        pass
        except Exception:
            return ParsedDocument(content="", metadata={}, file_path=str(file_path))
        return ParsedDocument(content="\n".join(texts), metadata={}, file_path=str(file_path))
