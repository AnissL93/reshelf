"""Export the organized library into a Calibre library via calibredb.

A Calibre library is defined by its ``metadata.db``, so an organized folder tree
cannot simply be adopted -- the books have to be imported. This module feeds
``calibredb`` the metadata we already matched, instead of letting Calibre guess
from file contents.
"""

import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

_DATE_FORMATS = ("%Y-%m-%d", "%b %d, %Y", "%B %d, %Y", "%Y-%m", "%Y")


@dataclass
class CalibreBook:
    path: str
    title: str
    authors: str | None
    publisher: str | None
    pubdate: str | None
    language: str | None
    isbn: str | None
    identifiers: dict = field(default_factory=dict)


def normalize_pubdate(value: str | None) -> str | None:
    """Coerce the assorted provider date forms into ISO ``YYYY-MM-DD``."""
    if not value:
        return None
    raw = value.strip()
    # Zero-pad "2018-10-1" so strptime's %Y-%m-%d accepts it.
    m = re.fullmatch(r"(\d{4})-(\d{1,2})(?:-(\d{1,2}))?", raw)
    if m:
        y, mo, d = m.group(1), int(m.group(2)), int(m.group(3) or 1)
        raw = f"{y}-{mo:02d}-{d:02d}"
    for fmt in _DATE_FORMATS:
        try:
            parsed = datetime.strptime(raw, fmt)
        except ValueError:
            continue
        return parsed.strftime("%Y-%m-%d")
    return None


def add_command(book: CalibreBook, library_path: str) -> list[str]:
    cmd = ["calibredb", "add", "--library-path", str(library_path), "--title", book.title]
    if book.authors:
        cmd += ["--authors", book.authors]
    if book.isbn:
        cmd += ["--isbn", book.isbn]
    if book.language:
        cmd += ["--languages", book.language]
    for kind, value in sorted(book.identifiers.items()):
        cmd += ["--identifier", f"{kind}:{value}"]
    cmd.append(book.path)
    return cmd


def set_metadata_command(book_id: int, book: CalibreBook, library_path: str) -> list[str] | None:
    """`calibredb add` has no publisher/pubdate flags; set them in a second pass."""
    fields = []
    if book.publisher:
        fields += ["--field", f"publisher:{book.publisher}"]
    if book.pubdate:
        fields += ["--field", f"pubdate:{book.pubdate}"]
    if not fields:
        return None
    return ["calibredb", "set_metadata", str(book_id),
            "--library-path", str(library_path)] + fields


def parse_added_ids(output: str) -> list[int]:
    m = re.search(r"Added book ids:\s*([\d,\s]+)", output)
    if not m:
        return []
    return [int(x) for x in re.findall(r"\d+", m.group(1))]


_QUERY = """
SELECT f.path AS src,
       w.canonical_title AS title,
       (SELECT group_concat(a.canonical_name, ' & ')
          FROM work_authors wa JOIN authors a ON a.id = wa.author_id
         WHERE wa.work_id = w.id) AS authors,
       e.publisher, e.publication_date, e.language,
       COALESCE(NULLIF(e.isbn13, ''), NULLIF(e.isbn10, '')) AS isbn,
       e.id AS edition_id
  FROM files f
  JOIN editions e ON e.id = f.matched_edition_id
  JOIN works w ON w.id = e.work_id
 WHERE f.status = 'COMMITTED'
"""


def books_to_export(db, journals: list[dict]) -> list[CalibreBook]:
    """Join matched metadata onto the library paths recorded in commit journals."""
    dest_by_src = {}
    for journal in journals:
        for action in journal.get("actions", []):
            if action.get("action") == "import":
                dest_by_src[action["src"]] = action["dest"]

    books = []
    for row in db.conn.execute(_QUERY):
        dest = dest_by_src.get(row["src"])
        if dest is None or not Path(dest).exists():
            continue
        ids = {
            r["type"]: r["value"]
            for r in db.conn.execute(
                "SELECT type, value FROM identifiers WHERE edition_id=? AND type<>'isbn13'",
                (row["edition_id"],),
            )
        }
        books.append(
            CalibreBook(
                path=dest,
                title=row["title"],
                authors=row["authors"],
                publisher=row["publisher"],
                pubdate=normalize_pubdate(row["publication_date"]),
                language=row["language"],
                isbn=row["isbn"],
                identifiers=ids,
            )
        )
    return books


def export(books: list[CalibreBook], library_path: str, dry_run: bool = False,
           on_progress=None) -> dict:
    """Import each book into the Calibre library. Returns a summary dict."""
    added, failed, skipped = [], [], []
    for i, book in enumerate(books, 1):
        if on_progress:
            on_progress(i, len(books), book)
        cmd = add_command(book, library_path)
        if dry_run:
            added.append({"path": book.path, "command": cmd})
            continue
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            failed.append({"path": book.path, "error": (proc.stderr or proc.stdout).strip()})
            continue
        ids = parse_added_ids(proc.stdout)
        if not ids:
            skipped.append({"path": book.path, "reason": "duplicate or not added"})
            continue
        meta_cmd = set_metadata_command(ids[0], book, library_path)
        if meta_cmd:
            subprocess.run(meta_cmd, capture_output=True, text=True)
        added.append({"path": book.path, "book_id": ids[0]})
    return {"added": added, "skipped": skipped, "failed": failed}
