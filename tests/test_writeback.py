import hashlib
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from reshelf.cli import app
from reshelf.config import load_config
from reshelf.db.database import Database
from reshelf.planner.committer import rollback_journal
from reshelf.store.models import BookMetadata
from reshelf.store.sidecar import SidecarStore
from reshelf.writeback import UnsupportedWriteBack, embed_metadata, rename_library_copy
from tests.helpers import make_epub, make_pdf
from tests.test_committer import _setup_committed_root

runner = CliRunner()


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


def test_embed_into_pdf_falls_back_to_full_save_when_incremental_is_unavailable(
    tmp_path, monkeypatch
):
    """The temp-file + os.replace branch - never reached by
    tests/helpers.make_pdf output, since pymupdf can always save that
    incrementally - is exercised here by forcing can_save_incrementally()
    to report False."""
    import pymupdf as fitz

    src = make_pdf(tmp_path / "b.pdf", "Old", "Old Author", text="body text")
    monkeypatch.setattr(fitz.Document, "can_save_incrementally", lambda self: False)

    embed_metadata(src, BookMetadata(title="New", authors=["New Author"]))

    with fitz.open(str(src)) as doc:
        assert doc.metadata["title"] == "New"
        assert doc.metadata["author"] == "New Author"
        assert "body text" in doc.load_page(0).get_text()


def _commit_one_book(tmp_path):
    """Drive the real init -> plan -> commit CLI path, exactly as production
    leaves it: DB files.path still pointing at incoming/ (copy mode never
    repoints it - see committer.apply_plan's import branch), and the
    sidecar holding both the incoming and library FileEntry rows (written
    by pipeline._record_committed_path). Returns (cfg, sha256, src, library_path).
    """
    root, src = _setup_committed_root(tmp_path)
    r = runner.invoke(app, ["commit", "--root", str(root)])
    assert r.exit_code == 0, r.output
    cfg = load_config(root)
    sha256 = hashlib.sha256(src.read_bytes()).hexdigest()
    library_path = (
        root
        / "library"
        / "Liu Cixin"
        / "The Three-Body Problem (2014)"
        / "The Three-Body Problem.epub"
    )
    assert library_path.exists()
    return cfg, sha256, src, library_path


def test_rename_library_copy_moves_the_file_and_updates_the_sidecar(tmp_path):
    cfg, sha256, src, library_path = _commit_one_book(tmp_path)
    store = SidecarStore(cfg)

    def correct(b):
        b.metadata.title = "New Title"
        b.metadata.authors = ["New Author"]

    store.update(sha256, correct, str(src))

    db = Database(cfg.database.path)
    new_path = rename_library_copy(cfg, db, store, sha256)

    assert new_path is not None
    assert "New Title" in new_path
    assert Path(new_path).exists()
    assert not library_path.exists()  # the library copy is what moved
    assert src.exists()  # the incoming original must never be touched

    book = store.load(sha256, str(src))
    assert any(f.path == new_path for f in book.files)
    assert any(f.path == str(src) for f in book.files)  # incoming entry untouched
    db.close()


def test_rename_is_a_no_op_for_an_uncommitted_book(tmp_path):
    from reshelf.config import default_config
    from reshelf.store.models import Book, FileEntry

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
    read the journal rename_library_copy writes, and it must describe the
    library copy - never the incoming original, which must survive
    untouched through the whole rename + rollback cycle."""
    cfg, sha256, src, library_path = _commit_one_book(tmp_path)
    store = SidecarStore(cfg)

    def correct(b):
        b.metadata.title = "New Title"

    store.update(sha256, correct, str(src))

    reports_dir = Path(cfg.library.root) / "reports"
    before = set(reports_dir.glob("commit-*.json"))

    db = Database(cfg.database.path)
    new_path = rename_library_copy(cfg, db, store, sha256)
    assert new_path is not None
    assert not library_path.exists()
    assert src.exists()

    after = set(reports_dir.glob("commit-*.json"))
    new_journal_files = after - before
    assert len(new_journal_files) == 1
    journal = json.loads(new_journal_files.pop().read_text())
    # This is the shape rollback_journal actually reads, describing the
    # library copy's move - not the incoming file, which never moved.
    assert journal["actions"] == [
        {
            "action": "import",
            "src": str(library_path),
            "dest": new_path,
            "moved": True,
        }
    ]

    result = rollback_journal(journal, db, Path(cfg.library.root) / "library")
    assert result == {"reverted": 1, "skipped": 0}
    assert library_path.exists()
    assert not Path(new_path).exists()
    assert src.exists()  # untouched throughout
    db.close()
