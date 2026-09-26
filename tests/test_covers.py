import zipfile

from reshelf.covers import cover_path, ensure_cover, extract_cover, thumb_path
from tests.helpers import CONTAINER, make_epub, make_pdf

SHA = "a" * 64


def epub_with_cover(path):
    """Build a one-pass EPUB whose manifest already points at cover.jpg.

    Unlike appending to make_epub's output (which would write content.opf
    twice and trip zipfile's "Duplicate name" warning), every entry here is
    written exactly once.
    """
    import pymupdf as fitz

    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 40, 60))
    pix.clear_with(200)
    cover_bytes = pix.tobytes("jpeg")

    opf = """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>Dune</dc:title>
    <dc:creator>Frank Herbert</dc:creator>
  </metadata>
  <manifest>
    <item id="c1" href="chapter1.xhtml" media-type="application/xhtml+xml"/>
    <item id="cover" href="cover.jpg" media-type="image/jpeg" properties="cover-image"/>
  </manifest>
  <spine><itemref idref="c1"/></spine>
</package>"""
    chapter = (
        '<?xml version="1.0"?>'
        '<html xmlns="http://www.w3.org/1999/xhtml"><body><p>text</p></body></html>'
    )
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr("META-INF/container.xml", CONTAINER)
        z.writestr("content.opf", opf)
        z.writestr("chapter1.xhtml", chapter)
        z.writestr("cover.jpg", cover_bytes)
    return path


def test_pdf_cover_is_the_first_page(tmp_path):
    src = make_pdf(tmp_path / "b.pdf", "Dune", "Herbert", text="page one")
    dest = tmp_path / "out.jpg"
    assert extract_cover(src, dest) is True
    assert dest.exists() and dest.stat().st_size > 0


def test_epub_cover_comes_from_the_manifest(tmp_path):
    src = epub_with_cover(tmp_path / "b.epub")
    dest = tmp_path / "out.jpg"
    assert extract_cover(src, dest) is True
    assert dest.exists() and dest.stat().st_size > 0


def test_an_epub_with_no_cover_reports_failure_without_raising(tmp_path):
    src = make_epub(tmp_path / "b.epub", "Dune", "Herbert")
    assert extract_cover(src, tmp_path / "out.jpg") is False


def test_an_unsupported_format_reports_failure(tmp_path):
    src = tmp_path / "b.azw3"
    src.write_bytes(b"not really")
    assert extract_cover(src, tmp_path / "out.jpg") is False


def test_a_corrupt_file_reports_failure_without_raising(tmp_path):
    src = tmp_path / "b.pdf"
    src.write_bytes(b"%PDF-1.4 garbage")
    assert extract_cover(src, tmp_path / "out.jpg") is False


def test_cover_is_downscaled_to_max_edge(tmp_path):
    import pymupdf as fitz

    src = make_pdf(tmp_path / "b.pdf", "Dune", "Herbert")
    dest = tmp_path / "out.jpg"
    extract_cover(src, dest, max_edge=100)
    pix = fitz.Pixmap(str(dest))
    assert max(pix.width, pix.height) <= 100


def test_ensure_cover_writes_both_sizes_and_is_idempotent(tmp_path):
    covers = tmp_path / "covers"
    src = make_pdf(tmp_path / "b.pdf", "Dune", "Herbert")
    assert ensure_cover(covers, SHA, src) is True
    assert cover_path(covers, SHA).exists()
    assert thumb_path(covers, SHA).exists()
    mtime = cover_path(covers, SHA).stat().st_mtime_ns
    assert ensure_cover(covers, SHA, src) is True
    assert cover_path(covers, SHA).stat().st_mtime_ns == mtime  # not rewritten
