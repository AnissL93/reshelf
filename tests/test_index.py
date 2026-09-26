import sqlite3

import pytest

from reshelf.config import default_config
from reshelf.db.migrations import migrate
from reshelf.store import index
from reshelf.store.models import Book, BookMetadata, FileEntry
from reshelf.store.sidecar import SidecarStore


@pytest.fixture
def conn(tmp_path):
    c = sqlite3.connect(tmp_path / "i.sqlite3")
    c.row_factory = sqlite3.Row
    c.executescript(
        "CREATE TABLE files (id INTEGER PRIMARY KEY, path TEXT, sha256 TEXT,"
        " format TEXT, status TEXT);"
    )
    migrate(c)
    return c


def book(sha, title="Dune", authors=("Frank Herbert",), fmt="epub", **kw):
    return Book(
        sha256=sha,
        files=[FileEntry(path=f"library/{title}.{fmt}", format=fmt)],
        metadata=BookMetadata(title=title, authors=list(authors), **kw),
    )


def add_file(conn, sha, status="MATCHED", fmt="epub", path=None):
    conn.execute(
        "INSERT INTO files (path, sha256, format, status) VALUES (?,?,?,?)",
        (path or f"library/{sha}.{fmt}", sha, fmt, status),
    )
    conn.commit()


def test_sync_writes_a_queryable_row(conn):
    index.sync(conn, book("a" * 64))
    add_file(conn, "a" * 64)
    rows, total = index.query(conn)
    assert total == 1
    assert rows[0]["title"] == "Dune"
    assert rows[0]["authors"] == "Frank Herbert"
    assert rows[0]["status"] == "MATCHED"


def test_sync_is_an_upsert_not_a_duplicate(conn):
    b = book("a" * 64)
    index.sync(conn, b)
    b.metadata.title = "Dune Messiah"
    index.sync(conn, b)
    rows, total = index.query(conn)
    assert total == 1
    assert rows[0]["title"] == "Dune Messiah"


def test_full_text_search_finds_by_title_and_author(conn):
    index.sync(conn, book("a" * 64, title="Dune", authors=["Frank Herbert"]))
    index.sync(conn, book("b" * 64, title="Neuromancer", authors=["William Gibson"]))
    assert index.query(conn, q="Dune")[1] == 1
    assert index.query(conn, q="Gibson")[1] == 1
    assert index.query(conn, q="Tolkien")[1] == 0


def test_search_reindexes_rather_than_accumulating_fts_rows(conn):
    b = book("a" * 64, title="Dune")
    index.sync(conn, b)
    b.metadata.title = "Neuromancer"
    index.sync(conn, b)
    assert index.query(conn, q="Dune")[1] == 0
    assert index.query(conn, q="Neuromancer")[1] == 1


def test_a_query_with_punctuation_does_not_explode(conn):
    """FTS5 MATCH treats bare punctuation as syntax; it must be escaped."""
    index.sync(conn, book("a" * 64, title="Dune"))
    assert index.query(conn, q='"')[1] == 0
    assert index.query(conn, q="AND OR NOT")[1] == 0


def test_filter_by_status(conn):
    index.sync(conn, book("a" * 64))
    index.sync(conn, book("b" * 64, title="Other"))
    add_file(conn, "a" * 64, status="MATCHED")
    add_file(conn, "b" * 64, status="UNRESOLVED")
    assert index.query(conn, status="UNRESOLVED")[1] == 1


def test_filter_by_format_and_tag(conn):
    index.sync(conn, book("a" * 64, fmt="pdf", tags=["sci-fi"]))
    index.sync(conn, book("b" * 64, title="Other", fmt="epub", tags=["history"]))
    assert index.query(conn, fmt="pdf")[1] == 1
    assert index.query(conn, tag="history")[1] == 1


def test_sort_whitelist_rejects_injection(conn):
    with pytest.raises(ValueError):
        index.query(conn, sort="title; DROP TABLE files")


def test_paging_reports_the_full_total(conn):
    for n in range(5):
        index.sync(conn, book(str(n) * 64, title=f"Book {n}"))
    rows, total = index.query(conn, limit=2, offset=0)
    assert total == 5
    assert len(rows) == 2


def test_duplicate_paths_collapse_to_one_row(conn):
    sha = "a" * 64
    index.sync(conn, book(sha))
    add_file(conn, sha, path="incoming/one.epub")
    add_file(conn, sha, path="incoming/two.epub", status="DUPLICATE")
    rows, total = index.query(conn)
    assert total == 1


def test_remove_clears_both_tables(conn):
    index.sync(conn, book("a" * 64))
    index.remove(conn, "a" * 64)
    assert index.query(conn)[1] == 0
    assert index.query(conn, q="Dune")[1] == 0


def test_rebuild_restores_everything_from_sidecars(tmp_path, conn):
    cfg = default_config(tmp_path)
    store = SidecarStore(cfg)
    for n, title in ((0, "Dune"), (1, "Neuromancer")):
        store.save(book(str(n) * 64, title=title))
    assert index.rebuild(conn, store) == 2
    assert index.query(conn)[1] == 2
    assert index.query(conn, q="Neuromancer")[1] == 1


def test_rebuild_discards_stale_rows(tmp_path, conn):
    index.sync(conn, book("z" * 64, title="Deleted Book"))
    store = SidecarStore(default_config(tmp_path))
    store.save(book("a" * 64))
    index.rebuild(conn, store)
    assert index.query(conn, q="Deleted")[1] == 0
