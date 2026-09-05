import shutil
import subprocess

import pytest

from reshelf.extractors.base import ExtractionError
from reshelf.extractors.mobi import extract_mobi, parse_ebook_meta_output
from tests.helpers import make_epub

EBOOK_META_OUTPUT = """\
Title               : The Three-Body Problem
Author(s)           : Liu Cixin [Liu, Cixin] & Ken Liu
Publisher           : Tor Books
Languages           : eng
Identifiers         : isbn:9780765382030, mobi-asin:B00IWUI7XW
Published           : 2014-11-11T08:00:00+00:00
"""


def test_parse_ebook_meta_output():
    meta = parse_ebook_meta_output(EBOOK_META_OUTPUT)
    assert meta.title == "The Three-Body Problem"
    assert meta.authors == ["Liu Cixin", "Ken Liu"]
    assert meta.isbns == ["9780765382030"]
    assert meta.language == "en"
    assert meta.publisher == "Tor Books"


def test_parse_unknown_fields_are_none():
    meta = parse_ebook_meta_output("Title               : Unknown\nAuthor(s)           : Unknown\n")
    assert meta.title is None and meta.authors == []


@pytest.mark.skipif(
    shutil.which("ebook-meta") is None, reason="calibre not installed"
)
def test_broken_mobi_degrades_gracefully(tmp_path):
    # ebook-meta exits 0 on unreadable files, guessing a title from the
    # filename — extraction must not crash, and downstream matching treats
    # the guess like any other weak title.
    p = tmp_path / "bad.mobi"
    p.write_bytes(b"not a mobi")
    try:
        meta = extract_mobi(p)
    except ExtractionError:
        return  # also acceptable
    assert meta.authors == [] and meta.isbns == []


@pytest.mark.skipif(
    shutil.which("ebook-convert") is None, reason="calibre not installed"
)
def test_extract_real_mobi_roundtrip(tmp_path):
    epub = make_epub(
        tmp_path / "t.epub",
        title="The Three-Body Problem",
        author="Liu Cixin",
        isbn="9780765382030",
        language="en",
    )
    mobi = tmp_path / "t.mobi"
    subprocess.run(
        ["ebook-convert", str(epub), str(mobi)], capture_output=True, check=True
    )
    meta = extract_mobi(mobi)
    assert meta.title == "The Three-Body Problem"
    assert "Liu Cixin" in meta.authors
    assert meta.isbns == ["9780765382030"]
