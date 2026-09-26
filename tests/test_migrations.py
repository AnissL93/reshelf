import sqlite3

from reshelf.db.migrations import MIGRATIONS, migrate


def test_migrate_from_empty_reaches_the_latest_version(tmp_path):
    conn = sqlite3.connect(tmp_path / "t.sqlite3")
    assert migrate(conn) == len(MIGRATIONS)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == len(MIGRATIONS)


def test_migrate_is_idempotent(tmp_path):
    conn = sqlite3.connect(tmp_path / "t.sqlite3")
    migrate(conn)
    assert migrate(conn) == len(MIGRATIONS)


def test_migrate_creates_the_jobs_table(tmp_path):
    conn = sqlite3.connect(tmp_path / "t.sqlite3")
    migrate(conn)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(jobs)")}
    assert {"id", "command", "status", "progress", "log"} <= cols


def test_migrate_creates_the_fts_table(tmp_path):
    conn = sqlite3.connect(tmp_path / "t.sqlite3")
    migrate(conn)
    conn.execute(
        "INSERT INTO books_fts (sha256, title, authors) VALUES ('a', 'Dune', 'Herbert')"
    )
    rows = conn.execute(
        "SELECT sha256 FROM books_fts WHERE books_fts MATCH 'Dune'"
    ).fetchall()
    assert rows == [("a",)]


def test_an_existing_v0_database_is_upgraded_in_place(tmp_path):
    """The real database predates migrations; it must not be recreated."""
    path = tmp_path / "t.sqlite3"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE files (id INTEGER PRIMARY KEY, path TEXT)")
    conn.execute("INSERT INTO files (path) VALUES ('keep-me.epub')")
    conn.commit()
    migrate(conn)
    assert conn.execute("SELECT path FROM files").fetchone() == ("keep-me.epub",)
