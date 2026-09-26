"""One-shot conversion of the old SQLite-as-record model into sidecars,
and rebuilding the derived index from them."""

from collections.abc import Callable

from reshelf.db.database import Database
from reshelf.store import index
from reshelf.store.models import Book, FileEntry
from reshelf.store.sidecar import SidecarStore

Progress = Callable[[int, int | None, str], None]


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

        matched = {}
        if row["matched_edition_id"]:
            matched = db.edition_metadata(row["matched_edition_id"]) or {}

        m = book.metadata
        m.title = matched.get("title") or row["title_raw"] or m.title
        if matched.get("authors"):
            # Split aggregated authors (group_concat result) into a list
            m.authors = [a.strip() for a in matched["authors"].split(";") if a.strip()]
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
        index.sync(db.conn, book, commit=False)
        written += 1
    db.conn.commit()
    return written


def reindex(db: Database, store: SidecarStore, progress: Progress) -> int:
    progress(0, None, "rebuilding index")
    count = index.rebuild(db.conn, store)
    progress(count, count, f"indexed {count} books")
    return count
