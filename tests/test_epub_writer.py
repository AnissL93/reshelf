import zipfile

import pytest

from reshelf.convert.epub_writer import (
    UnsupportedEpub,
    find_opf,
    rewrite_metadata,
    write_epub,
)
from reshelf.store.models import BookMetadata
from tests.helpers import make_epub

_CONTAINER = """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>"""

_SELF_CLOSING_OPF = """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>Old</dc:title>
    <dc:creator/>
    <dc:language/>
  </metadata>
  <manifest>
    <item id="c1" href="chapter1.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine><itemref idref="c1"/></spine>
</package>"""


def test_mimetype_is_first_and_stored_uncompressed(tmp_path):
    """The EPUB spec requires this exactly; readers reject it otherwise."""
    dest = write_epub(
        tmp_path / "out.epub",
        title="T",
        authors=["A"],
        chapters=[("c1.xhtml", "<p>hello</p>")],
    )
    with zipfile.ZipFile(dest) as z:
        first = z.infolist()[0]
        assert first.filename == "mimetype"
        assert first.compress_type == zipfile.ZIP_STORED
        assert z.read("mimetype") == b"application/epub+zip"


def test_written_epub_has_the_required_parts(tmp_path):
    dest = write_epub(
        tmp_path / "out.epub",
        title="Dune",
        authors=["Frank Herbert"],
        chapters=[("c1.xhtml", "<p>hello</p>")],
    )
    with zipfile.ZipFile(dest) as z:
        names = z.namelist()
        assert "META-INF/container.xml" in names
        assert "content.opf" in names
        assert "nav.xhtml" in names
        assert "c1.xhtml" in names
        opf = z.read("content.opf").decode()
        assert "<dc:title>Dune</dc:title>" in opf
        assert "Frank Herbert" in opf


def test_the_written_epub_round_trips_through_the_existing_extractor(tmp_path):
    from reshelf.extractors.epub import extract_epub

    dest = write_epub(
        tmp_path / "out.epub",
        title="Dune",
        authors=["Frank Herbert"],
        language="en",
        chapters=[("c1.xhtml", "<p>hello</p>")],
    )
    meta = extract_epub(dest)
    assert meta.title == "Dune"
    assert meta.authors == ["Frank Herbert"]


def test_titles_with_xml_special_characters_are_escaped(tmp_path):
    dest = write_epub(
        tmp_path / "out.epub",
        title="Tom & Jerry <the> \"book\"",
        authors=["A & B"],
        chapters=[("c1.xhtml", "<p>x</p>")],
    )
    from reshelf.extractors.epub import extract_epub

    assert extract_epub(dest).title == 'Tom & Jerry <the> "book"'


def test_resources_are_included(tmp_path):
    dest = write_epub(
        tmp_path / "out.epub",
        title="T",
        authors=["A"],
        chapters=[("c1.xhtml", '<img src="img/a.png"/>')],
        resources={"img/a.png": b"\x89PNG\r\n\x1a\n"},
    )
    with zipfile.ZipFile(dest) as z:
        assert z.read("img/a.png").startswith(b"\x89PNG")


def test_rewrite_metadata_replaces_title_and_author(tmp_path):
    src = make_epub(tmp_path / "b.epub", "Old Title", "Old Author")
    rewrite_metadata(src, BookMetadata(title="New Title", authors=["New Author"]))
    from reshelf.extractors.epub import extract_epub

    meta = extract_epub(src)
    assert meta.title == "New Title"
    assert meta.authors == ["New Author"]


def test_rewrite_metadata_preserves_the_content_files(tmp_path):
    src = make_epub(tmp_path / "b.epub", "Old", "Old")
    with zipfile.ZipFile(src) as z:
        before = z.read("chapter1.xhtml")
    rewrite_metadata(src, BookMetadata(title="New", authors=["New"]))
    with zipfile.ZipFile(src) as z:
        assert z.read("chapter1.xhtml") == before
        assert z.infolist()[0].filename == "mimetype"


def test_rewrite_metadata_on_a_non_epub_raises(tmp_path):
    bad = tmp_path / "b.epub"
    bad.write_bytes(b"not a zip")
    with pytest.raises(UnsupportedEpub):
        rewrite_metadata(bad, BookMetadata(title="x"))


def test_a_failed_rewrite_leaves_the_original_intact(tmp_path, monkeypatch):
    src = make_epub(tmp_path / "b.epub", "Old Title", "Old Author")
    monkeypatch.setattr("os.replace", lambda *a, **k: (_ for _ in ()).throw(OSError()))
    with pytest.raises(OSError):
        rewrite_metadata(src, BookMetadata(title="New"))
    from reshelf.extractors.epub import extract_epub

    assert extract_epub(src).title == "Old Title"


def test_rewrite_metadata_replaces_self_closing_dc_elements(tmp_path):
    """A self-closed <dc:language/> or <dc:creator/> must be updated in
    place, not left behind alongside a newly-inserted duplicate."""
    src = tmp_path / "b.epub"
    with zipfile.ZipFile(src, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr("META-INF/container.xml", _CONTAINER)
        z.writestr("content.opf", _SELF_CLOSING_OPF)
        z.writestr("chapter1.xhtml", "<html><body>text</body></html>")

    rewrite_metadata(
        src, BookMetadata(title="New", authors=["A1", "A2"], language="fr")
    )

    with zipfile.ZipFile(src) as z:
        opf = z.read("content.opf").decode()

    assert opf.count("<dc:language") == 1
    assert "<dc:language>fr</dc:language>" in opf
    assert opf.count("<dc:creator") == 2
    assert "<dc:creator/>" not in opf


def test_find_opf_skips_a_stray_opf_that_is_not_the_package_document(tmp_path):
    path = tmp_path / "b.epub"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr("stray_backup/old_metadata.opf", "<notpackage>junk</notpackage>")
        z.writestr("content.opf", _SELF_CLOSING_OPF)

    with zipfile.ZipFile(path) as z:
        assert find_opf(z) == "content.opf"


def test_find_opf_raises_when_no_document_is_a_real_package(tmp_path):
    path = tmp_path / "b.epub"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr("not_a_package.opf", "<notpackage>junk</notpackage>")

    with zipfile.ZipFile(path) as z:
        with pytest.raises(UnsupportedEpub):
            find_opf(z)
