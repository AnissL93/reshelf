import pytest

from reshelf.extractors.base import ExtractionError
from reshelf.extractors.pdf import extract_pdf
from tests.helpers import make_pdf


def test_extracts_metadata_and_page_isbn(tmp_path):
    p = make_pdf(
        tmp_path / "t.pdf",
        title="The Three-Body Problem",
        author="Liu Cixin",
        text="ISBN 978-0-765-38203-0",
    )
    meta = extract_pdf(p)
    assert meta.title == "The Three-Body Problem"
    assert meta.authors == ["Liu Cixin"]
    assert meta.isbns == ["9780765382030"]


def test_empty_metadata(tmp_path):
    p = make_pdf(tmp_path / "t.pdf", title="", author="")
    meta = extract_pdf(p)
    assert meta.title is None and meta.authors == []


def test_broken_pdf_raises(tmp_path):
    p = tmp_path / "bad.pdf"
    p.write_bytes(b"not a pdf")
    with pytest.raises(ExtractionError):
        extract_pdf(p)


def test_password_protected_pdf_raises(tmp_path):
    import pymupdf

    doc = pymupdf.open()
    doc.new_page()
    p = tmp_path / "locked.pdf"
    doc.save(
        str(p),
        encryption=pymupdf.PDF_ENCRYPT_AES_256,
        user_pw="secret",
        owner_pw="secret",
    )
    doc.close()
    with pytest.raises(ExtractionError):
        extract_pdf(p)
