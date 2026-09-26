"""Optional write-back beyond the sidecar.

Tier 1 (the sidecar) always happens and lives in `store.sidecar`. These are
the escalating, opt-in extras on top:

Tier 2, `rename_library_copy`, renames the committed copy under library/ to
follow corrected metadata. It journals the move in the same shape
`apply_plan` writes (`reports/commit-<id>.json`, entries under `"actions"`),
so `reshelf rollback <id>` can undo it exactly like any other commit. The
source it renames is whichever `book.files` entry the sidecar records under
`<root>/library` - never the DB `files.path`, which (in the default "copy"
commit mode) keeps pointing at the `incoming/` original forever and is not
a safe stand-in for "where the committed copy currently lives".

Tier 3, `embed_metadata`, writes metadata into the file itself - EPUB via
the OPF rewriter, PDF via pymupdf. It only ever touches whatever path it is
handed; callers are responsible for handing it a library copy or a derived
file, never an original under incoming/.
"""

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pymupdf as fitz

from reshelf.convert.epub_writer import UnsupportedEpub, rewrite_metadata
from reshelf.paths import under
from reshelf.planner.committer import dest_for, unique_dest
from reshelf.store import index
from reshelf.store.models import Book, BookMetadata, FileEntry

EMBEDDABLE: frozenset[str] = frozenset({"epub", "pdf"})


class UnsupportedWriteBack(Exception):
    """Raised when `embed_metadata` cannot write into a file's format."""

    def __init__(self, message: str, reason: str):
        super().__init__(message)
        self.reason = reason


def embed_metadata(path: Path, metadata: BookMetadata) -> None:
    """Write `metadata` into `path` itself. EPUB and PDF only.

    Raises UnsupportedWriteBack(reason="convert_first") for formats with no
    writer here (MOBI/AZW3/DJVU/TXT - there is no writer without Calibre),
    and reason="unwritable" for an EPUB/PDF that is present but malformed.
    """
    path = Path(path)
    fmt = path.suffix.lower().lstrip(".")
    if fmt not in EMBEDDABLE:
        raise UnsupportedWriteBack(
            f"cannot write metadata into a {fmt} file; convert it to EPUB first",
            reason="convert_first",
        )
    if fmt == "epub":
        try:
            rewrite_metadata(path, metadata)
        except UnsupportedEpub as e:
            raise UnsupportedWriteBack(str(e), reason="unwritable") from e
        return

    # PDF: pymupdf can only save non-incrementally to a *different* path
    # than the one it opened - saving in place requires an incremental
    # save. Fall back to write-to-temp-then-replace (same atomic pattern as
    # epub_writer._write_zip) when incremental save isn't available, so a
    # failed save never leaves a half-written PDF behind.
    tmp: Path | None = None
    with fitz.open(str(path)) as doc:
        info = dict(doc.metadata or {})
        info["title"] = metadata.title or ""
        info["author"] = "; ".join(metadata.authors)
        if metadata.publisher:
            info["producer"] = metadata.publisher
        doc.set_metadata(info)
        if doc.can_save_incrementally():
            doc.saveIncr()
        else:
            tmp = path.with_name(path.name + ".tmp")
            doc.save(str(tmp), deflate=True)
    if tmp is not None:
        os.replace(tmp, path)


def _write_journal(cfg, entries: list[dict]) -> None:
    """Same shape `apply_plan` writes, so `rollback_journal` can read it."""
    now = datetime.now(timezone.utc)
    commit_id = now.strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:8]
    journal = {
        "commit_id": commit_id,
        "plan_id": None,
        "created_at": now.isoformat(),
        "dry_run": False,
        "actions": entries,
        "skipped": [],
    }
    reports_dir = Path(cfg.library.root) / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    (reports_dir / f"commit-{commit_id}.json").write_text(
        json.dumps(journal, ensure_ascii=False, indent=2)
    )


def _library_entry(book: Book, library_dir: Path) -> FileEntry | None:
    """The `book.files` entry that actually lives under `library_dir`, if any."""
    return next((e for e in book.files if under(e.path, library_dir)), None)


def rename_library_copy(cfg, db, store, sha256: str) -> str | None:
    """Re-derive the library path from current metadata and move the copy there.

    Returns the new path, or None if the book has no committed library copy.
    """
    row = db.conn.execute(
        "SELECT * FROM files WHERE sha256 = ? AND status = 'COMMITTED' ORDER BY id LIMIT 1",
        (sha256,),
    ).fetchone()
    if row is None:
        return None
    book = store.load(sha256, row["path"])
    if book is None:
        return None

    library_dir = Path(cfg.library.root) / "library"
    old_entry = _library_entry(book, library_dir)
    if old_entry is None:
        return None
    old = Path(old_entry.path)
    if not old.exists():
        return None

    m = book.metadata
    action = {
        "file": str(old),
        "metadata_changes": {
            "title": m.title,
            "author": "; ".join(m.authors) or None,
            "isbn13": m.isbn13,
            "publisher": m.publisher,
            "publication_date": m.pubdate,
        },
    }
    new, _already = unique_dest(dest_for(action, library_dir), sha256)
    if new == old:
        return str(old)

    # `_already` (a byte-identical copy of the original already sitting at
    # the derived name - an earlier rename that died before the bookkeeping
    # below) is deliberately not a special case: skipping the move used to
    # repoint the sidecar at `new` and leave `old` behind with nothing
    # describing it. shutil.move overwrites `new` with `old`, which is the
    # copy this book's files[] actually names, so the leftover goes away
    # and exactly one file is left holding the bytes.
    new.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(old), str(new))
    # Journal the move the instant it has happened, before any of the
    # bookkeeping below - if something after this raises (or the process
    # dies), `reshelf rollback` must still have something to undo. Mirrors
    # apply_plan's try/finally journal-on-partial-failure guarantee for a
    # single action.
    _write_journal(
        cfg,
        [{"action": "import", "src": str(old), "dest": str(new), "moved": True}],
    )

    def mutate(b: Book) -> None:
        for entry in b.files:
            if entry.path == old_entry.path:
                entry.path = str(new)

    # The locator identifies the *document*, not where its bytes currently
    # sit: under metadata.layout=sidecar|library, path_for() uses it to
    # find the JSON, and the JSON did not move when the file did. Loading
    # on row["path"] (above) and storing on str(new) looked for a sidecar
    # that was never there - a KeyError raised *after* the move and the
    # journal, leaving the file somewhere nothing recorded.
    book = store.update(sha256, mutate, row["path"])
    index.sync(db.conn, book)
    return str(new)
