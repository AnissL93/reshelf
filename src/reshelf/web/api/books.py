import mimetypes
import re
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response, StreamingResponse

from reshelf.covers import cover_path, has_cover, thumb_path
from reshelf.paths import under
from reshelf.store import index
from reshelf.store.models import now as models_now
from reshelf.web.deps import AppState, get_state
from reshelf.web.schemas import (
    BookDetail,
    BookList,
    BookListItem,
    MetadataPatch,
    WriteBackResult,
)
from reshelf.writeback import (
    EMBEDDABLE,
    UnsupportedWriteBack,
    embed_metadata,
    rename_library_copy,
)

router = APIRouter(tags=["books"])


@router.get("/books", response_model=BookList)
def list_books(
    q: str | None = None,
    status: str | None = None,
    fmt: str | None = Query(None, alias="format"),
    tag: str | None = None,
    resolver: str | None = None,
    sort: Literal["title", "authors", "pubdate", "updated_at", "added"] = "title",
    order: Literal["asc", "desc"] = "asc",
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    state: AppState = Depends(get_state),
) -> BookList:
    covers = state.cfg.library.root / "covers"
    with state.db.lock:
        rows, total = index.query(
            state.db.conn,
            q=q, status=status, fmt=fmt, tag=tag, resolver=resolver,
            sort=sort, order=order,
            limit=page_size, offset=(page - 1) * page_size,
        )
    # has_cover is a Path.exists() per row - fine at page-size scale (<=200),
    # but deliberately never done outside the page being returned.
    items = [
        BookListItem(
            **{k: row.get(k) for k in BookListItem.model_fields if k != "has_cover"},
            has_cover=has_cover(covers, row["sha256"]),
        )
        for row in rows
    ]
    return BookList(items=items, total=total, page=page, page_size=page_size)


def _candidates(state: AppState, sha256: str) -> list[dict]:
    """Candidates from the matches table, joined for the match's own fields.

    Authors come from Database.edition_metadata (group_concat over
    work_authors), never a LIMIT-1 join, or co-authors silently vanish.
    """
    with state.db.lock:
        rows = state.db.conn.execute(
            "SELECT m.edition_id, m.score, m.confidence, m.resolver, m.status,"
            " m.evidence_json"
            " FROM matches m"
            " WHERE m.file_id IN (SELECT id FROM files WHERE sha256 = ?)"
            " ORDER BY m.score DESC LIMIT 25",
            (sha256,),
        ).fetchall()
        candidates = []
        for row in rows:
            candidate = dict(row)
            candidate.update(state.db.edition_metadata(row["edition_id"]) or {})
            candidates.append(candidate)
    return candidates


def _library_dir(state: AppState) -> Path:
    return Path(state.cfg.library.root) / "library"


@router.get("/books/{sha256}", response_model=BookDetail)
def get_book(sha256: str, state: AppState = Depends(get_state)) -> BookDetail:
    with state.db.lock:
        rows = state.db.conn.execute(
            "SELECT * FROM files WHERE sha256 = ? ORDER BY id", (sha256,)
        ).fetchall()
    book = state.store.load(sha256, rows[0]["path"] if rows else None)
    if book is None:
        raise HTTPException(404, "no such book")
    primary = book.primary_file(_library_dir(state))
    return BookDetail(
        sha256=sha256,
        sidecar=book.model_dump(mode="json", by_alias=True),
        status=rows[0]["status"] if rows else None,
        paths=[r["path"] for r in rows],
        candidates=_candidates(state, sha256),
        has_cover=has_cover(state.cfg.library.root / "covers", sha256),
        # Resolved here, once, with the library root in hand. The SPA used
        # to re-derive it from files[] and could not do the containment
        # check without the root, so it kept a fourth copy of the ranking.
        primary_format=primary.format if primary else None,
    )


@router.get("/books/{sha256}/candidates")
def get_candidates(sha256: str, state: AppState = Depends(get_state)) -> list[dict]:
    """The same list the detail response carries, for clients that only want it."""
    return get_book(sha256, state).candidates


