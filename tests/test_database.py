import pytest

from book_organizer.db.database import Database, LockError


def test_init_schema_creates_tables(tmp_path):
    with Database(tmp_path / "db" / "books.sqlite3") as db:
        db.init_schema()
        names = {
            r["name"]
            for r in db.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert {
        "files", "works", "editions", "authors", "author_aliases",
        "work_authors", "identifiers", "metadata_sources", "matches",
        "scan_runs",
    } <= names


def test_lock_prevents_second_instance(tmp_path):
    path = tmp_path / "db" / "books.sqlite3"
    with Database(path):
        with pytest.raises(LockError):
            Database(path)
    # released on close
    Database(path).close()


def test_wal_mode(tmp_path):
    with Database(tmp_path / "db" / "books.sqlite3") as db:
        mode = db.conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode == "wal"
