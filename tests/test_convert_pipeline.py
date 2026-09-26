import pytest

from reshelf.config import default_config
from reshelf.db.database import Database
from reshelf.pipeline import convert_book
from reshelf.store.models import Book, FileEntry
from reshelf.store.sidecar import SidecarStore

NOOP = lambda *a: None  # noqa: E731
SHA = "a" * 64


@pytest.fixture
def env(tmp_path):
    cfg = default_config(tmp_path)
    cfg.library.incoming.mkdir(parents=True)
    src = cfg.library.incoming / "book.txt"
    src.write_text("hello\n\nworld\n", encoding="utf-8")

    db = Database(cfg.database.path)
    db.init_schema()
    db.conn.execute(
        "INSERT INTO files (path, sha256, format, status) VALUES (?,?, 'txt','IDENTIFIED')",
        (str(src), SHA),
    )
    db.conn.commit()

    store = SidecarStore(cfg)
    store.save(Book(sha256=SHA, files=[FileEntry(path=str(src), format="txt")]))
    yield cfg, db, store
    db.close()


def test_convert_book_appends_a_derived_entry_and_keeps_the_key(env):
    cfg, db, store = env
    dest = convert_book(cfg, db, store, SHA, NOOP)
    book = store.load(SHA)
    assert book.sha256 == SHA  # the book is still keyed by the original
    derived = [f for f in book.files if f.role == "converted"]
    assert len(derived) == 1
    assert derived[0].path == dest
    assert derived[0].format == "epub"
    assert derived[0].sha256 and derived[0].sha256 != SHA


def test_the_original_file_survives(env):
    cfg, db, store = env
    original = store.load(SHA).files[0].path
    convert_book(cfg, db, store, SHA, NOOP)
    from pathlib import Path

    assert Path(original).exists()


def test_reconverting_replaces_rather_than_duplicates(env):
    cfg, db, store = env
    convert_book(cfg, db, store, SHA, NOOP)
    convert_book(cfg, db, store, SHA, NOOP)
    derived = [f for f in store.load(SHA).files if f.role == "converted"]
    assert len(derived) == 1


def test_the_derived_file_becomes_the_primary(env):
    cfg, db, store = env
    convert_book(cfg, db, store, SHA, NOOP)
    assert store.load(SHA).primary_file().format == "epub"


def test_converting_an_already_epub_book_raises(env):
    cfg, db, store = env
    db.conn.execute("UPDATE files SET format='epub' WHERE sha256=?", (SHA,))
    db.conn.commit()
    store.update(SHA, lambda b: setattr(b.files[0], "format", "epub"))
    from reshelf.convert.converters import ConversionError

    with pytest.raises(ConversionError):
        convert_book(cfg, db, store, SHA, NOOP)
