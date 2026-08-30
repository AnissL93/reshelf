import pytest

from book_organizer.extractors.base import ExtractionError
from book_organizer.extractors.epub import extract_epub
from tests.helpers import make_epub


def test_extracts_full_metadata(tmp_path):
    p = make_epub(
        tmp_path / "t.epub",
        title="The Three-Body Problem",
        author="Liu Cixin",
        isbn="9780765382030",
        language="eng",
        publisher="Tor Books",
    )
    meta = extract_epub(p)
    assert meta.title == "The Three-Body Problem"
    assert meta.authors == ["Liu Cixin"]
    assert meta.isbns == ["9780765382030"]
    assert meta.language == "en"
    assert meta.publisher == "Tor Books"


def test_missing_optional_fields(tmp_path):
    p = make_epub(tmp_path / "t.epub", title="三体", author="刘慈欣", language="zh")
    meta = extract_epub(p)
    assert meta.title == "三体" and meta.isbns == []


def test_broken_zip_raises(tmp_path):
    p = tmp_path / "bad.epub"
    p.write_bytes(b"not a zip")
    with pytest.raises(ExtractionError):
        extract_epub(p)
