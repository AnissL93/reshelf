"""Schema evolution keyed on PRAGMA user_version.

Append new scripts to MIGRATIONS; never edit an existing entry, because
databases in the wild have already run it. Version N means MIGRATIONS[:N]
have been applied.
"""

import sqlite3

# Version 1: the web layer's derived tables. The pre-existing pipeline
# tables live in database.SCHEMA and are created with IF NOT EXISTS, so a
# database from before migrations existed is at version 0 and simply gains
# these.
_V1 = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY,
    command TEXT NOT NULL,
    args_json TEXT,
    status TEXT NOT NULL,
    progress INTEGER DEFAULT 0,
    total INTEGER,
    message TEXT,
    log TEXT DEFAULT '',
    error TEXT,
    created_at TEXT,
    started_at TEXT,
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);

CREATE VIRTUAL TABLE IF NOT EXISTS books_fts USING fts5(
    sha256 UNINDEXED, title, authors, series, publisher, tags, description
);

CREATE TABLE IF NOT EXISTS book_index (
    sha256 TEXT PRIMARY KEY,
    title TEXT,
    authors TEXT,
    series TEXT,
    series_index REAL,
    publisher TEXT,
    pubdate TEXT,
    language TEXT,
    isbn13 TEXT,
    tags TEXT,
    resolver TEXT,
    confidence REAL,
    primary_path TEXT,
    primary_format TEXT,
    updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_book_index_resolver ON book_index(resolver);
"""

MIGRATIONS: list[str] = [_V1]


def migrate(conn: sqlite3.Connection) -> int:
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    for version in range(current, len(MIGRATIONS)):
        conn.executescript(MIGRATIONS[version])
        # PRAGMA user_version cannot take a bound parameter, hence the f-string.
        # The value is a loop index, never user input.
        conn.execute(f"PRAGMA user_version = {version + 1}")
    conn.commit()
    return conn.execute("PRAGMA user_version").fetchone()[0]
