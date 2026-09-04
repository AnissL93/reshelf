import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from reshelf.metadata.models import Candidate

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
                f"another reshelf instance holds {self.lock_path} "
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

    def save_candidate(self, cand: Candidate) -> int:
        e = cand.edition
        w = e.work
        row = self.conn.execute(
            "SELECT id FROM works WHERE canonical_title=?", (w.title,)
        ).fetchone()
        if row:
            work_id = row["id"]
        else:
            work_id = self.conn.execute(
                "INSERT INTO works (canonical_title, original_title, original_language)"
                " VALUES (?,?,?)",
                (w.title, w.original_title, w.original_language),
            ).lastrowid
        for a in w.authors:
            arow = self.conn.execute(
                "SELECT id FROM authors WHERE canonical_name=?", (a.name,)
            ).fetchone()
            author_id = (
                arow["id"]
                if arow
                else self.conn.execute(
                    "INSERT INTO authors (canonical_name) VALUES (?)", (a.name,)
                ).lastrowid
            )
            self.conn.execute(
                "INSERT OR IGNORE INTO work_authors (work_id, author_id) VALUES (?,?)",
                (work_id, author_id),
            )
        erow = None
        if e.isbn13:
            erow = self.conn.execute(
                "SELECT id FROM editions WHERE isbn13=?", (e.isbn13,)
            ).fetchone()
        if erow:
            edition_id = erow["id"]
        else:
            edition_id = self.conn.execute(
                "INSERT INTO editions (work_id, isbn10, isbn13, publisher,"
                " publication_date, language, edition_name) VALUES (?,?,?,?,?,?,?)",
                (work_id, e.isbn10, e.isbn13, e.publisher, e.publication_date,
                 e.language, e.edition_name),
            ).lastrowid
        if e.isbn13:
            self.conn.execute(
                "INSERT OR IGNORE INTO identifiers (edition_id, type, value, source)"
                " VALUES (?,'isbn13',?,?)",
                (edition_id, e.isbn13, cand.provider),
            )
        self.conn.execute(
            "INSERT OR IGNORE INTO metadata_sources (edition_id, provider,"
            " provider_id, retrieved_at) VALUES (?,?,?,?)",
            (edition_id, cand.provider, cand.provider_id, _now()),
        )
        return edition_id

    def record_match(
        self,
        file_id: int,
        edition_id: int,
        score: float,
        confidence: float,
        resolver: str,
        evidence: list[str],
        band: str,
    ) -> None:
        self.conn.execute(
            "INSERT INTO matches (file_id, edition_id, score, confidence, resolver,"
            " evidence_json, status, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (file_id, edition_id, score, confidence, resolver,
             json.dumps(evidence), band, _now()),
        )

    def set_file_match(
        self,
        file_id: int,
        edition_id: int | None,
        confidence: float,
        status: str,
    ) -> None:
        self.conn.execute(
            "UPDATE files SET matched_edition_id=?, match_confidence=?, status=?,"
            " updated_at=? WHERE id=?",
            (edition_id, confidence, status, _now(), file_id),
        )
