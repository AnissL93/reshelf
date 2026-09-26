"""One-shot conversion of the old SQLite-as-record model into sidecars,
and rebuilding the derived index from them."""

from collections.abc import Callable

from reshelf.db.database import Database
from reshelf.store import index
from reshelf.store.models import Book, FileEntry
from reshelf.store.sidecar import SidecarStore

Progress = Callable[[int, int | None, str], None]


def _matched_metadata(db: Database, file_id: int) -> dict:
    """Provider metadata for a MATCHED file, from the candidate cache."""
    row = db.conn.execute(
        "SELECT w.canonical_title AS title, e.isbn13, e.isbn10, e.publisher,"
        " e.publication_date, e.language,"
        " (SELECT a.canonical_name FROM work_authors wa"
        "   JOIN authors a ON a.id = wa.author_id"
        "   WHERE wa.work_id = w.id LIMIT 1) AS author"
        " FROM files f JOIN editions e ON e.id = f.matched_edition_id"
        " JOIN works w ON w.id = e.work_id WHERE f.id = ?",
        (file_id,),
    ).fetchone()
    return dict(row) if row else {}


def migrate_json(db: Database, store: SidecarStore, progress: Progress) -> int:
    rows = db.conn.execute(
        "SELECT * FROM files WHERE sha256 IS NOT NULL ORDER BY id"
    ).fetchall()
    total = len(rows)
    written = 0
    for n, row in enumerate(rows, 1):
        progress(n, total, row["path"])
        sha = row["sha256"]
        existing = store.load(sha, row["path"])
        if existing is not None and existing.is_human:
            continue  # a human decision is sticky

        book = existing or Book(sha256=sha)
        entry = FileEntry(
            path=row["path"],
            format=row["format"] or "",
            size=row["size"] or 0,
            mtime=row["mtime"] or 0,
        )
        if not any(f.path == entry.path for f in book.files):
            book.files.append(entry)

        matched = _matched_metadata(db, row["id"]) if row["matched_edition_id"] else {}
        m = book.metadata
        m.title = matched.get("title") or row["title_raw"] or m.title
        if matched.get("author"):
            m.authors = [matched["author"]]
        elif row["author_raw"] and not m.authors:
            m.authors = [a.strip() for a in row["author_raw"].split(";") if a.strip()]
        m.isbn13 = matched.get("isbn13") or row["isbn_raw"] or m.isbn13
        m.isbn10 = matched.get("isbn10") or m.isbn10
        m.publisher = matched.get("publisher") or m.publisher
        m.pubdate = matched.get("publication_date") or m.pubdate
        m.language = matched.get("language") or row["language_raw"] or m.language

        book.source.resolver = "deterministic" if matched else "embedded"
        book.source.confidence = row["match_confidence"] or 0.0

        store.save(book, row["path"])
        index.sync(db.conn, book)
        written += 1
    return written


def reindex(db: Database, store: SidecarStore, progress: Progress) -> int:
    progress(0, None, "rebuilding index")
    count = index.rebuild(db.conn, store)
    progress(count, count, f"indexed {count} books")
    return count
