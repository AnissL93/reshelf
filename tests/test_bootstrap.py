from pathlib import Path

from reshelf.config import default_config
from reshelf.db.database import Database
from reshelf.db.migrations import migrate
from reshelf.store import index
from reshelf.store.bootstrap import migrate_json, reindex
from reshelf.store.models import Book
from reshelf.store.sidecar import SidecarStore

NOOP = lambda *a: None  # noqa: E731


def seeded(tmp_path):
    cfg = default_config(tmp_path)
    db = Database(cfg.database.path)
    db.init_schema()
    db.conn.execute(
        "INSERT INTO files (path, sha256, format, size, mtime, status,"
        " title_raw, author_raw, isbn_raw, language_raw)"
        " VALUES ('incoming/dune.epub', ?, 'epub', 10, 1, 'IDENTIFIED',"
        " 'Dune', 'Frank Herbert', '9780441013593', 'en')",
        ("a" * 64,),
    )
    db.conn.execute(
        "INSERT INTO files (path, sha256, format, size, mtime, status)"
        " VALUES ('incoming/unknown.pdf', ?, 'pdf', 20, 2, 'SCANNED')",
        ("b" * 64,),
    )
    db.conn.commit()
    return cfg, db, SidecarStore(cfg)


def test_migrate_json_writes_a_sidecar_for_every_hashed_file(tmp_path):
    cfg, db, store = seeded(tmp_path)
    assert migrate_json(db, store, NOOP) == 2
    dune = store.load("a" * 64)
    assert dune.metadata.title == "Dune"
    assert dune.metadata.authors == ["Frank Herbert"]
    assert dune.metadata.isbn13 == "9780441013593"
    assert dune.source.resolver == "embedded"
    assert dune.files[0].path == "incoming/dune.epub"
    db.close()


def test_a_file_with_no_metadata_still_gets_a_sidecar(tmp_path):
    """Uniformity: every hashed file has a sidecar, so the UI never has a gap."""
    cfg, db, store = seeded(tmp_path)
    migrate_json(db, store, NOOP)
    unknown = store.load("b" * 64)
    assert unknown is not None
    assert unknown.metadata.title is None
    db.close()


def test_migrate_json_never_overwrites_a_human_decision(tmp_path):
    cfg, db, store = seeded(tmp_path)
    migrate_json(db, store, NOOP)
    store.update("a" * 64, lambda b: (
        setattr(b.metadata, "title", "My Correction"),
        setattr(b.source, "resolver", "human"),
    ))
    migrate_json(db, store, NOOP)
    assert store.load("a" * 64).metadata.title == "My Correction"
    db.close()


def test_migrate_json_is_idempotent(tmp_path):
    cfg, db, store = seeded(tmp_path)
    migrate_json(db, store, NOOP)
    migrate_json(db, store, NOOP)
    assert len(list(store.iter_all())) == 2
    db.close()


def test_unhashed_files_are_skipped(tmp_path):
    cfg, db, store = seeded(tmp_path)
    db.conn.execute(
        "INSERT INTO files (path, format, size, mtime, status)"
        " VALUES ('incoming/pending.epub', 'epub', 1, 1, 'NEW')"
    )
    db.conn.commit()
    assert migrate_json(db, store, NOOP) == 2
    db.close()


def test_reindex_restores_the_index_after_the_tables_are_wiped(tmp_path):
    cfg, db, store = seeded(tmp_path)
    migrate_json(db, store, NOOP)
    reindex(db, store, NOOP)
    assert index.query(db.conn, q="Dune")[1] == 1

    db.conn.execute("DELETE FROM book_index")
    db.conn.execute("DELETE FROM books_fts")
    db.conn.commit()
    assert index.query(db.conn, q="Dune")[1] == 0

    assert reindex(db, store, NOOP) == 2
    assert index.query(db.conn, q="Dune")[1] == 1
    db.close()


def test_rebuild_produces_correct_results_from_batched_syncs(tmp_path):
    """Verify rebuild() correctly indexes books even with batched commits."""
    cfg = default_config(tmp_path)
    store = SidecarStore(cfg)
    for n in range(3):
        store.save(Book(sha256=str(n) * 64))

    import sqlite3
    c = sqlite3.connect(tmp_path / "i.sqlite3", isolation_level=None)
    c.row_factory = sqlite3.Row
    c.executescript(
        "CREATE TABLE files (id INTEGER PRIMARY KEY, path TEXT, sha256 TEXT,"
        " format TEXT, status TEXT);"
    )
    migrate(c)

    # rebuild should produce correct results and be queryable after batched commits
    count = index.rebuild(c, store)
    assert count == 3
    assert index.query(c)[1] == 3
    c.close()


def test_migrate_json_preserves_all_co_authors_from_matched_metadata(tmp_path):
    """Matched books with multiple authors retain all of them, not just the first."""
    cfg, db, store = seeded(tmp_path)

    # Add a work with two authors
    db.conn.execute("INSERT INTO works (id, canonical_title) VALUES (1, 'Collaborative Work')")
    db.conn.execute("INSERT INTO authors (id, canonical_name) VALUES (10, 'Author One')")
    db.conn.execute("INSERT INTO authors (id, canonical_name) VALUES (11, 'Author Two')")
    db.conn.execute("INSERT INTO work_authors (work_id, author_id) VALUES (1, 10)")
    db.conn.execute("INSERT INTO work_authors (work_id, author_id) VALUES (1, 11)")

    # Add edition for this work
    db.conn.execute(
        "INSERT INTO editions (id, work_id, isbn13) VALUES (1, 1, '9780000000001')"
    )

    # Update the dune file to reference this edition
    db.conn.execute(
        "UPDATE files SET matched_edition_id = 1 WHERE sha256 = ?",
        ("a" * 64,),
    )
    db.conn.commit()

    # Migrate and check both authors are preserved
    migrate_json(db, store, NOOP)
    book = store.load("a" * 64)
    assert book.metadata.authors == ["Author One", "Author Two"]
    db.close()
