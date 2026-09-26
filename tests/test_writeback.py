import json
from pathlib import Path

import pytest

from reshelf.store.models import BookMetadata
from reshelf.writeback import UnsupportedWriteBack, embed_metadata
from tests.helpers import make_epub, make_pdf


def test_embed_into_epub(tmp_path):
    src = make_epub(tmp_path / "b.epub", "Old", "Old Author")
    embed_metadata(src, BookMetadata(title="New", authors=["New Author"]))
    from reshelf.extractors.epub import extract_epub

    meta = extract_epub(src)
    assert meta.title == "New"
    assert meta.authors == ["New Author"]


def test_embed_into_pdf(tmp_path):
    src = make_pdf(tmp_path / "b.pdf", "Old", "Old Author")
    embed_metadata(src, BookMetadata(title="New", authors=["New Author"]))
    import pymupdf as fitz

    with fitz.open(str(src)) as doc:
        assert doc.metadata["title"] == "New"
        assert doc.metadata["author"] == "New Author"


@pytest.mark.parametrize("suffix", [".mobi", ".azw3", ".djvu", ".txt"])
def test_unconverted_formats_report_convert_first(tmp_path, suffix):
    src = tmp_path / f"b{suffix}"
    src.write_bytes(b"x")
    with pytest.raises(UnsupportedWriteBack) as exc:
        embed_metadata(src, BookMetadata(title="New"))
    assert exc.value.reason == "convert_first"


def test_embedding_leaves_the_pdf_readable(tmp_path):
    src = make_pdf(tmp_path / "b.pdf", "Old", "Old Author", text="body text")
    embed_metadata(src, BookMetadata(title="New", authors=["A"]))
    import pymupdf as fitz

    with fitz.open(str(src)) as doc:
        assert "body text" in doc.load_page(0).get_text()


def test_rename_library_copy_moves_the_file_and_updates_the_sidecar(tmp_path):
    from reshelf.config import default_config
    from reshelf.db.database import Database
    from reshelf.store.models import Book, FileEntry
    from reshelf.store.sidecar import SidecarStore
    from reshelf.writeback import rename_library_copy

    cfg = default_config(tmp_path)
    library = tmp_path / "library" / "Old Author"
    library.mkdir(parents=True)
    old = library / "Old Title.epub"
    make_epub(old, "Old Title", "Old Author")

    db = Database(cfg.database.path)
    db.init_schema()
    db.conn.execute(
        "INSERT INTO files (path, sha256, format, status)"
        " VALUES (?, ?, 'epub', 'COMMITTED')",
        (str(old), "a" * 64),
    )
    db.conn.commit()

    store = SidecarStore(cfg)
    book = Book(sha256="a" * 64, files=[FileEntry(path=str(old), format="epub")])
    book.metadata.title = "New Title"
    book.metadata.authors = ["New Author"]
    store.save(book)

    new_path = rename_library_copy(cfg, db, store, "a" * 64)
    assert new_path is not None
    assert "New Title" in new_path
    assert Path(new_path).exists()
    assert not old.exists()
    assert any(f.path == new_path for f in store.load("a" * 64).files)
    db.close()


def test_rename_is_a_no_op_for_an_uncommitted_book(tmp_path):
    from reshelf.config import default_config
    from reshelf.db.database import Database
    from reshelf.store.models import Book, FileEntry
    from reshelf.store.sidecar import SidecarStore
    from reshelf.writeback import rename_library_copy

    cfg = default_config(tmp_path)
    cfg.library.incoming.mkdir(parents=True)
    src = cfg.library.incoming / "x.epub"
    make_epub(src, "T", "A")
    db = Database(cfg.database.path)
    db.init_schema()
    db.conn.execute(
        "INSERT INTO files (path, sha256, format, status)"
        " VALUES (?, ?, 'epub', 'MATCHED')",
        (str(src), "a" * 64),
    )
    db.conn.commit()
    store = SidecarStore(cfg)
    store.save(Book(sha256="a" * 64, files=[FileEntry(path=str(src), format="epub")]))

    assert rename_library_copy(cfg, db, store, "a" * 64) is None
    assert src.exists()
    db.close()


def test_rename_library_copy_journal_round_trips_through_rollback(tmp_path):
    """The correction this task exists for: rollback_journal must be able to
    read the journal rename_library_copy writes, not just that a file with
    the right-looking shape got written."""
    from reshelf.config import default_config
    from reshelf.db.database import Database
    from reshelf.planner.committer import rollback_journal
    from reshelf.store.models import Book, FileEntry
    from reshelf.store.sidecar import SidecarStore
    from reshelf.writeback import rename_library_copy

    cfg = default_config(tmp_path)
    library = tmp_path / "library" / "Old Author"
    library.mkdir(parents=True)
    old = library / "Old Title.epub"
    make_epub(old, "Old Title", "Old Author")

    db = Database(cfg.database.path)
    db.init_schema()
    db.conn.execute(
        "INSERT INTO files (path, sha256, format, status)"
        " VALUES (?, ?, 'epub', 'COMMITTED')",
        (str(old), "a" * 64),
    )
    db.conn.commit()

    store = SidecarStore(cfg)
    book = Book(sha256="a" * 64, files=[FileEntry(path=str(old), format="epub")])
    book.metadata.title = "New Title"
    book.metadata.authors = ["New Author"]
    store.save(book)

    new_path = rename_library_copy(cfg, db, store, "a" * 64)
    assert new_path is not None
    assert not old.exists()

    reports_dir = tmp_path / "reports"
    journal_files = sorted(reports_dir.glob("commit-*.json"))
    assert len(journal_files) == 1
    journal = json.loads(journal_files[0].read_text())
    # This is the shape rollback_journal actually reads.
    assert journal["actions"] == [
        {"action": "import", "src": str(old), "dest": new_path, "moved": True}
    ]

    result = rollback_journal(journal, db, tmp_path / "library")
    assert result == {"reverted": 1, "skipped": 0}
    assert old.exists()
    assert not Path(new_path).exists()
    db.close()
