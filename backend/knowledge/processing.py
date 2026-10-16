"""Extract bounded text and split it into searchable chunks."""

from io import BytesIO
from pathlib import Path

from pypdf import PdfReader


MAX_DOCUMENT_BYTES = 5_000_000
MAX_PAGES = 100
MAX_TEXT_CHARS = 300_000
MAX_CHUNKS = 500
CHUNK_CHARS = 1200
CHUNK_OVERLAP = 150
ALLOWED_SUFFIXES = {".pdf", ".txt", ".md", ".markdown"}


class DocumentError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def extract_pages(filename, raw):
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES or not raw or len(raw) > MAX_DOCUMENT_BYTES:
        raise DocumentError("invalid_document")
    if suffix == ".pdf":
        if not raw.startswith(b"%PDF-"):
            raise DocumentError("invalid_document")
        try:
            reader = PdfReader(BytesIO(raw), strict=False)
            if len(reader.pages) > MAX_PAGES or reader.is_encrypted:
                raise DocumentError("unsupported_pdf")
            pages = [(index + 1, page.extract_text() or "") for index, page in enumerate(reader.pages)]
        except DocumentError:
            raise
        except Exception as error:
            raise DocumentError("invalid_document") from error
    else:
        try:
            pages = [(None, raw.decode("utf-8-sig"))]
        except UnicodeDecodeError as error:
            raise DocumentError("invalid_encoding") from error
    if sum(len(text) for _, text in pages) > MAX_TEXT_CHARS:
        raise DocumentError("document_too_long")
    if not any(text.strip() for _, text in pages):
        raise DocumentError("no_extractable_text")
    return pages


def split_pages(pages):
    chunks = []
    for page, text in pages:
        text = " ".join(text.split())
        start = 0
        while start < len(text):
            end = min(start + CHUNK_CHARS, len(text))
            if end < len(text):
                boundary = text.rfind(" ", start + CHUNK_CHARS // 2, end)
                if boundary > start:
                    end = boundary
            part = text[start:end].strip()
            if part:
                chunks.append((page, part))
            if len(chunks) > MAX_CHUNKS:
                raise DocumentError("too_many_chunks")
            if end == len(text):
                break
            start = max(start + 1, end - CHUNK_OVERLAP)
    return chunks
