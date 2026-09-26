from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from reshelf.covers import cover_path
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
from reshelf.writeback import UnsupportedWriteBack, embed_metadata, rename_library_copy

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