@router.get("/tags")
def list_tags(state: AppState = Depends(get_state)) -> list[str]:
    with state.db.lock:
        rows = state.db.conn.execute(
            "SELECT tags FROM book_index WHERE tags IS NOT NULL AND tags != ''"
        ).fetchall()
    tags = {t.strip() for r in rows for t in r["tags"].split(";") if t.strip()}
    return sorted(tags)


@router.patch("/books/{sha256}/metadata", response_model=WriteBackResult)
def patch_metadata(
    sha256: str,
    payload: MetadataPatch,
    state: AppState = Depends(get_state),
) -> WriteBackResult:
    with state.db.lock:
        row = state.db.conn.execute(
            "SELECT * FROM files WHERE sha256 = ? ORDER BY id LIMIT 1", (sha256,)
        ).fetchone()
    if state.store.load(sha256, row["path"] if row else None) is None:
        raise HTTPException(404, "no such book")

    # Tier 1: always, and it is the source of truth - never rolled back
    # because a later tier failed.
    def mutate(book):
        book.metadata = payload.metadata
        book.source.resolver = "human"
        book.source.confidence = 1.0
        book.source.decided_at = models_now()

    book = state.store.update(sha256, mutate, row["path"] if row else None)
    with state.db.lock:
        index.sync(state.db.conn, book)

    warnings: list[str] = []
    library_file = None
    embedded = False

    library_dir = _library_dir(state)

    # Tier 3 before tier 2, so embedding targets a stable path - renaming
    # first would move the file out from under the path we're about to
    # write into.
    if payload.write_back.embed:
        primary = book.primary_file(library_dir)
        if primary is None:
            warnings.append("no file on disk to embed into")
        elif (
            primary.format in EMBEDDABLE
            and primary.role != "converted"
            and not under(primary.path, library_dir)
        ):
            # Format is fine but the only copy is the incoming/ original -
            # embed_metadata's own convert_first check would never catch
            # this, and writing into it would rewrite (and re-hash) the
            # user's source file. Never let tier 3 reach an original.
            raise HTTPException(
                422,
                {
                    "message": (
                        "only a library copy or a converted file can be"
                        " edited in place; commit it to the library first"
                    ),
                    "reason": "not_in_library",
                },
            )
        else:
            # A sidecar's files[] is user-editable JSON and the file it
            # names can be deleted out of band; embed_metadata on a
            # missing path is an unhandled OSError (a 500) rather than
            # something the UI can explain. Tier 1 is already written at
            # this point, so report it in the same shape as the other
            # tier-3 refusals.
            target = Path(primary.path)
            if not target.exists():
                raise HTTPException(
                    422,
                    {
                        "message": f"no file at {target}; rescan or reconvert the book",
                        "reason": "file_missing",
                    },
                )
            try:
                embed_metadata(target, book.metadata)
                embedded = True
            except UnsupportedWriteBack as e:
                raise HTTPException(
                    422, {"message": str(e), "reason": e.reason}
                ) from e

    if payload.write_back.library_file:
        library_file = rename_library_copy(state.cfg, state.db, state.store, sha256)
        if library_file is None:
            warnings.append("not committed to library/ yet; nothing to rename")
        else:
            # The sidecar locator is `row["path"]` - the same one the
            # mutate above used, and the one rename_library_copy itself
            # stores under. Reloading on the *new library path* asks a
            # sidecar|library layout for a JSON file that does not exist.
            book = state.store.load(sha256, row["path"] if row else None) or book

    return WriteBackResult(
        sidecar=book.model_dump(mode="json", by_alias=True),
        library_file=library_file,
        embedded=embedded,
        warnings=warnings,
    )


_RANGE = re.compile(r"^bytes=(\d*)-(\d*)$")
_CHUNK = 1 << 18  # 256 KiB


