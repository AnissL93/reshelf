"""Derived search index over the sidecars.

Nothing here is authoritative: drop the database and `reshelf reindex`
rebuilds it from the sidecars plus a rescan. Pipeline status is NOT
copied in; it is joined from `files`, which owns it.
"""

import re
import sqlite3

from reshelf.store.models import Book
from reshelf.store.sidecar import SidecarStore

SORTS = {
    "title": "bi.title",
    "authors": "bi.authors",
    "pubdate": "bi.pubdate",
    "updated_at": "bi.updated_at",
    "added": "f.id",
}

# One canonical files row per hash, so byte-identical duplicates collapse.
_BASE = """
FROM book_index bi
LEFT JOIN files f ON f.id = (
    SELECT MIN(id) FROM files WHERE sha256 = bi.sha256
)
"""

_WORD = re.compile(r"[^\w一-鿿]+")


def _fts_query(q: str) -> str | None:
    """FTS5 MATCH is a query language; user text must not be handed to it raw."""
    terms = [t for t in _WORD.split(q) if t]
    if not terms:
        return None
    return " ".join(f'"{t}"' for t in terms)


def sync(conn: sqlite3.Connection, book: Book, commit: bool = True) -> None:
    m = book.metadata
    primary = book.primary_file()
    conn.execute(
        """
        INSERT INTO book_index (sha256, title, authors, series, series_index,
            publisher, pubdate, language, isbn13, tags, resolver, confidence,
            primary_path, primary_format, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(sha256) DO UPDATE SET
            title=excluded.title, authors=excluded.authors, series=excluded.series,
            series_index=excluded.series_index, publisher=excluded.publisher,
            pubdate=excluded.pubdate, language=excluded.language,
            isbn13=excluded.isbn13, tags=excluded.tags, resolver=excluded.resolver,
            confidence=excluded.confidence, primary_path=excluded.primary_path,
            primary_format=excluded.primary_format, updated_at=excluded.updated_at
        """,
        (
            book.sha256, m.title, "; ".join(m.authors), m.series, m.series_index,
            m.publisher, m.pubdate, m.language, m.isbn13, "; ".join(m.tags),
            book.source.resolver, book.source.confidence,
            primary.path if primary else None,
            primary.format if primary else None,
            book.updated_at,
        ),
    )
    # FTS5 has no upsert; delete then insert.
    conn.execute("DELETE FROM books_fts WHERE sha256 = ?", (book.sha256,))
    conn.execute(
        "INSERT INTO books_fts (sha256, title, authors, series, publisher, tags,"
        " description) VALUES (?,?,?,?,?,?,?)",
        (
            book.sha256, m.title or "", "; ".join(m.authors), m.series or "",
            m.publisher or "", "; ".join(m.tags), m.description or "",
        ),
    )
    if commit:
        conn.commit()


def remove(conn: sqlite3.Connection, sha256: str) -> None:
    conn.execute("DELETE FROM book_index WHERE sha256 = ?", (sha256,))
    conn.execute("DELETE FROM books_fts WHERE sha256 = ?", (sha256,))
    conn.commit()


def rebuild(conn: sqlite3.Connection, store: SidecarStore) -> int:
    conn.execute("DELETE FROM book_index")
    conn.execute("DELETE FROM books_fts")
    count = 0
    for book in store.iter_all():
        sync(conn, book, commit=False)
        count += 1
    conn.commit()
    return count


def query(
    conn: sqlite3.Connection,
    *,
    q: str | None = None,
    status: str | None = None,
    fmt: str | None = None,
    tag: str | None = None,
    resolver: str | None = None,
    sort: str = "title",
    order: str = "asc",
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict], int]:
    if sort not in SORTS:
        raise ValueError(f"sort must be one of {sorted(SORTS)}")
    direction = "DESC" if order.lower() == "desc" else "ASC"

    where: list[str] = []
    params: list[object] = []
    if q:
        match = _fts_query(q)
        if match is None:
            return [], 0
        where.append(
            "bi.sha256 IN (SELECT sha256 FROM books_fts WHERE books_fts MATCH ?)"
        )
        params.append(match)
    if status:
        where.append("f.status = ?")
        params.append(status)
    if fmt:
        where.append("bi.primary_format = ?")
        params.append(fmt)
    if tag:
        where.append("(';' || bi.tags || ';') LIKE ?")
        params.append(f"%;{tag};%".replace(";;", ";"))
    if resolver:
        where.append("bi.resolver = ?")
        params.append(resolver)
    clause = (" WHERE " + " AND ".join(where)) if where else ""

    total = conn.execute(
        f"SELECT COUNT(*) {_BASE}{clause}", params
    ).fetchone()[0]
    rows = conn.execute(
        f"SELECT bi.*, f.status, f.path {_BASE}{clause}"
        f" ORDER BY {SORTS[sort]} {direction} LIMIT ? OFFSET ?",
        [*params, limit, offset],
    ).fetchall()
    return [dict(r) for r in rows], total
