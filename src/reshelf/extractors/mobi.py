"""Kindle-format (MOBI/AZW/AZW3) metadata via calibre's ebook-meta CLI."""

import re
import subprocess
from pathlib import Path

from reshelf.extractors.base import ExtractedMetadata, ExtractionError
from reshelf.metadata.isbn import find_isbns
from reshelf.metadata.normalization import clean_text, normalize_language


def parse_ebook_meta_output(text: str) -> ExtractedMetadata:
    fields: dict[str, str] = {}
    for line in text.splitlines():
        key, sep, value = line.partition(":")
        if sep:
            fields.setdefault(key.strip().lower(), value.strip())

    def get(key: str) -> str | None:
        value = clean_text(fields.get(key))
        return None if value in (None, "Unknown") else value

    authors_raw = get("author(s)") or ""
    authors_raw = re.sub(r"\[[^\]]*\]", "", authors_raw)  # drop sort names
    authors = [
        a.strip()
        for a in authors_raw.split("&")
        if a.strip() and a.strip().lower() != "unknown"
    ]
    languages = get("languages")
    return ExtractedMetadata(
        title=get("title"),
        authors=authors,
        isbns=find_isbns(get("identifiers")),
        language=normalize_language(languages.split(",")[0]) if languages else None,
        publisher=get("publisher"),
        date=get("published"),
    )


def extract_mobi(path: Path) -> ExtractedMetadata:
    try:
        proc = subprocess.run(
            ["ebook-meta", str(path)], capture_output=True, text=True, timeout=60
        )
    except FileNotFoundError as e:
        raise ExtractionError("ebook-meta not found (install calibre)") from e
    except subprocess.TimeoutExpired as e:
        raise ExtractionError("ebook-meta timed out") from e
    if proc.returncode != 0:
        raise ExtractionError(
            (proc.stderr or proc.stdout).strip().splitlines()[-1][:200]
            if (proc.stderr or proc.stdout).strip()
            else "ebook-meta failed"
        )
    return parse_ebook_meta_output(proc.stdout)
