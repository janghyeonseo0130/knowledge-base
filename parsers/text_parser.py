from pathlib import Path
from .base import BaseParser, ParsedDocument


class TextParser(BaseParser):
    SUPPORTED = {".txt", ".md", ".markdown", ".csv", ".html", ".htm", ".rtf"}

    def supports(self, file_path: Path) -> bool:
        return file_path.suffix.lower() in self.SUPPORTED

    def parse(self, file_path: Path) -> ParsedDocument:
        suffix = file_path.suffix.lower()
        if suffix in (".txt", ".md", ".markdown"):
            return self._plain(file_path)
        if suffix == ".csv":
            return self._csv(file_path)
        if suffix in (".html", ".htm"):
            return self._html(file_path)
        if suffix == ".rtf":
            return self._rtf(file_path)
        return self._plain(file_path)

    def _read(self, file_path: Path) -> str:
        for enc in ("utf-8", "utf-8-sig", "cp949", "euc-kr", "latin-1"):
            try:
                return file_path.read_text(encoding=enc)
            except (UnicodeDecodeError, LookupError):
                continue
        return file_path.read_text(encoding="utf-8", errors="replace")

    def _plain(self, file_path: Path) -> ParsedDocument:
        return ParsedDocument(content=self._read(file_path), metadata={}, file_path=str(file_path))

    def _csv(self, file_path: Path) -> ParsedDocument:
        import csv, io
        from .base import ParsedTable
        raw = self._read(file_path)
        all_rows = list(csv.reader(io.StringIO(raw)))
        if not all_rows:
            return ParsedDocument(content="", metadata={}, file_path=str(file_path))
        headers = [str(h).strip() for h in all_rows[0]]
        data_rows = [row for row in all_rows[1:] if any(v.strip() for v in row)]
        table = ParsedTable(sheet_name="Sheet1", headers=headers, rows=data_rows)
        return ParsedDocument(tables=[table], metadata={}, file_path=str(file_path))

    def _html(self, file_path: Path) -> ParsedDocument:
        from bs4 import BeautifulSoup
        raw = self._read(file_path)
        soup = BeautifulSoup(raw, "lxml")
        title = soup.title.string.strip() if soup.title and soup.title.string else ""
        text = soup.get_text(separator="\n", strip=True)
        return ParsedDocument(content=text, metadata={"title": title}, file_path=str(file_path))

    def _rtf(self, file_path: Path) -> ParsedDocument:
        from striprtf.striprtf import rtf_to_text
        raw = self._read(file_path)
        return ParsedDocument(content=rtf_to_text(raw), metadata={}, file_path=str(file_path))
