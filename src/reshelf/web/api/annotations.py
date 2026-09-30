"""Annotation and reading-progress endpoints.

All of it lands in the sidecar and none of it in SQLite: the user chose
no cross-book view, so there is nothing to index, no migration, and
`reshelf reindex` cannot lose a highlight.
"""

from fastapi import APIRouter, Depends, HTTPException, Response

from reshelf import annotations as anns
from reshelf.store.models import Annotation, Book, Reading
from reshelf.web.deps import AppState, get_state
from reshelf.web.schemas import AnnotationCreate, AnnotationPatch, ReadingUpdate

router = APIRouter(tags=["annotations"])


def _book_path(state: AppState, sha256: str) -> str | None:
    """The `files.path` a non-hash metadata layout needs to find the
    sidecar. None when there is no row - after a database rebuild `files`
    is empty (reindex rebuilds only book_index), and the hash layout finds
    the sidecar without it. Same fallback books.py uses everywhere."""
    with state.db.lock:
        row = state.db.conn.execute(
            "SELECT path FROM files WHERE sha256 = ? ORDER BY id LIMIT 1", (sha256,)
        ).fetchone()
    return row["path"] if row else None


def _require_sidecar(state: AppState, sha256: str) -> tuple[str | None, Book]:
    path = _book_path(state, sha256)
    book = state.store.load(sha256, path)
    if book is None:
        # Hashed but never extracted. store.update would raise KeyError
        # from under the handler; say what to do about it instead.
        raise HTTPException(404, "no such book, or no sidecar yet; run extract first")
    return path, book


@router.get("/books/{sha256}/annotations", response_model=list[Annotation])
def list_annotations(
    sha256: str, file_sha: str | None = None, state: AppState = Depends(get_state)
) -> list[Annotation]:
    _, book = _require_sidecar(state, sha256)
    return anns.list_for(book, file_sha)


@router.post("/books/{sha256}/annotations", response_model=Annotation, status_code=201)
def create_annotation(
    sha256: str, payload: AnnotationCreate, state: AppState = Depends(get_state)
) -> Annotation:
    path, _ = _require_sidecar(state, sha256)
    return anns.add(
        state.store,
        sha256,
        type=payload.type,
        file_sha=payload.file_sha,
        anchor=payload.anchor,
        color=payload.color,
        note=payload.note,
        file_path=path,
    )


@router.patch("/books/{sha256}/annotations/{ann_id}", response_model=Annotation)
def update_annotation(
    sha256: str,
    ann_id: str,
    payload: AnnotationPatch,
    state: AppState = Depends(get_state),
) -> Annotation:
    path, _ = _require_sidecar(state, sha256)
    try:
        return anns.patch(
            state.store, sha256, ann_id,
            note=payload.note, color=payload.color, file_path=path,
        )
    except anns.UnknownAnnotation as exc:
        raise HTTPException(404, f"no such annotation: {ann_id}") from exc


@router.delete("/books/{sha256}/annotations/{ann_id}", status_code=204)
def delete_annotation(
    sha256: str, ann_id: str, state: AppState = Depends(get_state)
) -> Response:
    path, _ = _require_sidecar(state, sha256)
    try:
        anns.remove(state.store, sha256, ann_id, file_path=path)
    except anns.UnknownAnnotation as exc:
        raise HTTPException(404, f"no such annotation: {ann_id}") from exc
    return Response(status_code=204)


@router.put("/books/{sha256}/reading", response_model=Reading)
def set_reading(
    sha256: str, payload: ReadingUpdate, state: AppState = Depends(get_state)
) -> Reading:
    path, _ = _require_sidecar(state, sha256)
    return anns.set_reading(
        state.store, sha256, payload.locator, payload.percent, file_path=path
    )


@router.post("/books/{sha256}/reading", response_model=Reading)
def set_reading_beacon(
    sha256: str, payload: ReadingUpdate, state: AppState = Depends(get_state)
) -> Reading:
    """navigator.sendBeacon can only POST, and the unload write is the one
    we least want to lose. Same body, same effect as the PUT."""
    return set_reading(sha256, payload, state)
