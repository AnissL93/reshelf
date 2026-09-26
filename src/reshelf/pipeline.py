"""Pipeline stages as plain functions.

Command bodies used to live in cli.py, where nothing else could call
them. Everything here takes an open Database and a progress callback, so
the CLI and the web job runner share one implementation. The callback
raises JobCancelled to stop a run cooperatively.
"""

from collections.abc import Callable
from pathlib import Path

from reshelf.config import Config
from reshelf.db.database import Database
from reshelf.extractors.base import ExtractionError
from reshelf.extractors.epub import extract_epub
from reshelf.extractors.mobi import extract_mobi
from reshelf.extractors.pdf import extract_pdf
from reshelf.metadata.isbn import find_isbns
from reshelf.scanner.hashing import sha256_file
from reshelf.scanner.scanner import iter_files
from reshelf.store import index
from reshelf.store.models import Book, FileEntry
from reshelf.store.sidecar import SidecarStore

Progress = Callable[[int, int | None, str], None]


class JobCancelled(Exception):
    """Raised by a progress callback to stop a running stage."""


EXTRACTORS = {
    "epub": extract_epub,
    "pdf": extract_pdf,
    "mobi": extract_mobi,
    "azw": extract_mobi,
    "azw3": extract_mobi,
}


def scan(
    cfg: Config, db: Database, store: SidecarStore, target: Path, progress: Progress
) -> dict:
    seen = added = changed = duplicates = 0
    run_id = db.start_scan_run(str(target))
    for fi in iter_files(Path(target), cfg.scan.formats, cfg.scan.recursive):
        seen += 1
        progress(seen, None, fi.path)
        previous = db.conn.execute(
            "SELECT sha256 FROM files WHERE path = ?", (fi.path,)
        ).fetchone()
        old_sha = previous["sha256"] if previous else None

        fid, state = db.upsert_file(fi.path, fi.size, fi.mtime, fi.extension)
        if state == "unchanged":
            continue
        added += state == "new"
        changed += state == "changed"
        new_sha = sha256_file(Path(fi.path))
        if db.set_hash(fid, new_sha):
            duplicates += 1
        if old_sha and old_sha != new_sha:
            _carry_sidecar(db, store, old_sha, new_sha, fi)
        db.conn.commit()
    db.finish_scan_run(run_id, seen, added, changed)
    db.conn.commit()
    return {"seen": seen, "added": added, "changed": changed, "duplicates": duplicates}


def _carry_sidecar(db, store, old_sha: str, new_sha: str, fi) -> None:
    """A file's content changed at a known path - keep its metadata.

    # ponytail: keyed on the path staying the same. Proper edition linking
    # only if re-downloads with renames turn out to be common.
    """
    old = store.load(old_sha, fi.path)
    if old is None:
        return
    # Under "hash" layout, load(new_sha, ...) genuinely looks up the new
    # hash's own file, so a hit here means one already exists - don't
    # clobber it. Under "sidecar"/"library" layout, path_for ignores the
    # sha argument entirely, so this load returns the same not-yet-rewritten
    # sidecar as `old` above; checking its own .sha256 tells them apart.
    existing = store.load(new_sha, fi.path)
    if existing is not None and existing.sha256 == new_sha:
        return
    carried = old.model_copy(deep=True)
    carried.sha256 = new_sha
    for entry in carried.files:
        if entry.path == fi.path:
            entry.size, entry.mtime = fi.size, fi.mtime
    carried.files = [f for f in carried.files if f.role != "converted"]

    old_path = store.path_for(old_sha, fi.path)
    new_path = store.path_for(new_sha, fi.path)
    store.save(carried, fi.path)

    # Duplicates share a hash. If another path still carries old_sha (set_hash
    # has already recorded new_sha for this path, so this only matches other
    # files), its sidecar and index row are still legitimately in use.
    still_used = db.conn.execute(
        "SELECT 1 FROM files WHERE sha256 = ? AND path != ? LIMIT 1",
        (old_sha, fi.path),
    ).fetchone()
    # Under "sidecar"/"library" layout old_path == new_path: deleting after
    # save would destroy the file we just wrote.
    if not still_used and old_path != new_path:
        store.delete(old_sha, fi.path)
    if not still_used:
        index.remove(db.conn, old_sha)
    index.sync(db.conn, carried)


def extract(
    cfg: Config, db: Database, store: SidecarStore, force: bool, progress: Progress
) -> dict:
    statuses = ["SCANNED"] + (["ERROR", "IDENTIFIED"] if force else [])
    rows = [r for s in statuses for r in db.files_with_status(s)]
    total = len(rows)
    done = errors = 0
    for n, row in enumerate(rows, 1):
        progress(n, total, row["path"])
        path = Path(row["path"])
        extractor = EXTRACTORS.get(row["format"])
        try:
            if extractor is None:
                raise ExtractionError(f"unsupported format {row['format']}")
            meta = extractor(path)
        except ExtractionError:
            db.set_status(row["id"], "ERROR")
            errors += 1
            continue
        isbns = meta.isbns or find_isbns(path.name)
        db.set_raw_metadata(
            row["id"],
            title=meta.title,
            author="; ".join(meta.authors) or None,
            isbn=isbns[0] if isbns else None,
            language=meta.language,
        )
        db.set_status(row["id"], "IDENTIFIED")
        _write_extracted_sidecar(db, store, row, meta, isbns)
        done += 1
    db.conn.commit()
    return {"extracted": done, "errors": errors}


def _write_extracted_sidecar(db, store, row, meta, isbns) -> None:
    """Every hashed file gets a sidecar, even an empty one - see Task 4."""
    sha = row["sha256"]
    if not sha:
        return
    book = store.load(sha, row["path"]) or Book(sha256=sha)
    if book.is_human:
        return
    if not any(f.path == row["path"] for f in book.files):
        book.files.append(
            FileEntry(
                path=row["path"],
                format=row["format"] or "",
                size=row["size"] or 0,
                mtime=row["mtime"] or 0,
            )
        )
    if book.source.resolver in ("embedded",):
        book.metadata.title = meta.title or book.metadata.title
        if meta.authors:
            book.metadata.authors = list(meta.authors)
        book.metadata.language = meta.language or book.metadata.language
        if isbns:
            book.metadata.isbn13 = isbns[0]
    store.save(book, row["path"])
    index.sync(db.conn, book)
