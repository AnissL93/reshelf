"""Annotation operations over the sidecar.

Kept out of the web layer so the rules live in one place, and out of
`books.py`, which is already long enough.

Every mutation runs inside `SidecarStore.update()`'s callback. That is
not a style choice: the store holds a per-book lock for the duration of
the callback, so load-mutate-write is atomic. Reading the list in a
request handler, changing it there, and writing it back would reopen the
lost-update window the store exists to close.
"""

import uuid
from typing import Literal

from reshelf.store.models import Annotation, Book, Reading, now
from reshelf.store.sidecar import SidecarStore


class UnknownAnnotation(KeyError):
    """No annotation with that id on this book."""


def list_for(book: Book, file_sha: str | None = None) -> list[Annotation]:
    if file_sha is None:
        return list(book.annotations)
    return [a for a in book.annotations if a.file_sha == file_sha]


def add(
    store: SidecarStore,
    sha256: str,
    *,
    type: Literal["highlight", "bookmark"],
    file_sha: str,
    anchor: dict,
    color: str = "yellow",
    note: str = "",
    file_path: str | None = None,
) -> Annotation:
    """Append one annotation. The id is assigned here, never by a client."""
    ann = Annotation(
        id=uuid.uuid4().hex,
        type=type,
        file_sha=file_sha,
        anchor=anchor,
        color=color,
        note=note,
    )
    store.update(sha256, lambda book: book.annotations.append(ann), file_path)
    return ann


def patch(
    store: SidecarStore,
    sha256: str,
    ann_id: str,
    *,
    note: str | None = None,
    color: str | None = None,
    file_path: str | None = None,
) -> Annotation:
    """Change a note or a colour. An anchor is immutable - a mark in a
    different place is a different mark."""
    found: list[Annotation] = []

    def mutate(book: Book) -> None:
        for ann in book.annotations:
            if ann.id == ann_id:
                if note is not None:
                    ann.note = note
                if color is not None:
                    ann.color = color
                ann.updated_at = now()
                found.append(ann)
                return
        raise UnknownAnnotation(ann_id)

    store.update(sha256, mutate, file_path)
    return found[0]


def remove(
    store: SidecarStore,
    sha256: str,
    ann_id: str,
    file_path: str | None = None,
) -> None:
    def mutate(book: Book) -> None:
        before = len(book.annotations)
        book.annotations = [a for a in book.annotations if a.id != ann_id]
        if len(book.annotations) == before:
            raise UnknownAnnotation(ann_id)

    store.update(sha256, mutate, file_path)


def set_reading(
    store: SidecarStore,
    sha256: str,
    locator: str | None,
    percent: float,
    file_path: str | None = None,
) -> Reading:
    reading = Reading(locator=locator, percent=percent, updated_at=now())
    store.update(sha256, lambda book: setattr(book, "reading", reading), file_path)
    return reading
