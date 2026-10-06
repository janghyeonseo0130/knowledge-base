"""
HWP / HWPX 파서

HWP  : OLE 컨테이너 기반 바이너리 포맷 (pyhwp → olefile 폴백)
HWPX : ZIP 기반 XML 포맷
"""
from pathlib import Path
import re, struct, zipfile, zlib
import xml.etree.ElementTree as ET
from .base import BaseParser, ParsedDocument


class HwpParser(BaseParser):
    def supports(self, file_path: Path) -> bool:
        return file_path.suffix.lower() == ".hwp"

    def parse(self, file_path: Path) -> ParsedDocument:
        try:
            return self._parse_pyhwp(file_path)
        except Exception:
            pass
        try:
            return self._parse_olefile(file_path)
        except Exception:
            return ParsedDocument(content="", metadata={}, file_path=str(file_path))

    def _parse_pyhwp(self, file_path: Path) -> ParsedDocument:
        import io
        from hwp5.hwp5txt import PlainTextTransform
        from hwp5.storage import Hwp5File
        hwp5 = Hwp5File(str(file_path))
        transform = PlainTextTransform()
        buf = io.StringIO()
        transform.transform_hwp5_to_plaintext(hwp5, buf)
        return ParsedDocument(content=buf.getvalue(), metadata={}, file_path=str(file_path))

    def _parse_olefile(self, file_path: Path) -> ParsedDocument:
        import olefile
        texts = []
        with olefile.OleFileIO(str(file_path)) as ole:
            for i in range(200):
                stream = f"BodyText/Section{i}"
                if not ole.exists(stream):
                    break
                raw = ole.openstream(stream).read()
                try:
                    data = zlib.decompress(raw, -15)
                    texts.append(self._extract_para_text(data))
                except Exception:
                    pass
        return ParsedDocument(content="\n".join(t for t in texts if t), metadata={}, file_path=str(file_path))

    def _extract_para_text(self, data: bytes) -> str:
        PARA_TEXT_TAG = 67
        result, pos = [], 0
        while pos + 4 <= len(data):
            header = struct.unpack_from("<I", data, pos)[0]
            tag_id = header & 0x3FF
            size   = (header >> 20) & 0xFFF
            if size == 0xFFF:
                if pos + 8 > len(data):
                    break
                size = struct.unpack_from("<I", data, pos + 4)[0]
                pos += 8
            else:
                pos += 4
            if tag_id == PARA_TEXT_TAG and pos + size <= len(data):
                raw = data[pos: pos + size]
                try:
                    text = raw.decode("utf-16-le", errors="ignore")
                    text = re.sub(r"[\x00-\x1f]", " ", text).strip()
                    if text:
                        result.append(text)
                except Exception:
                    pass
            pos += size
        return "\n".join(result)


class HwpxParser(BaseParser):
    def supports(self, file_path: Path) -> bool:
        return file_path.suffix.lower() == ".hwpx"

    def parse(self, file_path: Path) -> ParsedDocument:
        texts = []
        try:
            with zipfile.ZipFile(str(file_path), "r") as zf:
                section_files = sorted(
                    n for n in zf.namelist()
                    if re.search(r"section\d*\.xml", n, re.IGNORECASE)
                )
                for sf in section_files:
                    raw = zf.read(sf)
                    try:
                        root = ET.fromstring(raw)
                        for elem in root.iter():
                            local = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
                            if local in ("t", "run", "text", "hp:t") and elem.text:
                                t = elem.text.strip()
                                if t:
                                    texts.append(t)
                    except ET.ParseError:
                        pass
        except Exception:
            return ParsedDocument(content="", metadata={}, file_path=str(file_path))
        return ParsedDocument(content="\n".join(texts), metadata={}, file_path=str(file_path))
