from pathlib import Path

import pymupdf as fitz

from book_organizer.extractors.base import ExtractedMetadata, ExtractionError
from book_organizer.metadata.isbn import find_isbns
from book_organizer.metadata.normalization import clean_text

_ISBN_SCAN_PAGES = 5


def extract_pdf(path: Path) -> ExtractedMetadata:
    try:
        doc = fitz.open(path)
    except Exception as e:  # fitz raises several undocumented types
        raise ExtractionError(str(e)) from e
    try:
        if doc.is_encrypted and not doc.authenticate(""):
            raise ExtractionError("password-protected PDF")
        meta = doc.metadata or {}
        text_parts = [
            meta.get("title") or "",
            meta.get("subject") or "",
            meta.get("keywords") or "",
        ]
        for page in doc.pages(0, min(doc.page_count, _ISBN_SCAN_PAGES)):
            text_parts.append(page.get_text())
        author = clean_text(meta.get("author"))
        return ExtractedMetadata(
            title=clean_text(meta.get("title")),
            authors=[author] if author else [],
            isbns=find_isbns(" ".join(text_parts)),
            date=clean_text(meta.get("creationDate")),
        )
    except ExtractionError:
        raise
    except Exception as e:  # corrupt page trees, bad xrefs, ...
        raise ExtractionError(str(e)) from e
    finally:
        doc.close()
