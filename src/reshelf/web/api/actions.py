from fastapi import APIRouter, Depends, HTTPException

from reshelf import pipeline
from reshelf.convert import converters
from reshelf.web.api.books import get_book
from reshelf.web.deps import AppState, get_state
from reshelf.web.schemas import BookDetail, ChoosePayload, RematchPayload

router = APIRouter(tags=["actions"])


def _require_book(state: AppState, sha256: str):
    with state.db.lock:
        row = state.db.conn.execute(
            "SELECT * FROM files WHERE sha256 = ? ORDER BY id LIMIT 1", (sha256,)
        ).fetchone()
    if row is None:
        raise HTTPException(404, "no such book")
    return row


@router.post("/books/{sha256}/convert", status_code=202)
def convert(sha256: str, state: AppState = Depends(get_state)) -> dict:
    row = _require_book(state, sha256)
    book = state.store.load(sha256, row["path"])
    if book is None:
        # Hashed but never extracted: convert_book's store.update would
        # raise KeyError deep inside the worker, failing a job the user
        # cannot act on. Say so now instead.
        raise HTTPException(404, "no sidecar for this book yet; run extract first")
    # Mirrors convert_book's own source selection: the book's ORIGINAL file,
    # not whatever the primary file is now (a book already converted has an
    # EPUB primary, which must not make a second attempt look valid/invalid
    # for the wrong reason).
    source = next((f for f in book.files if f.role == "original"), None)
    fmt = (source.format if source else row["format"]) or ""
    if converters.target_format(fmt) is None:
        raise HTTPException(409, f"{fmt or 'this format'} needs no conversion")
    return {"job_id": state.runner.enqueue("convert", {"sha256": sha256})}


@router.post("/books/{sha256}/rematch", status_code=202)
def rematch(
    sha256: str, payload: RematchPayload, state: AppState = Depends(get_state)
) -> dict:
    _require_book(state, sha256)
    if payload.ai and not state.cfg.ai.enabled:
        raise HTTPException(409, "ai.provider is not configured")
    return {
        "job_id": state.runner.enqueue(
            "rematch",
            {"sha256": sha256, "query": payload.query, "ai": payload.ai},
        )
    }


@router.post("/books/{sha256}/choose", response_model=BookDetail)
def choose(
    sha256: str, payload: ChoosePayload, state: AppState = Depends(get_state)
) -> BookDetail:
    _require_book(state, sha256)
    try:
        pipeline.choose(state.cfg, state.db, state.store, sha256, payload.candidate_id)
    except KeyError as e:
        raise HTTPException(404, f"no such candidate: {payload.candidate_id}") from e
    return get_book(sha256, state)