def _parse_range(header: str, size: int) -> tuple[int, int] | None | Literal[False]:
    """(start, end) inclusive; None means 'serve the whole thing'; False means 416."""
    match = _RANGE.match(header.strip())
    if not match:
        return None
    first, last = match.group(1), match.group(2)
    if not first and not last:
        return None
    if not first:  # bytes=-N, the final N bytes
        length = int(last)
        if length <= 0:
            return False
        return max(0, size - length), size - 1
    start = int(first)
    end = int(last) if last else size - 1
    if start >= size or start > end:
        return False
    return start, min(end, size - 1)


@router.get("/books/{sha256}/file")
def get_file(
    sha256: str,
    request: Request,
    original: bool = False,
    path: str | None = None,
    state: AppState = Depends(get_state),
):
    """Serve one of the book's files.

    `path` names exactly which `files[]` entry to serve - the detail view
    lists every one of them, and `original=true` alone cannot say *which*
    original was clicked (a committed book has two). `original` is kept
    for callers that just want "the source file, whichever it is".
    """
    with state.db.lock:
        row = state.db.conn.execute(
            "SELECT path FROM files WHERE sha256 = ? ORDER BY id LIMIT 1", (sha256,)
        ).fetchone()
    book = state.store.load(sha256, row["path"] if row else None)
    if book is None:
        raise HTTPException(404, "no such book")
    if path is not None:
        entry = next((f for f in book.files if f.path == path), None)
    elif original:
        entry = next((f for f in book.files if f.role == "original"), None)
    else:
        entry = book.primary_file(_library_dir(state))
    if entry is None:
        raise HTTPException(404, "no file recorded for this book")
    # No root containment here: a library legitimately spans wherever the
    # user pointed `scan`, so most sidecars record a path outside
    # library.root and gating on it 404s the whole library. What keeps this
    # from being an arbitrary-file reader is above - `?path=` must match a
    # recorded files[] entry exactly, so HTTP input can never name a new
    # path. The remaining input is the sidecar itself, which only the
    # operator can write.
    path = Path(entry.path)
    if not path.is_file():
        raise HTTPException(404, "file missing")

    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    size = path.stat().st_size
    headers = {"Accept-Ranges": "bytes"}

    range_header = request.headers.get("range")
    parsed = _parse_range(range_header, size) if range_header else None
    if parsed is False:
        return Response(
            status_code=416, headers={**headers, "Content-Range": f"bytes */{size}"}
        )

    # Starlette's own FileResponse has a built-in Range engine that reads the
    # Range header straight from the ASGI scope - passing it a Range-bearing
    # request would re-parse the header a second time and 400 on a malformed
    # one instead of the 200 fallback this contract wants. So a Range header,
    # valid or not, is always served by our own streaming below; FileResponse
    # is only safe when there is no Range header at all.
    if parsed is None:
        if not range_header:
            return FileResponse(path, media_type=media_type, headers=headers)
        start, end, status_code = 0, size - 1, 200
    else:
        start, end, status_code = *parsed, 206

    length = end - start + 1

    def chunks():
        with path.open("rb") as fh:
            fh.seek(start)
            remaining = length
            while remaining > 0:
                data = fh.read(min(_CHUNK, remaining))
                if not data:
                    return
                remaining -= len(data)
                yield data

    response_headers = {**headers, "Content-Length": str(length)}
    if status_code == 206:
        response_headers["Content-Range"] = f"bytes {start}-{end}/{size}"

    return StreamingResponse(
        chunks(),
        status_code=status_code,
        media_type=media_type,
        headers=response_headers,
    )


@router.get("/books/{sha256}/cover")
def get_cover(
    sha256: str,
    size: Literal["thumb", "full"] = "full",
    state: AppState = Depends(get_state),
):
    covers = state.cfg.library.root / "covers"
    path = thumb_path(covers, sha256) if size == "thumb" else cover_path(covers, sha256)
    if not path.is_file():
        raise HTTPException(404, "no cover")
    return FileResponse(
        path, media_type="image/jpeg", headers={"Cache-Control": "max-age=86400"}
    )
