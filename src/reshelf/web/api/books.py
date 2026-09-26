import mimetypes
import re
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response, StreamingResponse

from reshelf.covers import cover_path, thumb_path
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
            has_cover=cover_path(covers, row["sha256"]).exists(),
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


@router.get("/books/{sha256}", response_model=BookDetail)
def get_book(sha256: str, state: AppState = Depends(get_state)) -> BookDetail:
    with state.db.lock:
        rows = state.db.conn.execute(
            "SELECT * FROM files WHERE sha256 = ? ORDER BY id", (sha256,)
        ).fetchall()
    book = state.store.load(sha256, rows[0]["path"] if rows else None)
    if book is None:
        raise HTTPException(404, "no such book")
    return BookDetail(
        sha256=sha256,
        sidecar=book.model_dump(mode="json", by_alias=True),
        status=rows[0]["status"] if rows else None,
        paths=[r["path"] for r in rows],
        candidates=_candidates(state, sha256),
        has_cover=cover_path(state.cfg.library.root / "covers", sha256).exists(),
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

    # Tier 3 before tier 2, so embedding targets a stable path - renaming
    # first would move the file out from under the path we're about to
    # write into.
    if payload.write_back.embed:
        primary = book.primary_file()
        if primary is None:
            warnings.append("no file on disk to embed into")
        elif (
            primary.format in EMBEDDABLE
            and primary.role != "converted"
            and "library" not in Path(primary.path).parts
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
            try:
                embed_metadata(Path(primary.path), book.metadata)
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
            book = state.store.load(sha256, library_file)

    return WriteBackResult(
        sidecar=book.model_dump(mode="json", by_alias=True),
        library_file=library_file,
        embedded=embedded,
        warnings=warnings,
    )


_RANGE = re.compile(r"^bytes=(\d*)-(\d*)$")
_CHUNK = 1 << 18  # 256 KiB


def _resolve_inside_root(root: Path, candidate: str) -> Path | None:
    """Never serve a path outside the library root, whatever the sidecar says.

    The sidecar is a user-editable JSON file - it can be hand-edited,
    restored from a backup, or corrupted to point anywhere. Path.resolve()
    on both sides collapses symlinks and '..' before the containment
    check, so a symlink under the root pointing outside it is caught too.
    """
    path = Path(candidate)
    if not path.is_absolute():
        path = root / path
    try:
        path = path.resolve()
        path.relative_to(root.resolve())
    except (OSError, ValueError):
        return None
    return path if path.is_file() else None


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
    state: AppState = Depends(get_state),
):
    with state.db.lock:
        row = state.db.conn.execute(
            "SELECT path FROM files WHERE sha256 = ? ORDER BY id LIMIT 1", (sha256,)
        ).fetchone()
    book = state.store.load(sha256, row["path"] if row else None)
    if book is None:
        raise HTTPException(404, "no such book")
    entry = (
        next((f for f in book.files if f.role == "original"), None)
        if original
        else book.primary_file()
    )
    if entry is None:
        raise HTTPException(404, "no file recorded for this book")
    path = _resolve_inside_root(Path(state.cfg.library.root), entry.path)
    if path is None:
        raise HTTPException(404, "file missing or outside the library root")

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
