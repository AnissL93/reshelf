import shutil
import tempfile
import zipfile
from pathlib import Path

import pytest

from reshelf.convert.converters import (
    ConversionError,
    available,
    convert,
    target_format,
)

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


def test_target_formats():
    assert target_format("azw3") == "epub"
    assert target_format("mobi") == "epub"
    assert target_format("txt") == "epub"
    assert target_format("djvu") == "pdf"
    assert target_format("epub") is None
    assert target_format("pdf") is None


def test_availability_reports_the_missing_ddjvu_binary():
    caps = available()
    assert caps["txt"] is True
    assert caps["djvu"] is (shutil.which("ddjvu") is not None)


def test_txt_becomes_a_readable_epub(tmp_path):
    src = tmp_path / "book.txt"
    src.write_text("Chapter one.\n\nChapter two.\n", encoding="utf-8")
    dest = convert(src, tmp_path / "out.epub")
    assert dest.exists()
    with zipfile.ZipFile(dest) as z:
        assert z.infolist()[0].filename == "mimetype"
    from reshelf.extractors.epub import extract_epub

    assert extract_epub(dest).title == "book"


def test_txt_in_gbk_is_decoded_not_mangled(tmp_path):
    """A Chinese library is full of GBK text files."""
    src = tmp_path / "zh.txt"
    src.write_bytes("第一章 起点\n\n正文内容\n".encode("gbk"))
    dest = convert(src, tmp_path / "out.epub")
    with zipfile.ZipFile(dest) as z:
        body = "".join(
            z.read(n).decode("utf-8") for n in z.namelist() if n.endswith(".xhtml")
        )
    assert "第一章 起点" in body


def test_txt_content_is_html_escaped(tmp_path):
    src = tmp_path / "book.txt"
    src.write_text("a < b & c > d", encoding="utf-8")
    dest = convert(src, tmp_path / "out.epub")
    with zipfile.ZipFile(dest) as z:
        body = "".join(
            z.read(n).decode("utf-8") for n in z.namelist() if n.endswith(".xhtml")
        )
    assert "&lt;" in body and "&amp;" in body


def test_an_unconvertible_format_raises(tmp_path):
    src = tmp_path / "book.epub"
    src.write_bytes(b"x")
    with pytest.raises(ConversionError, match="nothing to convert"):
        convert(src, tmp_path / "out.epub")


def test_a_corrupt_mobi_raises_conversion_error_not_a_crash(tmp_path):
    src = tmp_path / "book.mobi"
    src.write_bytes(b"definitely not a mobi")
    with pytest.raises(ConversionError):
        convert(src, tmp_path / "out.epub")


def test_a_failed_mobi_unpack_does_not_leak_its_temp_dir(tmp_path):
    tmp_root = Path(tempfile.gettempdir())
    before = set(tmp_root.glob("mobiex*"))
    src = tmp_path / "book.mobi"
    src.write_bytes(b"definitely not a mobi")
    with pytest.raises(ConversionError):
        convert(src, tmp_path / "out.epub")
    leaked = set(tmp_root.glob("mobiex*")) - before
    assert not leaked, f"leaked temp dirs: {leaked}"


@pytest.mark.skipif(shutil.which("ddjvu") is None, reason="djvulibre not installed")
def test_djvu_becomes_a_pdf(tmp_path):
    pytest.skip("needs a sample .djvu fixture; see Step 6")


def test_djvu_without_ddjvu_raises_a_clear_error(tmp_path, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: None)
    src = tmp_path / "book.djvu"
    src.write_bytes(b"AT&TFORM")
    with pytest.raises(ConversionError, match="ddjvu"):
        convert(src, tmp_path / "out.pdf")
