import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY,
    path TEXT UNIQUE NOT NULL,
    sha256 TEXT,
    format TEXT,
    size INTEGER,
    mtime INTEGER,
    status TEXT,
    title_raw TEXT,
    author_raw TEXT,
    isbn_raw TEXT,
    language_raw TEXT,
    matched_edition_id INTEGER,
    match_confidence REAL,
    created_at TEXT,
    updated_at TEXT,
    FOREIGN KEY(matched_edition_id) REFERENCES editions(id)
);
CREATE INDEX IF NOT EXISTS idx_files_sha256 ON files(sha256);

CREATE TABLE IF NOT EXISTS works (
    id INTEGER PRIMARY KEY,
    canonical_title TEXT,
    original_title TEXT,
    original_language TEXT,
    description TEXT
);

CREATE TABLE IF NOT EXISTS editions (
    id INTEGER PRIMARY KEY,
    work_id INTEGER,
    isbn10 TEXT,
    isbn13 TEXT,
    publisher TEXT,
    publication_date TEXT,
    language TEXT,
    edition_name TEXT,
    FOREIGN KEY(work_id) REFERENCES works(id)
);
CREATE INDEX IF NOT EXISTS idx_editions_isbn13 ON editions(isbn13);

CREATE TABLE IF NOT EXISTS authors (
    id INTEGER PRIMARY KEY,
    canonical_name TEXT
);

CREATE TABLE IF NOT EXISTS author_aliases (
    id INTEGER PRIMARY KEY,
    author_id INTEGER,
    alias TEXT,
    FOREIGN KEY(author_id) REFERENCES authors(id)
);

CREATE TABLE IF NOT EXISTS work_authors (
    work_id INTEGER,
    author_id INTEGER,
    PRIMARY KEY(work_id, author_id)
);

CREATE TABLE IF NOT EXISTS identifiers (
    id INTEGER PRIMARY KEY,
    edition_id INTEGER,
    type TEXT,
    value TEXT,
    source TEXT,
    UNIQUE(type, value),
    FOREIGN KEY(edition_id) REFERENCES editions(id)
);

CREATE TABLE IF NOT EXISTS metadata_sources (
    id INTEGER PRIMARY KEY,
    edition_id INTEGER,
    provider TEXT,
    provider_id TEXT,
    retrieved_at TEXT,
    raw_json TEXT,
    UNIQUE(provider, provider_id),
    FOREIGN KEY(edition_id) REFERENCES editions(id)
);

CREATE TABLE IF NOT EXISTS matches (
    id INTEGER PRIMARY KEY,
    file_id INTEGER,
    edition_id INTEGER,
    score REAL,
    confidence REAL,
    resolver TEXT,
    evidence_json TEXT,
    status TEXT,
    created_at TEXT,
    FOREIGN KEY(file_id) REFERENCES files(id),
    FOREIGN KEY(edition_id) REFERENCES editions(id)
);
CREATE INDEX IF NOT EXISTS idx_matches_file ON matches(file_id);

CREATE TABLE IF NOT EXISTS scan_runs (
    id INTEGER PRIMARY KEY,
    started_at TEXT,
    completed_at TEXT,
    root_path TEXT,
    files_seen INTEGER,
    files_added INTEGER,
    files_changed INTEGER
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class LockError(RuntimeError):
    pass


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock_path = self.path.parent / ".lock"
        try:
            self._lock_fd = os.open(
                self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY
            )
            os.write(self._lock_fd, str(os.getpid()).encode())
        except FileExistsError:
            raise LockError(
                f"another book-organizer instance holds {self.lock_path} "
                "(delete it if that process crashed)"
            ) from None
        self.conn = sqlite3.connect(self.path, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")

    def init_schema(self) -> None:
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()
        os.close(self._lock_fd)
        self.lock_path.unlink(missing_ok=True)

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def upsert_file(self, path: str, size: int, mtime: int, fmt: str) -> tuple[int, str]:
        row = self.conn.execute(
            "SELECT id, size, mtime FROM files WHERE path=?", (path,)
        ).fetchone()
        now = _now()
        if row is None:
            cur = self.conn.execute(
                "INSERT INTO files (path, size, mtime, format, status, created_at, updated_at)"
                " VALUES (?,?,?,?,'NEW',?,?)",
                (path, size, mtime, fmt, now, now),
            )
            return cur.lastrowid, "new"
        if (row["size"], row["mtime"]) == (size, mtime):
            return row["id"], "unchanged"
        self.conn.execute(
            "UPDATE files SET size=?, mtime=?, sha256=NULL, status='NEW', updated_at=?"
            " WHERE id=?",
            (size, mtime, now, row["id"]),
        )
        return row["id"], "changed"

    def set_hash(self, file_id: int, sha256: str) -> bool:
        dup = self.conn.execute(
            "SELECT id FROM files WHERE sha256=? AND id != ?", (sha256, file_id)
        ).fetchone()
        status = "DUPLICATE" if dup else "SCANNED"
        self.conn.execute(
            "UPDATE files SET sha256=?, status=?, updated_at=? WHERE id=?",
            (sha256, status, _now(), file_id),
        )
        return dup is not None

    def set_status(self, file_id: int, status: str) -> None:
        self.conn.execute(
            "UPDATE files SET status=?, updated_at=? WHERE id=?",
            (status, _now(), file_id),
        )

    def set_raw_metadata(
        self,
        file_id: int,
        title: str | None,
        author: str | None,
        isbn: str | None,
        language: str | None,
    ) -> None:
        self.conn.execute(
            "UPDATE files SET title_raw=?, author_raw=?, isbn_raw=?, language_raw=?,"
            " updated_at=? WHERE id=?",
            (title, author, isbn, language, _now(), file_id),
        )

    def files_with_status(self, status: str) -> list:
        return self.conn.execute(
            "SELECT * FROM files WHERE status=? ORDER BY path", (status,)
        ).fetchall()

    def start_scan_run(self, root: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO scan_runs (started_at, root_path) VALUES (?,?)",
            (_now(), root),
        )
        return cur.lastrowid

    def finish_scan_run(self, run_id: int, seen: int, added: int, changed: int) -> None:
        self.conn.execute(
            "UPDATE scan_runs SET completed_at=?, files_seen=?, files_added=?,"
            " files_changed=? WHERE id=?",
            (_now(), seen, added, changed, run_id),
        )
