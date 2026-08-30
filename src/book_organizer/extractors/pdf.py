from pathlib import Path

import fitz

from book_organizer.extractors.base import ExtractedMetadata, ExtractionError
from book_organizer.metadata.isbn import find_isbns

_ISBN_SCAN_PAGES = 5


def extract_pdf(path: Path) -> ExtractedMetadata:
    try:
        doc = fitz.open(path)
    except Exception as e:  # fitz raises several undocumented types
        raise ExtractionError(str(e)) from e
    try:
        meta = doc.metadata or {}
        text_parts = [
            meta.get("title") or "",
            meta.get("subject") or "",
            meta.get("keywords") or "",
        ]
        for page in doc.pages(0, min(doc.page_count, _ISBN_SCAN_PAGES)):
            text_parts.append(page.get_text())
        author = (meta.get("author") or "").strip()
        return ExtractedMetadata(
            title=(meta.get("title") or "").strip() or None,
            authors=[author] if author else [],
            isbns=find_isbns(" ".join(text_parts)),
            date=(meta.get("creationDate") or "").strip() or None,
        )
    finally:
        doc.close()
