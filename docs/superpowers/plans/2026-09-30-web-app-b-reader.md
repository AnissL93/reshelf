# Sub-project B — Reader and Annotations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An in-browser reader at `/read/:sha` for PDF and EPUB with area
highlights, text-selection highlights, notes, bookmarks and reading
progress, all persisted into the JSON sidecar.

**Architecture:** Two rendering engines (`pdfjs-dist` from npm, foliate-js
vendored from upstream) hide behind one `Engine` interface, so the reader
shell — toolbar, annotation sidebar, persistence — never learns which
format it is showing. Annotations are appended to the `annotations` list
the sidecar already reserves, through `SidecarStore.update()`, which is
already atomic and per-book locked. Nothing touches SQLite.

**Tech Stack:** Python 3.11+, FastAPI, pydantic v2, pytest · React 19,
TypeScript (strict), Vite 8, react-router 7, pdfjs-dist 6.x, foliate-js
(vendored), vitest.

**Spec:** `docs/superpowers/specs/2026-09-30-web-app-b-reader.md`

## Global Constraints

Copied verbatim from the spec. Every task's requirements include these.

- **No Calibre.** No `ebook-convert`, no `calibredb`, anywhere.
- **No auth, single user, binds `127.0.0.1`.** Do not add a login, a
  token, a user id or a per-user scope to anything.
- **The sidecar is the source of truth.** SQLite is a derived,
  rebuildable index. This sub-project makes **no SQLite change, no
  migration, no index write** — deleting `db/books.sqlite3` and running
  `reshelf reindex` must leave every annotation intact.
- **Every sidecar write goes through `SidecarStore.update()`**, mutating
  inside the callback. Never read a list, modify it in the handler, and
  write the whole list back. The client never sends a whole annotation
  array.
- **Every sidecar model uses `model_config = _EXTRA`**
  (`ConfigDict(extra="allow", populate_by_name=True)`) so a sidecar
  written by a newer reshelf survives a rewrite by this one.
- **The backend never interprets an anchor.** `anchor` is stored and
  returned verbatim as a `dict`.
- **Anchor coordinates are normalized 0–1** against the page's own size.
  Never store CSS pixels.
- **`page` is 1-based.**
- **Highlight colors are exactly** `yellow`, `green`, `blue`, `pink`.
- **`file_sha = entry.sha256 or book.sha256`** — the one rule that binds
  an annotation to the file it was made on.
- **Reading progress writes at most once per 5 seconds**, plus once on
  unload via `navigator.sendBeacon`.
- **pdfjs-dist comes from npm; foliate-js is vendored from upstream**
  (`johnfactotum/foliate-js`) at a pinned commit. The `foliate-js` npm
  package is an unofficial partial republish — do not install it.
- **TypeScript strict stays on.** `npm run build` runs `tsc -b` and must
  pass with no `any` escape hatches added.
- Python: `.venv/bin/python -m pytest`. Web: `cd web && npm run build`,
  `npm run lint`, `npm test`.

## Review Focus

Five input classes the spec implies but that no task's own happy-path
tests would exercise. Each has a test added to the task that owns the
code, marked **[RF-n]**.

1. **A PDF whose pages differ in size.** Common in scans: a normalized
   rect must divide by *that page's* size, not page 1's, or highlights
   drift on every page after the first mismatch. → Task 5.
2. **A PDF page with `/Rotate 90`.** Also common in scans. A rect
   captured on a rotated page must round-trip to the same visual region,
   not a sideways one. → Task 5.
3. **A degenerate drag** — a click with no movement, or a drag up and to
   the left. Produces a zero-area or negative-size rect; saving it
   creates an invisible or `NaN` highlight the user cannot select or
   delete. → Task 5.
4. **A book that was hashed but never extracted**, so no sidecar file
   exists. `SidecarStore.update()` raises `KeyError` from inside the
   handler; the user must get a `404` explaining it, not a `500`. → Task 3.
5. **A sidecar whose `annotations` list was hand-edited** and holds a
   non-object, or an object missing `id`. Validating the list strictly
   would raise inside `Book.model_validate` and `500` the whole book
   detail page for a book that is otherwise fine. → Task 1.

---

## File Structure

**Backend — create:**
- `src/reshelf/annotations.py` — annotation operations over the sidecar.
  Kept out of `books.py`, which is already 329 lines and was flagged for
  splitting in A's review.
- `src/reshelf/web/api/annotations.py` — the HTTP router.
- `tests/test_annotations.py`, `tests/test_api_annotations.py`.

**Backend — modify:**
- `src/reshelf/store/models.py` — add `Annotation`; retype
  `Book.annotations`.
- `src/reshelf/web/schemas.py` — request/response models.
- `src/reshelf/web/api/books.py` — `readable` on `BookDetail`.
- `src/reshelf/web/app.py` — include the router.

**Frontend — create:**
- `web/vendor/foliate-js/` + `web/vendor/foliate-js/VENDOR.md`
- `web/src/reader/engines/types.ts` — `Anchor`, `Locator`, `Engine`.
- `web/src/reader/anchors.ts` — pure geometry. **The tested part.**
- `web/src/reader/anchors.test.ts`
- `web/src/reader/engines/pdf.ts`, `web/src/reader/engines/epub.ts`
- `web/src/reader/useAnnotations.ts`, `web/src/reader/Sidebar.tsx`,
  `web/src/reader/Reader.tsx`, `web/src/reader/ConvertToRead.tsx`
- `web/vitest.config.ts`

**Frontend — modify:**
- `web/src/api.ts`, `web/src/App.tsx`, `web/src/routes/BookDetail.tsx`,
  `web/package.json`

---

## Task 1: The `Annotation` model

**Files:**
- Modify: `src/reshelf/store/models.py`
- Test: `tests/test_annotations.py` (create)

**Interfaces:**
- Consumes: `_EXTRA`, `now()` — both already in `models.py`.
- Produces: `Annotation` with fields `id: str`, `type:
  Literal["highlight","bookmark"]`, `file_sha: str`, `color: str`,
  `note: str`, `anchor: dict`, `created_at: str`, `updated_at: str`.
  `Book.annotations: list[Annotation]`.

Note `anchor` is a plain `dict`: per the spec the backend never
interprets it, and typing it loosely is what makes an anchor kind from a
future version survive a round-trip for free.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_annotations.py`:

```python
import json

from reshelf.store.models import Annotation, Book

SHA = "a" * 64


def test_annotation_round_trips_through_json():
    ann = Annotation(
        id="abc123",
        type="highlight",
        file_sha=SHA,
        color="yellow",
        note="核心論點",
        anchor={"kind": "pdf-area", "page": 42, "rect": [0.1, 0.2, 0.4, 0.15]},
    )
    book = Book(sha256=SHA, annotations=[ann])
    reloaded = Book.model_validate(json.loads(book.model_dump_json(by_alias=True)))
    assert reloaded.annotations[0].note == "核心論點"
    assert reloaded.annotations[0].anchor == {
        "kind": "pdf-area", "page": 42, "rect": [0.1, 0.2, 0.4, 0.15],
    }


def test_an_unknown_anchor_kind_survives_a_round_trip():
    """A sidecar written by a future reshelf must not lose data here."""
    book = Book.model_validate({
        "sha256": SHA,
        "annotations": [{
            "id": "x", "type": "highlight", "file_sha": SHA,
            "anchor": {"kind": "djvu-region", "page": 3, "poly": [[0, 0], [1, 1]]},
        }],
    })
    dumped = json.loads(book.model_dump_json(by_alias=True))
    assert dumped["annotations"][0]["anchor"]["kind"] == "djvu-region"
    assert dumped["annotations"][0]["anchor"]["poly"] == [[0, 0], [1, 1]]


def test_unknown_annotation_fields_survive_a_round_trip():
    book = Book.model_validate({
        "sha256": SHA,
        "annotations": [{
            "id": "x", "type": "highlight", "file_sha": SHA,
            "anchor": {}, "tags": ["later-feature"],
        }],
    })
    dumped = json.loads(book.model_dump_json(by_alias=True))
    assert dumped["annotations"][0]["tags"] == ["later-feature"]


# [RF-5] A hand-edited sidecar must not 500 the whole book.
def test_a_malformed_annotation_entry_is_dropped_not_fatal(caplog):
    """`annotations` is user-editable JSON on disk. One bad entry must
    cost that entry, not the book - a 500 on the detail page for a book
    whose metadata is perfectly fine is the worse failure."""
    book = Book.model_validate({
        "sha256": SHA,
        "annotations": [
            "this is not an object",
            {"no_id": True},
            {"id": "good", "type": "highlight", "file_sha": SHA, "anchor": {}},
        ],
    })
    assert [a.id for a in book.annotations] == ["good"]
    assert "annotation" in caplog.text.lower()
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
.venv/bin/python -m pytest tests/test_annotations.py -v
```

Expected: FAIL — `ImportError: cannot import name 'Annotation'`.

- [ ] **Step 3: Implement**

In `src/reshelf/store/models.py`, add `logging` to the imports, a module
logger, and `field_validator` to the pydantic import:

```python
import logging
from pydantic import BaseModel, ConfigDict, Field, field_validator

logger = logging.getLogger(__name__)
```

Add the model above `class Book`:

```python
class Annotation(BaseModel):
    """One highlight or bookmark. `anchor` is opaque here by design:
    the backend stores and returns it verbatim, which is also what makes
    an anchor kind from a future version survive a rewrite."""

    model_config = _EXTRA

    id: str
    type: Literal["highlight", "bookmark"] = "highlight"
    file_sha: str
    color: str = "yellow"
    note: str = ""
    anchor: dict = Field(default_factory=dict)
    created_at: str = Field(default_factory=now)
    updated_at: str = Field(default_factory=now)
```

On `Book`, replace `annotations: list[dict] = Field(default_factory=list)`
with:

```python
    annotations: list[Annotation] = Field(default_factory=list)

    @field_validator("annotations", mode="before")
    @classmethod
    def _drop_malformed_annotations(cls, value: object) -> object:
        """Sidecars are hand-editable. A junk entry costs that entry, not
        the book: strict validation here would raise inside
        model_validate and 500 the detail page of a book whose metadata
        is fine."""
        if not isinstance(value, list):
            return value
        kept = []
        for item in value:
            if isinstance(item, Annotation):
                kept.append(item)
            elif isinstance(item, dict) and isinstance(item.get("id"), str):
                kept.append(item)
            else:
                logger.warning("dropping malformed annotation entry: %r", item)
        return kept
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
.venv/bin/python -m pytest tests/test_annotations.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Run the whole suite — nothing else may break**

```bash
.venv/bin/python -m pytest -q
```

Expected: the existing 367 pass, plus the 4 new.

- [ ] **Step 6: Commit**

```bash
git add src/reshelf/store/models.py tests/test_annotations.py
git commit -m "feat(store): typed Annotation model on the sidecar"
```

---

## Task 2: Annotation operations over the sidecar

**Files:**
- Create: `src/reshelf/annotations.py`
- Modify: `tests/test_annotations.py`

**Interfaces:**
- Consumes: `SidecarStore.update(sha256, mutate, file_path=None) -> Book`,
  `SidecarStore.load(sha256, file_path=None) -> Book | None`,
  `Annotation`, `Reading`, `now()`.
- Produces:
  - `list_for(book: Book, file_sha: str | None = None) -> list[Annotation]`
  - `add(store, sha256, *, type, file_sha, anchor, color="yellow", note="", file_path=None) -> Annotation`
  - `patch(store, sha256, ann_id, *, note=None, color=None, file_path=None) -> Annotation`
  - `remove(store, sha256, ann_id, file_path=None) -> None`
  - `set_reading(store, sha256, locator, percent, file_path=None) -> Reading`
  - `UnknownAnnotation(KeyError)`

Every mutation happens **inside** the `update()` callback, so two
concurrent writers cannot lose each other's work.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_annotations.py`:

```python
import threading

import pytest

from reshelf import annotations as anns
from reshelf.config import default_config, save_config
from reshelf.store.sidecar import SidecarStore

ANCHOR = {"kind": "pdf-area", "page": 1, "rect": [0.1, 0.1, 0.2, 0.2]}
OTHER_SHA = "b" * 64


@pytest.fixture
def store(tmp_path):
    cfg = default_config(tmp_path)
    for sub in ("metadata", "db"):
        (tmp_path / sub).mkdir(parents=True, exist_ok=True)
    save_config(cfg, tmp_path)
    s = SidecarStore(cfg)
    s.save(Book(sha256=SHA, files=[FileEntry(path=str(tmp_path / "x.pdf"), format="pdf")]))
    return s


def test_add_assigns_an_id_and_timestamps(store):
    ann = anns.add(store, SHA, type="highlight", file_sha=SHA, anchor=ANCHOR)
    assert len(ann.id) == 32
    assert ann.created_at and ann.updated_at
    assert store.load(SHA).annotations[0].id == ann.id


def test_add_then_patch_then_remove(store):
    ann = anns.add(store, SHA, type="highlight", file_sha=SHA, anchor=ANCHOR)
    patched = anns.patch(store, SHA, ann.id, note="hello", color="green")
    assert (patched.note, patched.color) == ("hello", "green")
    assert patched.anchor == ANCHOR
    anns.remove(store, SHA, ann.id)
    assert store.load(SHA).annotations == []


def test_patch_does_not_touch_created_at(store):
    ann = anns.add(store, SHA, type="highlight", file_sha=SHA, anchor=ANCHOR)
    patched = anns.patch(store, SHA, ann.id, note="x")
    assert patched.created_at == ann.created_at


def test_patch_and_remove_raise_on_an_unknown_id(store):
    with pytest.raises(anns.UnknownAnnotation):
        anns.patch(store, SHA, "nope", note="x")
    with pytest.raises(anns.UnknownAnnotation):
        anns.remove(store, SHA, "nope")


def test_list_for_filters_by_file_sha(store):
    a = anns.add(store, SHA, type="highlight", file_sha=SHA, anchor=ANCHOR)
    b = anns.add(store, SHA, type="highlight", file_sha=OTHER_SHA, anchor=ANCHOR)
    book = store.load(SHA)
    assert [x.id for x in anns.list_for(book, SHA)] == [a.id]
    assert [x.id for x in anns.list_for(book, OTHER_SHA)] == [b.id]
    assert len(anns.list_for(book)) == 2


def test_concurrent_adds_all_land(store):
    """Two tabs annotating one book. Mutating inside update()'s callback
    is what makes this safe; building a list in the caller and writing it
    back would lose one of these."""
    errors = []

    def go(i):
        try:
            anns.add(store, SHA, type="highlight", file_sha=SHA,
                     anchor={**ANCHOR, "page": i})
        except Exception as exc:  # noqa: BLE001 - reported below
            errors.append(exc)

    threads = [threading.Thread(target=go, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert len(store.load(SHA).annotations) == 20


def test_set_reading_persists(store):
    reading = anns.set_reading(store, SHA, "page=42", 0.37)
    assert reading.percent == 0.37
    assert store.load(SHA).reading.locator == "page=42"
```

Add `FileEntry` to the existing `from reshelf.store.models import ...` line
at the top of the file.

- [ ] **Step 2: Run the tests to verify they fail**

```bash
.venv/bin/python -m pytest tests/test_annotations.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'reshelf.annotations'`.

- [ ] **Step 3: Implement**

Create `src/reshelf/annotations.py`:

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
.venv/bin/python -m pytest tests/test_annotations.py -v
```

Expected: all pass, including `test_concurrent_adds_all_land`.

- [ ] **Step 5: Commit**

```bash
git add src/reshelf/annotations.py tests/test_annotations.py
git commit -m "feat(annotations): sidecar CRUD with per-book locking"
```

---

## Task 3: The annotation HTTP router

**Files:**
- Create: `src/reshelf/web/api/annotations.py`
- Modify: `src/reshelf/web/schemas.py`, `src/reshelf/web/app.py`
- Test: `tests/test_api_annotations.py` (create)

**Interfaces:**
- Consumes: Task 2's `reshelf.annotations`; `AppState` (`.cfg`, `.db`,
  `.store`, `.runner`) and `get_state` from `reshelf.web.deps`.
- Produces: routes `GET|POST /books/{sha256}/annotations`,
  `PATCH|DELETE /books/{sha256}/annotations/{ann_id}`,
  `PUT /books/{sha256}/reading`. Schemas `AnnotationCreate`,
  `AnnotationPatch`, `ReadingUpdate`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_api_annotations.py`:

```python
import pytest
from fastapi.testclient import TestClient

from reshelf.web.app import create_app
from tests.test_api_metadata import SHA, build

ANCHOR = {"kind": "pdf-area", "page": 1, "rect": [0.1, 0.1, 0.2, 0.2]}


@pytest.fixture
def client(tmp_path):
    build(tmp_path)
    with TestClient(create_app(tmp_path)) as c:
        yield c


def _post(client, **over):
    body = {"type": "highlight", "file_sha": SHA, "anchor": ANCHOR}
    body.update(over)
    return client.post(f"/api/books/{SHA}/annotations", json=body)


def test_post_creates_and_get_lists(client):
    r = _post(client, note="核心論點", color="green")
    assert r.status_code == 201
    created = r.json()
    assert created["note"] == "核心論點"
    assert created["color"] == "green"
    assert created["anchor"] == ANCHOR
    listed = client.get(f"/api/books/{SHA}/annotations").json()
    assert [a["id"] for a in listed] == [created["id"]]


def test_post_ignores_a_client_supplied_id(client):
    """Letting a client pick the id lets it overwrite someone else's."""
    created = _post(client, id="attacker-chosen").json()
    assert created["id"] != "attacker-chosen"


def test_patch_updates_note_and_colour(client):
    ann = _post(client).json()
    r = client.patch(
        f"/api/books/{SHA}/annotations/{ann['id']}",
        json={"note": "changed", "color": "blue"},
    )
    assert r.status_code == 200
    assert r.json()["note"] == "changed"
    assert r.json()["anchor"] == ANCHOR


def test_patch_refuses_to_move_an_anchor(client):
    ann = _post(client).json()
    r = client.patch(
        f"/api/books/{SHA}/annotations/{ann['id']}",
        json={"anchor": {"kind": "pdf-area", "page": 99, "rect": [0, 0, 1, 1]}},
    )
    assert r.status_code == 422


def test_patch_rejects_an_unknown_colour(client):
    ann = _post(client).json()
    r = client.patch(
        f"/api/books/{SHA}/annotations/{ann['id']}", json={"color": "chartreuse"}
    )
    assert r.status_code == 422


def test_delete_removes_it(client):
    ann = _post(client).json()
    assert client.delete(f"/api/books/{SHA}/annotations/{ann['id']}").status_code == 204
    assert client.get(f"/api/books/{SHA}/annotations").json() == []


def test_delete_of_an_unknown_id_is_404(client):
    """Not a silent success: the client's optimistic state is wrong and
    it needs to know so it can refetch."""
    assert client.delete(f"/api/books/{SHA}/annotations/nope").status_code == 404


def test_get_filters_by_file_sha(client):
    mine = _post(client).json()
    _post(client, file_sha="c" * 64)
    listed = client.get(
        f"/api/books/{SHA}/annotations", params={"file_sha": SHA}
    ).json()
    assert [a["id"] for a in listed] == [mine["id"]]


def test_put_reading_persists_and_shows_on_the_book(client):
    r = client.put(
        f"/api/books/{SHA}/reading", json={"locator": "page=42", "percent": 0.37}
    )
    assert r.status_code == 200
    detail = client.get(f"/api/books/{SHA}").json()
    assert detail["sidecar"]["reading"]["locator"] == "page=42"


def test_put_reading_rejects_a_percent_out_of_range(client):
    r = client.put(
        f"/api/books/{SHA}/reading", json={"locator": "page=1", "percent": 42}
    )
    assert r.status_code == 422


def test_annotations_for_an_unknown_book_are_404(client):
    unknown = "f" * 64
    assert client.get(f"/api/books/{unknown}/annotations").status_code == 404
    assert client.post(
        f"/api/books/{unknown}/annotations",
        json={"type": "highlight", "file_sha": unknown, "anchor": ANCHOR},
    ).status_code == 404


# [RF-4] Hashed but never extracted: there is no sidecar file to update.
def test_posting_to_a_book_with_no_sidecar_is_404_not_500(tmp_path):
    """`SidecarStore.update` raises KeyError when the file is absent.
    Uncaught, that is a 500 for a state the user can actually fix
    (`extract`), so it must be a 404 that says so - the same treatment
    actions.convert already gives this case."""
    build(tmp_path)
    from reshelf.config import load_config
    from reshelf.store.sidecar import SidecarStore

    SidecarStore(load_config(tmp_path)).delete(SHA)
    with TestClient(create_app(tmp_path)) as c:
        r = c.post(
            f"/api/books/{SHA}/annotations",
            json={"type": "highlight", "file_sha": SHA, "anchor": ANCHOR},
        )
    assert r.status_code == 404
    assert "extract" in r.json()["detail"]
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
.venv/bin/python -m pytest tests/test_api_annotations.py -v
```

Expected: FAIL — every request 404s, no router mounted.

- [ ] **Step 3: Add the schemas**

In `src/reshelf/web/schemas.py`, add to the imports:

```python
from typing import Any, Literal

from pydantic import BaseModel, Field
```

and append:

```python
class AnnotationCreate(BaseModel):
    """`id`, `created_at` and `updated_at` are assigned server-side. They
    are absent from this model on purpose: a client that sends one gets
    it ignored by FastAPI rather than honoured."""

    type: Literal["highlight", "bookmark"] = "highlight"
    file_sha: str
    anchor: dict[str, Any]
    color: Literal["yellow", "green", "blue", "pink"] = "yellow"
    note: str = ""


class AnnotationPatch(BaseModel):
    """`extra="forbid"` is what turns an attempt to move an anchor into a
    422 instead of a silently ignored field."""

    model_config = {"extra": "forbid"}

    note: str | None = None
    color: Literal["yellow", "green", "blue", "pink"] | None = None


class ReadingUpdate(BaseModel):
    locator: str | None = None
    percent: float = Field(0.0, ge=0.0, le=1.0)
```

- [ ] **Step 4: Write the router**

Create `src/reshelf/web/api/annotations.py`:

```python
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


def _book_path(state: AppState, sha256: str) -> str:
    """The `files.path` a non-hash metadata layout needs to find the
    sidecar. Mirrors books.py: the row is the only place that mapping
    lives, and `store.load`/`store.update` need it for layouts other
    than `hash`."""
    with state.db.lock:
        row = state.db.conn.execute(
            "SELECT path FROM files WHERE sha256 = ? ORDER BY id LIMIT 1", (sha256,)
        ).fetchone()
    if row is None:
        raise HTTPException(404, "no such book")
    return row["path"]


def _require_sidecar(state: AppState, sha256: str) -> tuple[str, Book]:
    path = _book_path(state, sha256)
    book = state.store.load(sha256, path)
    if book is None:
        # Hashed but never extracted. store.update would raise KeyError
        # from under the handler; say what to do about it instead.
        raise HTTPException(404, "no sidecar for this book yet; run extract first")
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
```

- [ ] **Step 5: Mount the router**

In `src/reshelf/web/app.py`, add `annotations` to the
`from reshelf.web.api import ...` line and include it **before** the SPA
mount, alongside the others:

```python
    app.include_router(actions.router, prefix="/api")
    app.include_router(annotations.router, prefix="/api")
    app.include_router(jobs.router, prefix="/api")
```

The SPA is mounted last at `/` so it never shadows `/api`; do not move it.

- [ ] **Step 6: Run the tests to verify they pass**

```bash
.venv/bin/python -m pytest tests/test_api_annotations.py -v
```

Expected: 12 passed.

- [ ] **Step 7: Run the whole suite**

```bash
.venv/bin/python -m pytest -q
```

- [ ] **Step 8: Commit**

```bash
git add src/reshelf/web/api/annotations.py src/reshelf/web/schemas.py \
        src/reshelf/web/app.py tests/test_api_annotations.py
git commit -m "feat(api): annotation and reading-progress endpoints"
```

---

## Task 4: `readable` files on the book detail response

**Files:**
- Modify: `src/reshelf/web/api/books.py`, `src/reshelf/web/schemas.py`
- Test: `tests/test_api_books.py`

**Interfaces:**
- Consumes: `converters.target_format(fmt) -> str | None` from
  `reshelf.convert.converters`; `BookDetail` schema.
- Produces: `ReadableFile` schema with `file_sha: str`, `path: str`,
  `format: str`, `role: str`, `engine: Literal["pdf","epub"] | None`,
  `convert_to: str | None`; `BookDetail.readable: list[ReadableFile]`.
  The reader and the detail page both consume this.

This exists so the `file_sha = entry.sha256 or book.sha256` rule and the
format→engine table live on the server once, not duplicated in
TypeScript. It is the same reason `primary_format` is already resolved
server-side.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_api_books.py`:

```python
def test_readable_marks_a_pdf_for_the_pdf_engine(tmp_path):
    build(tmp_path, fmt="pdf")
    with TestClient(create_app(tmp_path)) as c:
        readable = c.get(f"/api/books/{SHA}").json()["readable"]
    assert len(readable) == 1
    assert readable[0]["engine"] == "pdf"
    assert readable[0]["convert_to"] is None
    assert readable[0]["file_sha"] == SHA


def test_readable_offers_a_conversion_for_a_mobi(tmp_path):
    build(tmp_path, fmt="mobi")
    with TestClient(create_app(tmp_path)) as c:
        readable = c.get(f"/api/books/{SHA}").json()["readable"]
    assert readable[0]["engine"] is None
    assert readable[0]["convert_to"] == "epub"


def test_readable_offers_pdf_for_a_djvu(tmp_path):
    build(tmp_path, fmt="djvu")
    with TestClient(create_app(tmp_path)) as c:
        readable = c.get(f"/api/books/{SHA}").json()["readable"]
    assert readable[0]["convert_to"] == "pdf"


def test_a_derived_file_gets_its_own_file_sha(tmp_path):
    """The rule that keeps a PDF's page 42 from being confused with its
    converted EPUB's CFI: originals carry the book's hash, derived files
    carry their own."""
    build(tmp_path)
    from reshelf.config import load_config
    from reshelf.store.models import FileEntry
    from reshelf.store.sidecar import SidecarStore

    derived = tmp_path / "derived" / "converted.epub"
    derived.parent.mkdir(parents=True, exist_ok=True)
    make_epub(derived, "T", "A")
    store = SidecarStore(load_config(tmp_path))
    store.update(SHA, lambda b: b.files.append(FileEntry(
        path=str(derived), format="epub", role="converted", sha256="d" * 64,
    )))
    with TestClient(create_app(tmp_path)) as c:
        readable = c.get(f"/api/books/{SHA}").json()["readable"]
    by_sha = {r["file_sha"]: r for r in readable}
    assert set(by_sha) == {SHA, "d" * 64}
    assert by_sha["d" * 64]["engine"] == "epub"
    assert by_sha["d" * 64]["role"] == "converted"
```

Ensure `make_epub` is imported in that file (`from tests.helpers import
make_epub`); add it if absent.

- [ ] **Step 2: Run the tests to verify they fail**

```bash
.venv/bin/python -m pytest tests/test_api_books.py -k readable -v
```

Expected: FAIL — `KeyError: 'readable'`.

- [ ] **Step 3: Add the schema**

In `src/reshelf/web/schemas.py`:

```python
class ReadableFile(BaseModel):
    """One file of a book, described for the reader.

    `file_sha` is what an annotation binds to, resolved here rather than
    in the SPA: `FileEntry.sha256` is None on originals, where the book's
    own hash is the original's, and getting that fallback wrong would
    attach a PDF's highlights to its converted EPUB."""

    file_sha: str
    path: str
    format: str
    role: str
    engine: Literal["pdf", "epub"] | None = None
    convert_to: str | None = None
```

and on `BookDetail` add:

```python
    readable: list[ReadableFile] = []
```

- [ ] **Step 4: Populate it**

In `src/reshelf/web/api/books.py`, import the converters and the schema:

```python
from reshelf.convert import converters
from reshelf.store.models import Book
from reshelf.web.schemas import ReadableFile
```

`Book` may already be imported in this module - do not add it twice.

Add near the top of the module:

```python
# The two formats with a browser engine. Everything else is read through
# its converted form (see ReadableFile.convert_to).
ENGINES: dict[str, str] = {"pdf": "pdf", "epub": "epub"}


def _readable(book: Book) -> list[ReadableFile]:
    out = []
    for entry in book.files:
        fmt = (entry.format or "").lower()
        engine = ENGINES.get(fmt)
        out.append(ReadableFile(
            file_sha=entry.sha256 or book.sha256,
            path=entry.path,
            format=fmt,
            role=entry.role,
            engine=engine,
            convert_to=None if engine else converters.target_format(fmt),
        ))
    return out
```

In `get_book`, pass `readable=_readable(book)` to the `BookDetail(...)`
construction.

- [ ] **Step 5: Run the tests to verify they pass**

```bash
.venv/bin/python -m pytest tests/test_api_books.py -v
```

- [ ] **Step 6: Commit**

```bash
git add src/reshelf/web/api/books.py src/reshelf/web/schemas.py tests/test_api_books.py
git commit -m "feat(api): describe each book file for the reader"
```

---

## Task 5: vitest, and the anchor geometry

**Files:**
- Create: `web/vitest.config.ts`, `web/src/reader/engines/types.ts`,
  `web/src/reader/anchors.ts`, `web/src/reader/anchors.test.ts`
- Modify: `web/package.json`

**Interfaces:**
- Produces:
  - `type Anchor` — the discriminated union.
  - `type Locator = { locator: string; percent: number }`
  - `interface Engine` — consumed by Tasks 6, 7, 10.
  - `normalizeRect(px: PixelRect, page: PageBox): NormRect | null`
  - `denormalizeRect(rect: NormRect, page: PageBox): PixelRect`
  - `mergeRects(rects: NormRect[]): NormRect[]`
  - `type PageBox = { width: number; height: number; rotation: 0|90|180|270 }`
  - `type NormRect = [number, number, number, number]`

This is the only JavaScript with real arithmetic in it, and the only
JavaScript with tests. A deviation from sub-project A's "no JS test
framework", agreed during design: an off-by-one here shows up as
highlights that silently drift, which is exactly what a test catches and
review does not.

- [ ] **Step 1: Install vitest**

```bash
cd web && npm install --save-dev vitest@^4
```

- [ ] **Step 2: Configure it**

Create `web/vitest.config.ts`:

```ts
import { defineConfig } from "vitest/config";

// Kept out of vite.config.ts so the SPA build config stays a build config.
// No DOM environment: the only tested module is pure arithmetic, and
// pulling in jsdom to test it would be testing jsdom.
export default defineConfig({
  test: { environment: "node", include: ["src/**/*.test.ts"] },
});
```

Add to `web/package.json` scripts:

```json
"test": "vitest run"
```

`vitest.config.ts` sits outside `src/`, so add it to
`web/tsconfig.node.json`'s include list beside `vite.config.ts` -
otherwise `tsc -b` never looks at it:

```json
  "include": ["vite.config.ts", "vitest.config.ts"]
```

- [ ] **Step 3: Write the types and the colour table**

Create `web/src/reader/colors.ts`:

```ts
// The one colour table. The engines need a translucent fill to paint
// with and the UI needs a solid chip to click; keeping both here stops
// "blue" meaning one thing in the overlay and another in the sidebar.
import type { AnnotationColor } from "./engines/types";

export const COLORS: AnnotationColor[] = ["yellow", "green", "blue", "pink"];

/** Painted over the page, so translucent. */
export const FILL: Record<AnnotationColor, string> = {
  yellow: "rgba(255, 214, 0, 0.35)",
  green: "rgba(0, 200, 83, 0.30)",
  blue: "rgba(41, 121, 255, 0.28)",
  pink: "rgba(255, 64, 129, 0.28)",
};

/** Shown as a chip in the toolbar and sidebar, so solid. */
export const SWATCH: Record<AnnotationColor, string> = {
  yellow: "#ffd600",
  green: "#00c853",
  blue: "#2979ff",
  pink: "#ff4081",
};
```

Create `web/src/reader/engines/types.ts`:

```ts
// The reader shell talks to this and never to pdf.js or foliate-js
// directly. Both engines implement it; the shell stays format-blind.

/** Width and height in the PDF's own units, plus the page's /Rotate.
 * Rotation matters: a scanned page is often stored sideways with
 * /Rotate 90, so the box the user drew and the box the page stores are
 * in different frames. */
export type PageBox = {
  width: number;
  height: number;
  rotation: 0 | 90 | 180 | 270;
};

/** [x, y, width, height], each 0..1 of the page, origin top-left of the
 * page as displayed. Normalized so a highlight survives zoom, resize and
 * device pixel ratio. */
export type NormRect = [number, number, number, number];

export type PixelRect = { x: number; y: number; width: number; height: number };

export type Anchor =
  | { kind: "pdf-text"; page: number; rects: NormRect[]; text: string }
  | { kind: "pdf-area"; page: number; rect: NormRect }
  | { kind: "pdf-page"; page: number }
  | { kind: "epub"; cfi: string; text?: string };

export type Locator = { locator: string; percent: number };

export type AnnotationColor = "yellow" | "green" | "blue" | "pink";

/** The one definition. `api.ts` re-exports it rather than declaring a
 * second one - two spellings of the same record is how a colour becomes
 * a bare string on one side and a union on the other. */
export type Annotation = {
  id: string;
  type: "highlight" | "bookmark";
  file_sha: string;
  color: AnnotationColor;
  note: string;
  /** Opaque to the server, which stores and returns it verbatim. That is
   * what lets an anchor kind this build does not know survive a write. */
  anchor: Anchor | { kind: string; [k: string]: unknown };
  created_at: string;
  updated_at: string;
};

export interface Engine {
  /** False for EPUB: reflowable text has no stable page geometry to box,
   * so the shell hides the area tool rather than offering a dead control. */
  readonly supportsArea: boolean;
  mount(host: HTMLElement, fileUrl: string): Promise<void>;
  goTo(anchor: Anchor): Promise<void>;
  locate(): Locator;
  paint(annotations: Annotation[]): void;
  onTextSelect(cb: (anchor: Anchor, text: string) => void): void;
  onAreaSelect(cb: (anchor: Anchor) => void): void;
  destroy(): void;
}
```

- [ ] **Step 4: Write the failing tests**

Create `web/src/reader/anchors.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { denormalizeRect, mergeRects, normalizeRect } from "./anchors";
import type { NormRect, PageBox } from "./engines/types";

const A4: PageBox = { width: 600, height: 800, rotation: 0 };

describe("normalizeRect", () => {
  it("divides by the page's own size", () => {
    expect(normalizeRect({ x: 60, y: 80, width: 120, height: 160 }, A4))
      .toEqual([0.1, 0.1, 0.2, 0.2]);
  });

  it("round-trips through denormalizeRect", () => {
    const px = { x: 60, y: 80, width: 120, height: 160 };
    const back = denormalizeRect(normalizeRect(px, A4)!, A4);
    expect(back).toEqual(px);
  });

  // [RF-1] Scanned PDFs frequently mix page sizes within one file.
  it("uses the given page's size, so a different page scales differently", () => {
    const wide: PageBox = { width: 1200, height: 800, rotation: 0 };
    const px = { x: 60, y: 80, width: 120, height: 160 };
    expect(normalizeRect(px, A4)).not.toEqual(normalizeRect(px, wide));
    expect(normalizeRect(px, wide)).toEqual([0.05, 0.1, 0.1, 0.2]);
  });

  // [RF-2] Scans are often stored sideways.
  // [RF-2] The round-trip below is symmetric and would pass even with the
  // axes swapped the wrong way, so assert the swap itself first.
  it("measures a 90-degree page against its swapped axes", () => {
    const sideways: PageBox = { width: 600, height: 800, rotation: 90 };
    // Displayed, a /Rotate 90 page is 800 wide and 600 tall.
    expect(normalizeRect({ x: 400, y: 300, width: 80, height: 60 }, sideways))
      .toEqual([0.5, 0.5, 0.1, 0.1]);
  });

  it("round-trips on a rotated page", () => {
    for (const rotation of [90, 180, 270] as const) {
      const page: PageBox = { width: 600, height: 800, rotation };
      const px = { x: 60, y: 80, width: 120, height: 160 };
      const back = denormalizeRect(normalizeRect(px, page)!, page);
      expect(back.x).toBeCloseTo(px.x, 6);
      expect(back.y).toBeCloseTo(px.y, 6);
      expect(back.width).toBeCloseTo(px.width, 6);
      expect(back.height).toBeCloseTo(px.height, 6);
    }
  });

  // [RF-3] A click with no drag, or a drag up and to the left.
  it("returns null for a zero-area rect", () => {
    expect(normalizeRect({ x: 10, y: 10, width: 0, height: 50 }, A4)).toBeNull();
    expect(normalizeRect({ x: 10, y: 10, width: 50, height: 0 }, A4)).toBeNull();
  });

  it("normalizes a backwards drag instead of storing a negative size", () => {
    expect(normalizeRect({ x: 180, y: 240, width: -120, height: -160 }, A4))
      .toEqual([0.1, 0.1, 0.2, 0.2]);
  });

  it("clamps a drag that leaves the page", () => {
    const r = normalizeRect({ x: -60, y: -80, width: 1200, height: 1600 }, A4)!;
    expect(r).toEqual([0, 0, 1, 1]);
  });
});

describe("mergeRects", () => {
  it("joins rects that share a line into one box", () => {
    expect(mergeRects([
      [0.1, 0.2, 0.3, 0.02],
      [0.4, 0.2, 0.2, 0.02],
    ])).toEqual([[0.1, 0.2, 0.5, 0.02]]);
  });

  it("keeps rects on different lines apart", () => {
    const rects: [number, number, number, number][] = [
      [0.1, 0.2, 0.3, 0.02],
      [0.1, 0.5, 0.3, 0.02],
    ];
    expect(mergeRects(rects)).toHaveLength(2);
  });

  it("returns an empty array unchanged", () => {
    expect(mergeRects([])).toEqual([]);
  });

  // The engines call paint() repeatedly with the same annotation objects.
  // Merging in place would rewrite the stored anchor a little more on
  // every repaint until the highlight no longer matches what was saved.
  it("does not mutate the rects it was given", () => {
    const rects: NormRect[] = [
      [0.1, 0.2, 0.3, 0.02],
      [0.4, 0.2, 0.2, 0.02],
    ];
    const snapshot = structuredClone(rects);
    mergeRects(rects);
    expect(rects).toEqual(snapshot);
  });
});
```

- [ ] **Step 5: Run the tests to verify they fail**

```bash
cd web && npm test
```

Expected: FAIL — cannot resolve `./anchors`.

- [ ] **Step 6: Implement**

Create `web/src/reader/anchors.ts`:

```ts
// Pure geometry for anchors. No DOM, no engine imports - this is the
// module that gets tested, so everything that can live here does.
import type { NormRect, PageBox, PixelRect } from "./engines/types";

const clamp01 = (n: number) => Math.min(1, Math.max(0, n));

/** Rotation swaps the axes a /Rotate 90 or 270 page is measured on. */
function displaySize(page: PageBox): { w: number; h: number } {
  return page.rotation === 90 || page.rotation === 270
    ? { w: page.height, h: page.width }
    : { w: page.width, h: page.height };
}

/** A drawn box in display pixels -> 0..1 of the page. Null when the drag
 * has no area: a click with no movement must not become an invisible
 * highlight that cannot be selected or deleted. */
export function normalizeRect(px: PixelRect, page: PageBox): NormRect | null {
  // A backwards drag arrives as a negative width or height. Normalize it
  // rather than storing a negative size no renderer can draw.
  const x0 = Math.min(px.x, px.x + px.width);
  const y0 = Math.min(px.y, px.y + px.height);
  const x1 = Math.max(px.x, px.x + px.width);
  const y1 = Math.max(px.y, px.y + px.height);

  const { w, h } = displaySize(page);
  if (w <= 0 || h <= 0) return null;

  const nx0 = clamp01(x0 / w);
  const ny0 = clamp01(y0 / h);
  const nx1 = clamp01(x1 / w);
  const ny1 = clamp01(y1 / h);

  const width = nx1 - nx0;
  const height = ny1 - ny0;
  if (width <= 0 || height <= 0) return null;
  return [nx0, ny0, width, height];
}

export function denormalizeRect(rect: NormRect, page: PageBox): PixelRect {
  const { w, h } = displaySize(page);
  return {
    x: rect[0] * w,
    y: rect[1] * h,
    width: rect[2] * w,
    height: rect[3] * h,
  };
}

/** Selection rects arrive one per text run. Joining the ones that share a
 * line keeps a sentence from being drawn as a row of separate boxes. */
export function mergeRects(rects: NormRect[]): NormRect[] {
  if (rects.length === 0) return [];
  // Copy each tuple, not just the outer array: `[...rects]` shares the
  // tuples with the caller, and the merge below writes through them.
  const sorted = rects
    .map((r) => [...r] as NormRect)
    .sort((a, b) => a[1] - b[1] || a[0] - b[0]);
  const out: NormRect[] = [sorted[0]];
  for (const rect of sorted.slice(1)) {
    const last = out[out.length - 1];
    const sameLine = Math.abs(rect[1] - last[1]) < last[3] / 2;
    if (sameLine) {
      const right = Math.max(last[0] + last[2], rect[0] + rect[2]);
      last[2] = right - last[0];
      last[3] = Math.max(last[3], rect[3]);
    } else {
      out.push(rect);
    }
  }
  return out;
}
```

- [ ] **Step 7: Run the tests to verify they pass**

```bash
cd web && npm test
```

Expected: 10 passed.

- [ ] **Step 8: Verify the build still typechecks**

```bash
cd web && npm run build && npm run lint
```

- [ ] **Step 9: Commit**

```bash
git add web/package.json web/package-lock.json web/vitest.config.ts \
        web/src/reader/engines/types.ts web/src/reader/anchors.ts \
        web/src/reader/anchors.test.ts
git commit -m "feat(reader): anchor geometry, with vitest for the arithmetic"
```

---

## Task 6: The PDF engine

**Files:**
- Create: `web/src/reader/engines/pdf.ts`
- Modify: `web/package.json`

**Interfaces:**
- Consumes: `Engine`, `Anchor`, `Annotation`, `PageBox`, `Locator` from
  `./types`; `normalizeRect`, `denormalizeRect`, `mergeRects` from
  `../anchors`.
- Produces: `class PdfEngine implements Engine`, plus
  `setAreaMode(on: boolean)` and `setScale(scale: number)`, both called
  by the shell (Task 10). Colours come from `../colors`, not from here.

- [ ] **Step 1: Install pdfjs-dist**

```bash
cd web && npm install pdfjs-dist@^6
```

- [ ] **Step 2: Implement the engine**

Create `web/src/reader/engines/pdf.ts`:

```ts
// pdf.js behind the Engine interface. Scrolling canvas, one canvas per
// page, an absolutely-positioned overlay per page for highlights, and
// pdf.js's own text layer on top where the PDF has one.
//
// 64% of this library's PDFs are scans with no text layer, so the area
// tool - not text selection - is the interaction that has to work.
import * as pdfjs from "pdfjs-dist";
import type { PDFDocumentProxy, PDFPageProxy } from "pdfjs-dist";
import { TextLayer } from "pdfjs-dist";
import { denormalizeRect, mergeRects, normalizeRect } from "../anchors";
import { FILL } from "../colors";
import type {
  Anchor, Annotation, Engine, Locator, NormRect, PageBox,
} from "./types";

pdfjs.GlobalWorkerOptions.workerSrc = new URL(
  "pdfjs-dist/build/pdf.worker.min.mjs",
  import.meta.url,
).toString();

type PageView = {
  num: number;
  box: PageBox;
  scale: number;
  wrapper: HTMLDivElement;
  overlay: HTMLDivElement;
};

export class PdfEngine implements Engine {
  readonly supportsArea = true;

  private doc: PDFDocumentProxy | null = null;
  private host: HTMLElement | null = null;
  private views = new Map<number, PageView>();
  private annotations: Annotation[] = [];
  private current = 1;
  private scale: number;
  private areaMode = false;
  private onText: ((a: Anchor, t: string) => void) | null = null;
  private onArea: ((a: Anchor) => void) | null = null;
  private cleanup: (() => void)[] = [];

  /** The shell owns the zoom level, so it is passed in rather than
   * defaulted here - otherwise the first zoom click jumps from the
   * engine's private default to the shell's. */
  constructor(scale = 1.4) {
    this.scale = scale;
  }

  async mount(host: HTMLElement, fileUrl: string): Promise<void> {
    this.host = host;
    host.classList.add("pdf-host");
    // Range requests are the point of A's file endpoint: a 200MB scan
    // must not be downloaded whole before the first page shows.
    this.doc = await pdfjs.getDocument({
      url: fileUrl,
      rangeChunkSize: 1 << 18,
    }).promise;

    for (let num = 1; num <= this.doc.numPages; num++) {
      await this.renderPage(num);
    }
    this.watchScroll();
    this.watchSelection();
  }

  private async renderPage(num: number): Promise<void> {
    const page: PDFPageProxy = await this.doc!.getPage(num);
    const viewport = page.getViewport({ scale: this.scale });

    const wrapper = document.createElement("div");
    wrapper.className = "pdf-page";
    wrapper.dataset.page = String(num);
    wrapper.style.position = "relative";
    wrapper.style.width = `${viewport.width}px`;
    wrapper.style.height = `${viewport.height}px`;

    const canvas = document.createElement("canvas");
    canvas.width = Math.floor(viewport.width * devicePixelRatio);
    canvas.height = Math.floor(viewport.height * devicePixelRatio);
    canvas.style.width = `${viewport.width}px`;
    canvas.style.height = `${viewport.height}px`;
    wrapper.append(canvas);

    const overlay = document.createElement("div");
    overlay.className = "pdf-overlay";
    Object.assign(overlay.style, {
      position: "absolute", inset: "0", pointerEvents: "none",
    });
    wrapper.append(overlay);

    this.host!.append(wrapper);

    const ctx = canvas.getContext("2d")!;
    ctx.scale(devicePixelRatio, devicePixelRatio);
    await page.render({ canvasContext: ctx, viewport, canvas }).promise;

    // A scanned page has no text content; TextLayer then renders nothing
    // and selection simply never fires. That is the intended behaviour,
    // not a case to special-case.
    const textDiv = document.createElement("div");
    textDiv.className = "textLayer";
    textDiv.style.position = "absolute";
    textDiv.style.inset = "0";
    // setProperty, not Object.assign: assigning a custom property onto
    // the style object writes an ordinary JS property that never reaches
    // CSS, leaving pdf.js's spans laid out at scale 1 over a canvas
    // rendered at `this.scale` - every selection off by the zoom factor.
    textDiv.style.setProperty("--scale-factor", String(this.scale));
    wrapper.append(textDiv);
    const textContent = await page.getTextContent();
    if (textContent.items.length) {
      await new TextLayer({ textContentSource: textContent, container: textDiv, viewport })
        .render();
    }

    // The rotation the page declares, which is what anchors normalize
    // against - not the viewport's post-rotation size.
    const raw = page.getViewport({ scale: 1, rotation: 0 });
    this.views.set(num, {
      num,
      box: {
        width: raw.width,
        height: raw.height,
        rotation: ((page.rotate % 360) + 360) % 360 as PageBox["rotation"],
      },
      scale: this.scale,
      wrapper,
      overlay,
    });

    this.attachAreaTool(this.views.get(num)!);
  }

  // -- area highlights: the primary interaction on a scanned PDF --------

  private attachAreaTool(view: PageView): void {
    let start: { x: number; y: number } | null = null;
    let ghost: HTMLDivElement | null = null;

    const rectOf = (e: PointerEvent) => {
      const r = view.wrapper.getBoundingClientRect();
      return { x: e.clientX - r.left, y: e.clientY - r.top };
    };

    const down = (e: PointerEvent) => {
      if (!this.areaMode || e.button !== 0) return;
      e.preventDefault();
      start = rectOf(e);
      ghost = document.createElement("div");
      Object.assign(ghost.style, {
        position: "absolute", border: "2px dashed #2979ff",
        background: "rgba(41,121,255,0.12)", pointerEvents: "none",
      });
      view.wrapper.append(ghost);
      view.wrapper.setPointerCapture(e.pointerId);
    };

    const move = (e: PointerEvent) => {
      if (!start || !ghost) return;
      const now = rectOf(e);
      Object.assign(ghost.style, {
        left: `${Math.min(start.x, now.x)}px`,
        top: `${Math.min(start.y, now.y)}px`,
        width: `${Math.abs(now.x - start.x)}px`,
        height: `${Math.abs(now.y - start.y)}px`,
      });
    };

    const up = (e: PointerEvent) => {
      if (!start) return;
      const end = rectOf(e);
      ghost?.remove();
      ghost = null;
      const px = {
        x: start.x / view.scale,
        y: start.y / view.scale,
        width: (end.x - start.x) / view.scale,
        height: (end.y - start.y) / view.scale,
      };
      start = null;
      // normalizeRect returns null for a click with no drag. Dropping it
      // here is what stops a stray click becoming an invisible highlight.
      const rect = normalizeRect(px, view.box);
      if (rect) this.onArea?.({ kind: "pdf-area", page: view.num, rect });
    };

    view.wrapper.addEventListener("pointerdown", down);
    view.wrapper.addEventListener("pointermove", move);
    view.wrapper.addEventListener("pointerup", up);
    this.cleanup.push(() => {
      view.wrapper.removeEventListener("pointerdown", down);
      view.wrapper.removeEventListener("pointermove", move);
      view.wrapper.removeEventListener("pointerup", up);
    });
  }

  setAreaMode(on: boolean): void {
    this.areaMode = on;
    this.host?.classList.toggle("area-mode", on);
  }

  // -- text selection: only fires where the PDF has a text layer --------

  private watchSelection(): void {
    const handler = () => {
      const sel = document.getSelection();
      if (!sel || sel.isCollapsed || !this.host) return;
      const text = sel.toString().trim();
      if (!text) return;
      const wrapper = (sel.anchorNode?.parentElement)?.closest<HTMLElement>(".pdf-page");
      if (!wrapper) return;
      const view = this.views.get(Number(wrapper.dataset.page));
      if (!view) return;

      const base = wrapper.getBoundingClientRect();
      const rects: NormRect[] = [];
      for (const r of Array.from(sel.getRangeAt(0).getClientRects())) {
        const norm = normalizeRect({
          x: (r.left - base.left) / view.scale,
          y: (r.top - base.top) / view.scale,
          width: r.width / view.scale,
          height: r.height / view.scale,
        }, view.box);
        if (norm) rects.push(norm);
      }
      if (!rects.length) return;
      this.onText?.(
        { kind: "pdf-text", page: view.num, rects: mergeRects(rects), text },
        text,
      );
    };
    document.addEventListener("selectionchange", handler);
    this.cleanup.push(() => document.removeEventListener("selectionchange", handler));
  }

  // -- painting ---------------------------------------------------------

  paint(annotations: Annotation[]): void {
    this.annotations = annotations;
    for (const view of this.views.values()) view.overlay.replaceChildren();
    for (const ann of annotations) {
      const anchor = ann.anchor as Anchor;
      if (anchor.kind === "pdf-area") {
        this.drawRect(anchor.page, anchor.rect, ann);
      } else if (anchor.kind === "pdf-text") {
        for (const rect of anchor.rects) this.drawRect(anchor.page, rect, ann);
      }
      // Any other kind - including one from a future version - is simply
      // not drawn here. The sidebar still lists it.
    }
  }

  private drawRect(page: number, rect: NormRect, ann: Annotation): void {
    const view = this.views.get(page);
    if (!view) return;
    const px = denormalizeRect(rect, view.box);
    const el = document.createElement("div");
    el.dataset.annotation = ann.id;
    Object.assign(el.style, {
      position: "absolute",
      left: `${px.x * view.scale}px`,
      top: `${px.y * view.scale}px`,
      width: `${px.width * view.scale}px`,
      height: `${px.height * view.scale}px`,
      background: FILL[ann.color] ?? FILL.yellow,
      borderRadius: "2px",
    });
    view.overlay.append(el);
  }

  // -- navigation and position ------------------------------------------

  async goTo(anchor: Anchor): Promise<void> {
    const page = "page" in anchor ? anchor.page : 1;
    this.views.get(page)?.wrapper.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  locate(): Locator {
    const total = this.doc?.numPages ?? 1;
    return { locator: `page=${this.current}`, percent: this.current / total };
  }

  private watchScroll(): void {
    const observer = new IntersectionObserver((entries) => {
      for (const entry of entries) {
        if (entry.isIntersecting) {
          this.current = Number((entry.target as HTMLElement).dataset.page);
        }
      }
    }, { threshold: 0.5 });
    for (const view of this.views.values()) observer.observe(view.wrapper);
    this.cleanup.push(() => observer.disconnect());
  }

  async setScale(scale: number): Promise<void> {
    this.scale = scale;
    this.host!.replaceChildren();
    this.views.clear();
    for (let num = 1; num <= (this.doc?.numPages ?? 0); num++) {
      await this.renderPage(num);
    }
    this.paint(this.annotations);
  }

  onTextSelect(cb: (a: Anchor, t: string) => void): void {
    this.onText = cb;
  }

  onAreaSelect(cb: (a: Anchor) => void): void {
    this.onArea = cb;
  }

  destroy(): void {
    for (const fn of this.cleanup) fn();
    this.cleanup = [];
    this.views.clear();
    void this.doc?.destroy();
    this.doc = null;
    this.host?.replaceChildren();
  }
}
```

- [ ] **Step 3: Verify it typechecks and lints**

```bash
cd web && npm run build && npm run lint
```

Expected: clean. If `TextLayer` is not exported from the `pdfjs-dist`
root in the installed 6.x, import it from `pdfjs-dist/build/pdf.mjs` and
say so in a comment.

- [ ] **Step 4: Run the existing anchor tests — unchanged**

```bash
cd web && npm test
```

- [ ] **Step 5: Commit**

```bash
git add web/package.json web/package-lock.json web/src/reader/engines/pdf.ts
git commit -m "feat(reader): pdf.js engine with area and text highlights"
```

---

## Task 7: Vendor foliate-js and write the EPUB engine

**Files:**
- Create: `web/vendor/foliate-js/**`, `web/vendor/foliate-js/VENDOR.md`,
  `web/src/reader/engines/epub.ts`
- Modify: `web/package.json` (zip reader), `web/.gitignore` if it would
  exclude `vendor/`

**Interfaces:**
- Consumes: `Engine`, `Anchor`, `Annotation`, `Locator` from `./types`;
  `COLORS` from `./pdf`.
- Produces: `class EpubEngine implements Engine` with
  `supportsArea = false`, plus `setFontSize(px: number)` - the EPUB half
  of the spec's toolbar sizing control (the PDF half is `PdfEngine.setScale`).

**Why vendored:** the `foliate-js` npm package is published by a third
party, not the author, has one version from April 2025, and ships only a
subset of the modules (`epub.js`, `epubcfi.js`, `overlayer.js`,
`paginator.js`, `view.js` — no zip reader, no `text-walker.js`).
Upstream is MIT and designed to be dropped in as plain ESM.

- [ ] **Step 1: Vendor upstream at a pinned commit**

```bash
cd /tmp && rm -rf foliate-src && git clone --depth 1 \
  https://github.com/johnfactotum/foliate-js foliate-src
cd foliate-src && git rev-parse HEAD   # record this
cd /home/hy/Projects/book-org
mkdir -p web/vendor/foliate-js
cp -r /tmp/foliate-src/*.js /tmp/foliate-src/vendor web/vendor/foliate-js/
rm -rf web/vendor/foliate-js/vendor/.git
```

Write `web/vendor/foliate-js/VENDOR.md`:

```markdown
# foliate-js (vendored)

Upstream: https://github.com/johnfactotum/foliate-js
Licence: MIT (see LICENSE in this directory)
Pinned commit: <paste the sha from `git rev-parse HEAD` above>
Vendored on: 2026-09-30

## Why vendored rather than installed

The `foliate-js` package on npm is not published by the author. It is a
third-party republish, one version, untouched since 2025-04, and it
ships only a subset of the modules — no zip reader, so its `epub.js`
cannot actually open a file. Upstream is plain ESM with no build step
and is meant to be vendored.

## Updating

Re-clone upstream, copy `*.js` and `vendor/` over this directory, update
the pinned commit above, then run the reader smoke checklist in
`docs/superpowers/plans/2026-09-30-web-app-b-reader.md` (Task 12).

## Local modifications

None. Keep it that way — anything reshelf-specific belongs in
`web/src/reader/engines/epub.ts`.
```

Copy upstream's `LICENSE` into the directory too.

- [ ] **Step 2: Confirm the module graph loads under Vite before building on it**

This is the one step in the plan whose outcome is not certain in advance:
foliate-js is ESM with relative imports and its own bundled zip reader,
and Vite must resolve the whole graph. Find out now, not in Task 10.

Create a scratch entry `web/src/reader/engines/_probe.ts`:

```ts
import "../../../vendor/foliate-js/view.js";
export const probe = () => customElements.get("foliate-view") !== undefined;
```

Import it from `web/src/main.tsx` temporarily, run `npm run build`, and
confirm the build succeeds and emits the foliate modules.

**If the build fails to resolve the graph:** stop and report it rather
than working around it silently. The fallback is `npm install epubjs@0.3`
and an `EpubEngine` written against `rendition.annotations`, which costs
a stale dependency but not the feature. Do not take the fallback without
saying so.

Delete `_probe.ts` and its import once the build is green.

- [ ] **Step 3: Install the zip reader**

foliate-js's EPUB loader takes a zip-like interface; upstream's own demo
wires `@zip.js/zip.js`.

```bash
cd web && npm install @zip.js/zip.js
```

- [ ] **Step 4: Implement the engine**

Create `web/src/reader/engines/epub.ts`:

```ts
// foliate-js behind the Engine interface.
//
// EPUB is reflowable: there is no page geometry to draw a box on, so
// supportsArea is false and the shell hides the area tool. Anchors are
// CFIs, which foliate-js generates from a Range and resolves back to one.
import { BlobReader, BlobWriter, TextWriter, ZipReader } from "@zip.js/zip.js";
import { FILL } from "../colors";
import type { Anchor, Annotation, Engine, Locator } from "./types";

// @ts-expect-error - vendored plain ESM, no type declarations upstream.
import { EPUB } from "../../../vendor/foliate-js/epub.js";
// @ts-expect-error - registers the <foliate-view> custom element.
import "../../../vendor/foliate-js/view.js";
// @ts-expect-error - vendored plain ESM.
import { Overlayer } from "../../../vendor/foliate-js/overlayer.js";

/** foliate-js's EPUB loader wants entry lookup by name, not a zip object.
 * Confirm the exact shape against upstream's own demo during Step 2's
 * probe - this is the part of the file most likely to need adjusting. */
async function zipLoader(blob: Blob) {
  const reader = new ZipReader(new BlobReader(blob));
  const entries = new Map(
    (await reader.getEntries()).map((e) => [e.filename, e]),
  );
  return {
    entries: [...entries.values()],
    loadText: async (name: string) =>
      entries.get(name)?.getData?.(new TextWriter()),
    loadBlob: async (name: string) =>
      entries.get(name)?.getData?.(new BlobWriter()),
    getSize: (name: string) => entries.get(name)?.uncompressedSize ?? 0,
  };
}

type FoliateView = HTMLElement & {
  open(book: unknown): Promise<void>;
  goTo(target: string): Promise<void>;
  addAnnotation(a: { value: string }): void;
  deleteAnnotation(a: { value: string }): void;
  renderer: {
    getContents(): { doc: Document }[];
    setStyles?(css: string): void;
  };
  addEventListener(t: string, cb: (e: CustomEvent) => void): void;
};

export class EpubEngine implements Engine {
  // Reflowable text has no stable page geometry, so there is nothing to
  // box. The shell hides the area tool rather than offering a dead one.
  readonly supportsArea = false;

  private view: FoliateView | null = null;
  private annotations: Annotation[] = [];
  private position: Locator = { locator: "", percent: 0 };
  private onText: ((a: Anchor, t: string) => void) | null = null;

  async mount(host: HTMLElement, fileUrl: string): Promise<void> {
    const blob = await (await fetch(fileUrl)).blob();
    const book = await new EPUB(await zipLoader(blob)).init();

    const view = document.createElement("foliate-view") as FoliateView;
    host.append(view);
    await view.open(book);
    this.view = view;

    view.addEventListener("relocate", (e) => {
      const detail = e.detail as { cfi: string; fraction: number };
      this.position = { locator: detail.cfi, percent: detail.fraction ?? 0 };
    });

    // foliate-js asks us how to draw each annotation it is showing.
    view.addEventListener("draw-annotation", (e) => {
      const detail = e.detail as {
        draw: (fn: unknown, opts: unknown) => void;
        annotation: { value: string };
      };
      const ann = this.annotations.find((a) => this.cfiOf(a) === detail.annotation.value);
      detail.draw(Overlayer.highlight, {
        color: FILL[ann?.color ?? "yellow"],
      });
    });

    view.addEventListener("load", () => this.watchSelection());
    this.watchSelection();
  }

  private cfiOf(ann: Annotation): string | null {
    const anchor = ann.anchor as Anchor;
    return anchor.kind === "epub" ? anchor.cfi : null;
  }

  private watchSelection(): void {
    for (const { doc } of this.view?.renderer.getContents() ?? []) {
      doc.addEventListener("selectionchange", () => {
        const sel = doc.getSelection();
        if (!sel || sel.isCollapsed) return;
        const text = sel.toString().trim();
        if (!text) return;
        // @ts-expect-error - foliate-js augments the view with this.
        const cfi = this.view?.getCFI?.(doc, sel.getRangeAt(0));
        if (!cfi) return;
        this.onText?.({ kind: "epub", cfi, text }, text);
      });
    }
  }

  paint(annotations: Annotation[]): void {
    for (const ann of this.annotations) {
      const cfi = this.cfiOf(ann);
      if (cfi) this.view?.deleteAnnotation({ value: cfi });
    }
    this.annotations = annotations;
    for (const ann of annotations) {
      const cfi = this.cfiOf(ann);
      // A pdf-* anchor on an EPUB file, or a kind from a future version,
      // is not drawable here. The sidebar still lists it.
      if (cfi) this.view?.addAnnotation({ value: cfi });
    }
  }

  async goTo(anchor: Anchor): Promise<void> {
    if (anchor.kind === "epub") await this.view?.goTo(anchor.cfi);
  }

  /** Reflowable text resizes rather than zooming, so this is the EPUB
   * counterpart to PdfEngine.setScale. foliate-js re-paginates and
   * re-emits `relocate`, so the stored CFI stays valid across a change. */
  setFontSize(px: number): void {
    this.view?.renderer.setStyles?.(`
      html, body { font-size: ${px}px; }
    `);
  }

  locate(): Locator {
    return this.position;
  }

  onTextSelect(cb: (a: Anchor, t: string) => void): void {
    this.onText = cb;
  }

  onAreaSelect(): void {
    // Never fires. Part of the interface so the shell stays format-blind.
  }

  destroy(): void {
    this.view?.remove();
    this.view = null;
  }
}
```

- [ ] **Step 5: Verify it typechecks and lints**

```bash
cd web && npm run build && npm run lint
```

- [ ] **Step 6: Commit**

```bash
git add web/vendor web/package.json web/package-lock.json \
        web/src/reader/engines/epub.ts
git commit -m "feat(reader): vendored foliate-js EPUB engine"
```

---

## Task 8: API client and the annotations hook

**Files:**
- Modify: `web/src/api.ts`
- Create: `web/src/reader/useAnnotations.ts`

**Interfaces:**
- Consumes: `req`, `ApiError` from `../api`; `Annotation`, `Anchor` from
  `./engines/types`.
- Produces from `api.ts`: `ReadableFile` type, `Annotation` type re-export,
  `listAnnotations`, `createAnnotation`, `patchAnnotation`,
  `deleteAnnotation`, `putReading`, `beaconReading`.
  From `useAnnotations.ts`: `useAnnotations(sha, fileSha)` returning
  `{ annotations, create, update, remove, error, retry }`.

- [ ] **Step 1: Extend `web/src/api.ts`**

Add to the `Sidecar`/`BookDetail` section:

```ts
// reshelf.web.schemas.ReadableFile - one file of a book, described for
// the reader. `file_sha` is resolved server-side because the fallback
// rule (originals carry the book's hash) must not be duplicated here.
export type ReadableFile = {
  file_sha: string;
  path: string;
  format: string;
  role: string;
  engine: "pdf" | "epub" | null;
  convert_to: string | null;
};
```

and add `readable: ReadableFile[];` to the `BookDetail` type.

Add a new section before the transport helpers:

```ts
// -- annotations (annotations.py) ----------------------------------------

// Defined once, in the reader's types module, and re-exported here so
// every caller sees the same record. A second Annotation shape on the
// transport side is how `color` ends up a bare string in one file and a
// union in another.
export type { Annotation, AnnotationColor } from "./reader/engines/types";
```

and after the actions section:

```ts
export const listAnnotations = (sha256: string, fileSha?: string) =>
  req<Annotation[]>(
    `/books/${sha256}/annotations${fileSha ? `?file_sha=${fileSha}` : ""}`,
  );

/** `id`, `created_at` and `updated_at` are assigned by the server; the
 * parameter type has no slot for them so a caller cannot try. */
export const createAnnotation = (
  sha256: string,
  body: {
    type: "highlight" | "bookmark";
    file_sha: string;
    anchor: object;
    color?: AnnotationColor;
    note?: string;
  },
) =>
  req<Annotation>(`/books/${sha256}/annotations`, {
    method: "POST",
    body: JSON.stringify(body),
  });

/** Note and colour only. The server 422s anything else - an anchor is
 * immutable, because a mark in a different place is a different mark. */
export const patchAnnotation = (
  sha256: string,
  id: string,
  body: { note?: string; color?: AnnotationColor },
) =>
  req<Annotation>(`/books/${sha256}/annotations/${id}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });

export const deleteAnnotation = (sha256: string, id: string) =>
  req<void>(`/books/${sha256}/annotations/${id}`, { method: "DELETE" });

export const putReading = (
  sha256: string,
  body: { locator: string | null; percent: number },
) =>
  req<Reading>(`/books/${sha256}/reading`, {
    method: "PUT",
    body: JSON.stringify(body),
  });

/** The unload write. `fetch` is cancelled when the page goes away;
 * sendBeacon is the only thing the browser guarantees to deliver. */
export function beaconReading(
  sha256: string,
  body: { locator: string | null; percent: number },
): void {
  navigator.sendBeacon(
    `/api/books/${sha256}/reading`,
    new Blob([JSON.stringify(body)], { type: "application/json" }),
  );
}
```

`sendBeacon` issues a POST, and the route is a PUT. Add a POST alias in
`src/reshelf/web/api/annotations.py` so the unload write is not silently
dropped:

```python
@router.post("/books/{sha256}/reading", response_model=Reading)
def set_reading_beacon(
    sha256: str, payload: ReadingUpdate, state: AppState = Depends(get_state)
) -> Reading:
    """navigator.sendBeacon can only POST, and the unload write is the one
    we least want to lose. Same body, same effect as the PUT."""
    return set_reading(sha256, payload, state)
```

Add a test for it in `tests/test_api_annotations.py`:

```python
def test_reading_also_accepts_a_post_for_sendbeacon(client):
    r = client.post(
        f"/api/books/{SHA}/reading", json={"locator": "page=9", "percent": 0.1}
    )
    assert r.status_code == 200
    assert client.get(f"/api/books/{SHA}").json()["sidecar"]["reading"]["locator"] == "page=9"
```

- [ ] **Step 2: Write the hook**

Create `web/src/reader/useAnnotations.ts`:

```ts
// Annotation state for one file of one book.
//
// Writes are optimistic, and a failed write does NOT roll the mark back
// off the screen. Losing a highlight the user just drew is worse than
// showing one that is not yet saved: the mark stays, an error surfaces,
// and `retry` re-sends.
import { useCallback, useEffect, useState } from "react";
import {
  createAnnotation, deleteAnnotation, listAnnotations, patchAnnotation,
} from "../api";
import type { Annotation, AnnotationColor } from "../api";

type Pending = { annotation: Annotation; send: () => Promise<Annotation> };

export default function useAnnotations(sha: string, fileSha: string | null) {
  const [annotations, setAnnotations] = useState<Annotation[]>([]);
  const [pending, setPending] = useState<Pending[]>([]);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(() => {
    if (!fileSha) return;
    listAnnotations(sha, fileSha).then(setAnnotations).catch(
      (e: Error) => setError(e.message),
    );
  }, [sha, fileSha]);

  useEffect(reload, [reload]);

  const create = useCallback(
    (anchor: object, type: "highlight" | "bookmark", color: AnnotationColor, note = "") => {
      if (!fileSha) return;
      const optimistic: Annotation = {
        id: `pending-${crypto.randomUUID()}`,
        type, file_sha: fileSha, color, note,
        anchor: anchor as Annotation["anchor"],
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      };
      setAnnotations((prev) => [...prev, optimistic]);
      const send = () =>
        createAnnotation(sha, { type, file_sha: fileSha, anchor, color, note });
      send()
        .then((saved) =>
          setAnnotations((prev) => prev.map((a) => (a.id === optimistic.id ? saved : a))),
        )
        .catch((e: Error) => {
          // The mark stays on screen. Deliberately.
          setError(e.message);
          setPending((p) => [...p, { annotation: optimistic, send }]);
        });
    },
    [sha, fileSha],
  );

  const update = useCallback(
    (id: string, body: { note?: string; color?: AnnotationColor }) => {
      setAnnotations((prev) => prev.map((a) => (a.id === id ? { ...a, ...body } : a)));
      patchAnnotation(sha, id, body)
        .then((saved) => setAnnotations((p) => p.map((a) => (a.id === id ? saved : a))))
        .catch((e: Error) => setError(e.message));
    },
    [sha],
  );

  const remove = useCallback(
    (id: string) => {
      setAnnotations((prev) => prev.filter((a) => a.id !== id));
      deleteAnnotation(sha, id).catch((e: Error) => {
        // A 404 means our state was stale, so refetch rather than guess.
        setError(e.message);
        reload();
      });
    },
    [sha, reload],
  );

  const retry = useCallback(() => {
    const queued = pending;
    setPending([]);
    setError(null);
    for (const item of queued) {
      item.send()
        .then((saved) =>
          setAnnotations((prev) =>
            prev.map((a) => (a.id === item.annotation.id ? saved : a)),
          ),
        )
        .catch((e: Error) => {
          setError(e.message);
          setPending((p) => [...p, item]);
        });
    }
  }, [pending]);

  return {
    annotations, create, update, remove, reload,
    error, unsaved: pending.length, retry,
  };
}
```

- [ ] **Step 3: Run the Python test for the beacon alias**

```bash
.venv/bin/python -m pytest tests/test_api_annotations.py -v
```

- [ ] **Step 4: Typecheck and lint**

```bash
cd web && npm run build && npm run lint
```

- [ ] **Step 5: Commit**

```bash
git add web/src/api.ts web/src/reader/useAnnotations.ts \
        src/reshelf/web/api/annotations.py tests/test_api_annotations.py
git commit -m "feat(reader): annotation client and optimistic state"
```

---

## Task 9: The annotation sidebar

**Files:**
- Create: `web/src/reader/Sidebar.tsx`

**Interfaces:**
- Consumes: `Annotation`, `AnnotationColor` from `../api`.
- Produces: `default function Sidebar(props)` where props are
  `{ annotations, onJump, onUpdate, onRemove, unsaved, error, onRetry }`.

- [ ] **Step 1: Implement**

Create `web/src/reader/Sidebar.tsx`:

```tsx
// The active file's annotations, in reading order. Clicking one jumps to
// it; the note is edited in place.
import { useState } from "react";
import { COLORS, SWATCH } from "./colors";
import type { Annotation, AnnotationColor } from "../api";

function where(ann: Annotation): string {
  const anchor = ann.anchor as { kind: string; page?: number };
  if (typeof anchor.page === "number") return `p.${anchor.page}`;
  if (anchor.kind === "epub") return "location";
  // A kind this build does not know. Listed, not hidden - it is the
  // user's data and the next version may understand it (spec section 7).
  return "unsupported in this version";
}

function quoted(ann: Annotation): string | null {
  const text = (ann.anchor as { text?: unknown }).text;
  return typeof text === "string" && text ? text : null;
}

export default function Sidebar({
  annotations, onJump, onUpdate, onRemove, unsaved, error, onRetry,
}: {
  annotations: Annotation[];
  onJump: (a: Annotation) => void;
  onUpdate: (id: string, body: { note?: string; color?: AnnotationColor }) => void;
  onRemove: (id: string) => void;
  unsaved: number;
  error: string | null;
  onRetry: () => void;
}) {
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState("");

  return (
    <aside className="reader-sidebar">
      {error && (
        <div className="banner error">
          <span>{unsaved > 0 ? `${unsaved} unsaved` : "Error"}: {error}</span>
          <button onClick={onRetry}>Retry</button>
        </div>
      )}
      {annotations.length === 0 && (
        <p className="empty">No highlights yet. Select text, or use the area
          tool to draw a box on the page.</p>
      )}
      <ul className="annotations">
        {annotations.map((ann) => (
          <li key={ann.id} className={`annotation ${ann.type}`}>
            <button className="jump" onClick={() => onJump(ann)}>
              <span
                className="swatch"
                style={{ background: ann.type === "bookmark" ? "none" : SWATCH[ann.color] }}
              >
                {ann.type === "bookmark" ? "⚑" : ""}
              </span>
              <span className="where" title={(ann.anchor as { kind: string }).kind}>
                {where(ann)}
              </span>
              {quoted(ann) && <q className="quote">{quoted(ann)}</q>}
            </button>

            {editing === ann.id ? (
              <div className="note-edit">
                <textarea
                  value={draft}
                  autoFocus
                  onChange={(e) => setDraft(e.target.value)}
                />
                <button
                  onClick={() => {
                    onUpdate(ann.id, { note: draft });
                    setEditing(null);
                  }}
                >
                  Save
                </button>
                <button onClick={() => setEditing(null)}>Cancel</button>
              </div>
            ) : (
              <button
                className="note"
                onClick={() => {
                  setEditing(ann.id);
                  setDraft(ann.note);
                }}
              >
                {ann.note || <em>Add a note</em>}
              </button>
            )}

            <div className="annotation-actions">
              {ann.type === "highlight" &&
                COLORS.map((c) => (
                  <button
                    key={c}
                    aria-label={`Colour ${c}`}
                    className={`swatch-button${ann.color === c ? " on" : ""}`}
                    style={{ background: SWATCH[c] }}
                    onClick={() => onUpdate(ann.id, { color: c })}
                  />
                ))}
              <button
                className="delete"
                onClick={() => {
                  if (confirm("Delete this annotation?")) onRemove(ann.id);
                }}
              >
                Delete
              </button>
            </div>
          </li>
        ))}
      </ul>
    </aside>
  );
}
```

- [ ] **Step 2: Typecheck and lint**

```bash
cd web && npm run build && npm run lint
```

- [ ] **Step 3: Commit**

```bash
git add web/src/reader/Sidebar.tsx
git commit -m "feat(reader): annotation sidebar"
```

---

## Task 10: The reader shell and route

**Files:**
- Create: `web/src/reader/Reader.tsx`, `web/src/reader/ConvertToRead.tsx`
- Modify: `web/src/App.tsx`, `web/src/styles.css` (or the project's
  existing stylesheet)

**Interfaces:**
- Consumes: `PdfEngine`, `EpubEngine`, `Engine`, `useAnnotations`,
  `Sidebar`, `getBook`, `fileUrl`, `putReading`, `beaconReading`,
  `convertBook`, `useJob`.
- Produces: route `/read/:sha`.

- [ ] **Step 1: The convert-first card**

Create `web/src/reader/ConvertToRead.tsx`:

```tsx
// A format with no browser engine but a converter: MOBI, AZW3 and TXT
// become EPUB; DJVU becomes PDF. Reuses sub-project A's per-book convert
// job rather than adding a second conversion path.
import { convertBook } from "../api";
import type { ReadableFile } from "../api";
import useJob from "../hooks/useJob";
import { useEffect, useState } from "react";

export default function ConvertToRead({
  sha, file, onDone,
}: {
  sha: string;
  file: ReadableFile;
  onDone: () => void;
}) {
  const [jobId, setJobId] = useState<number | null>(null);
  const { job, running } = useJob(jobId);

  // In an effect, not in the render body: calling the parent's setState
  // while rendering re-renders it mid-render and loops.
  useEffect(() => {
    if (job?.status === "done") onDone();
  }, [job?.status, onDone]);

  if (!file.convert_to) {
    return (
      <div className="reader-notice">
        <p>{file.format.toUpperCase()} cannot be read in the browser.</p>
        <a href={`/api/books/${sha}/file?path=${encodeURIComponent(file.path)}`}>
          Download it
        </a>
      </div>
    );
  }

  return (
    <div className="reader-notice">
      <p>This is a {file.format.toUpperCase()} file.</p>
      <button
        disabled={running}
        onClick={() => convertBook(sha).then((r) => setJobId(r.job_id))}
      >
        {running
          ? (job?.message ?? "Converting…")
          : `Convert to ${file.convert_to.toUpperCase()} & read`}
      </button>
      {job?.status === "failed" && <p className="error">{job.error}</p>}
    </div>
  );
}
```

- [ ] **Step 2: The shell**

Create `web/src/reader/Reader.tsx`:

```tsx
// The reader shell. Owns the toolbar, the annotation sidebar and
// persistence; knows nothing about pdf.js or foliate-js beyond picking
// which one to construct.
import { useCallback, useEffect, useRef, useState } from "react";
import { useParams, useSearchParams, Link } from "react-router-dom";
import { beaconReading, fileUrl, getBook, putReading } from "../api";
import type { AnnotationColor, BookDetail, ReadableFile } from "../api";
import { COLORS, SWATCH } from "./colors";
import ConvertToRead from "./ConvertToRead";
import Sidebar from "./Sidebar";
import useAnnotations from "./useAnnotations";
import { EpubEngine } from "./engines/epub";
import { PdfEngine } from "./engines/pdf";
import type { Anchor, Engine } from "./engines/types";

/** Progress is written at most this often. Not an optimization: an
 * unthrottled writer rewrites a multi-kilobyte sidecar on every scroll
 * tick. */
const PROGRESS_MS = 5000;

function pick(book: BookDetail, want: string | null): ReadableFile | null {
  if (want) return book.readable.find((f) => f.file_sha === want) ?? null;
  // Prefer a file we can actually render, then fall back to the first so
  // the convert-first card has something to describe.
  return book.readable.find((f) => f.engine) ?? book.readable[0] ?? null;
}

export default function Reader() {
  const { sha = "" } = useParams();
  const [params, setParams] = useSearchParams();
  const [book, setBook] = useState<BookDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [color, setColor] = useState<AnnotationColor>("yellow");
  const [areaMode, setAreaMode] = useState(false);
  // One control, two meanings: a PDF zooms, reflowable EPUB text resizes.
  const [zoom, setZoom] = useState(1.4);
  const [fontSize, setFontSize] = useState(16);

  const hostRef = useRef<HTMLDivElement>(null);
  const engineRef = useRef<Engine | null>(null);
  const lastWrite = useRef(0);

  const file = book ? pick(book, params.get("file_sha")) : null;
  const {
    annotations, create, update, remove, error: annError, unsaved, retry,
  } = useAnnotations(sha, file?.file_sha ?? null);

  const reload = useCallback(
    () => getBook(sha).then(setBook).catch((e: Error) => setError(e.message)),
    [sha],
  );
  useEffect(() => { void reload(); }, [reload]);

  // -- mount the engine -------------------------------------------------
  useEffect(() => {
    const host = hostRef.current;
    if (!host || !file?.engine || !book) return;
    const engine: Engine = file.engine === "pdf" ? new PdfEngine(zoom) : new EpubEngine();
    engineRef.current = engine;

    engine.onTextSelect((anchor) => create(anchor, "highlight", color));
    engine.onAreaSelect((anchor) => create(anchor, "highlight", color));

    engine
      .mount(host, fileUrl(sha, file.path))
      .then(() => {
        const stored = book.sidecar.reading?.locator;
        if (stored) {
          void engine.goTo(
            file.engine === "pdf"
              ? { kind: "pdf-page", page: Number(stored.replace("page=", "")) || 1 }
              : { kind: "epub", cfi: stored },
          );
        }
      })
      .catch((e: Error) => setError(e.message));

    return () => {
      engine.destroy();
      engineRef.current = null;
    };
    // Deliberately narrow: this effect loads the file, so it must re-run
    // only when the file changes. `create` and `color` are rebound by the
    // effect below instead - listing them here would re-download the book
    // every time the user picked a different highlight colour.
  }, [sha, file?.file_sha, file?.engine, book?.sha256]);

  // The selection callbacks close over `color` from mount time, so rebind
  // them whenever the colour changes instead of remounting the engine.
  useEffect(() => {
    const engine = engineRef.current;
    if (!engine) return;
    engine.onTextSelect((anchor) => create(anchor, "highlight", color));
    engine.onAreaSelect((anchor) => create(anchor, "highlight", color));
  }, [color, create]);

  useEffect(() => {
    engineRef.current?.paint(annotations);
  }, [annotations]);

  useEffect(() => {
    if (engineRef.current instanceof PdfEngine) {
      engineRef.current.setAreaMode(areaMode);
    }
  }, [areaMode]);

  useEffect(() => {
    const engine = engineRef.current;
    if (engine instanceof PdfEngine) {
      // Re-renders every page, so never on the first mount - the engine
      // already rendered at this scale.
      void engine.setScale(zoom).then(() => engine.paint(annotations));
    } else if (engine instanceof EpubEngine) {
      engine.setFontSize(fontSize);
    }
    // Deliberately excludes `annotations`: the effect above already
    // repaints when they change, and re-running this one would re-render
    // every page of the PDF each time a note was edited.
  }, [zoom, fontSize]);

  // -- reading progress -------------------------------------------------
  useEffect(() => {
    if (!file?.engine) return;
    const tick = () => {
      const engine = engineRef.current;
      if (!engine) return;
      const now = Date.now();
      if (now - lastWrite.current < PROGRESS_MS) return;
      lastWrite.current = now;
      void putReading(sha, engine.locate()).catch(() => {
        // Progress is not worth an error banner; the next tick retries.
      });
    };
    const id = window.setInterval(tick, PROGRESS_MS);
    const onLeave = () => {
      const engine = engineRef.current;
      // fetch() is cancelled on unload; sendBeacon is the only delivery
      // the browser guarantees.
      if (engine) beaconReading(sha, engine.locate());
    };
    window.addEventListener("pagehide", onLeave);
    return () => {
      window.clearInterval(id);
      window.removeEventListener("pagehide", onLeave);
      onLeave();
    };
  }, [sha, file?.engine]);

  if (error) {
    return (
      <div className="reader-notice error">
        <p>{error}</p>
        <a href={fileUrl(sha, file?.path)}>Download instead</a>
      </div>
    );
  }
  if (!book || !file) return <p>Loading…</p>;

  const title = book.sidecar.metadata.title ?? sha.slice(0, 12);

  return (
    <div className="reader">
      <header className="reader-toolbar">
        <Link to={`/book/${sha}`}>&larr; {title}</Link>

        {book.readable.length > 1 && (
          <select
            value={file.file_sha}
            onChange={(e) => setParams({ file_sha: e.target.value })}
          >
            {book.readable.map((f) => (
              <option key={f.file_sha} value={f.file_sha}>
                {f.format.toUpperCase()} ({f.role})
              </option>
            ))}
          </select>
        )}

        {file.engine && (
          <>
            {COLORS.map((c) => (
              <button
                key={c}
                aria-label={`Highlight ${c}`}
                className={`swatch-button${color === c ? " on" : ""}`}
                style={{ background: SWATCH[c] }}
                onClick={() => setColor(c)}
              />
            ))}
            {/* Derived from the format, not from engineRef: the ref is
                null on the first render, so reading supportsArea off it
                would hide the tool until something else re-rendered. */}
            {file.engine === "pdf" && (
              <button
                className={areaMode ? "on" : ""}
                onClick={() => setAreaMode((v) => !v)}
              >
                Area
              </button>
            )}
            {file.engine === "pdf" ? (
              <span className="sizing">
                <button onClick={() => setZoom((z) => Math.max(0.5, z - 0.2))}>
                  &minus;
                </button>
                <span>{Math.round(zoom * 100)}%</span>
                <button onClick={() => setZoom((z) => Math.min(4, z + 0.2))}>
                  +
                </button>
              </span>
            ) : (
              <span className="sizing">
                <button onClick={() => setFontSize((f) => Math.max(10, f - 2))}>
                  A&minus;
                </button>
                <span>{fontSize}px</span>
                <button onClick={() => setFontSize((f) => Math.min(32, f + 2))}>
                  A+
                </button>
              </span>
            )}

            <button
              onClick={() => {
                const engine = engineRef.current;
                if (!engine) return;
                const loc = engine.locate();
                create(
                  file.engine === "pdf"
                    ? { kind: "pdf-page", page: Number(loc.locator.replace("page=", "")) || 1 }
                    : { kind: "epub", cfi: loc.locator },
                  "bookmark",
                  color,
                );
              }}
            >
              Bookmark
            </button>
          </>
        )}
      </header>

      <div className="reader-body">
        {file.engine ? (
          <div className="reader-host" ref={hostRef} />
        ) : (
          <ConvertToRead sha={sha} file={file} onDone={reload} />
        )}
        <Sidebar
          annotations={annotations}
          onJump={(a) => void engineRef.current?.goTo(a.anchor as Anchor)}
          onUpdate={update}
          onRemove={remove}
          unsaved={unsaved}
          error={annError}
          onRetry={retry}
        />
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Add the route**

In `web/src/App.tsx`, import `Reader from "./reader/Reader"` and add
inside `<Routes>`:

```tsx
          <Route path="/read/:sha" element={<Reader />} />
```

- [ ] **Step 4: Add the stylesheet rules**

Append to the project's existing stylesheet (`web/src/styles.css`):

```css
.reader { display: flex; flex-direction: column; height: 100vh; }
.reader-toolbar { display: flex; gap: .5rem; align-items: center; padding: .5rem; border-bottom: 1px solid #ddd; }
.reader-body { display: flex; flex: 1; min-height: 0; }
.reader-host { flex: 1; overflow: auto; background: #525659; padding: 1rem; }
.reader-host .pdf-page { margin: 0 auto 1rem; background: #fff; box-shadow: 0 1px 6px rgba(0,0,0,.4); }
.reader-host.area-mode { cursor: crosshair; }
.reader-host.area-mode .textLayer { pointer-events: none; }
.reader-sidebar { width: 22rem; overflow: auto; border-left: 1px solid #ddd; padding: .5rem; }
.reader-sidebar .annotations { list-style: none; margin: 0; padding: 0; }
.reader-sidebar .annotation { border-bottom: 1px solid #eee; padding: .5rem 0; }
.reader-sidebar .jump { display: flex; gap: .4rem; align-items: baseline; width: 100%; text-align: left; background: none; border: 0; cursor: pointer; }
.reader-sidebar .swatch { display: inline-block; width: 1rem; height: 1rem; border-radius: 2px; }
.reader-sidebar .quote { font-style: italic; color: #444; }
.reader-toolbar .sizing { display: inline-flex; gap: .25rem; align-items: center; }
.swatch-button { width: 1.25rem; height: 1.25rem; border: 1px solid #999; border-radius: 3px; cursor: pointer; }
.swatch-button.on { outline: 2px solid #222; }
.reader-notice { flex: 1; display: grid; place-content: center; gap: .75rem; text-align: center; }
.banner.error { background: #fdecea; padding: .5rem; display: flex; gap: .5rem; justify-content: space-between; }
/* pdf.js ships its own text-layer rules; these are the minimum needed
   for selection to line up with the rendered glyphs. */
.textLayer { opacity: .25; line-height: 1; }
.textLayer span { position: absolute; white-space: pre; transform-origin: 0 0; color: transparent; }
.textLayer ::selection { background: #2979ff; }
```

- [ ] **Step 5: Typecheck, lint and build**

```bash
cd web && npm run build && npm run lint && npm test
```

- [ ] **Step 6: Commit**

```bash
git add web/src/reader/Reader.tsx web/src/reader/ConvertToRead.tsx \
        web/src/App.tsx web/src/styles.css
git commit -m "feat(reader): reader shell, toolbar and /read/:sha route"
```

---

## Task 11: Book detail page integration

**Files:**
- Modify: `web/src/routes/BookDetail.tsx`

**Interfaces:**
- Consumes: `BookDetail.readable`, `listAnnotations`.
- Produces: a "Read" link per readable file and a read-only annotation
  list grouped by file.

- [ ] **Step 1: Implement**

In `web/src/routes/BookDetail.tsx`:

Add imports:

```tsx
import { Link } from "react-router-dom";
import { listAnnotations } from "../api";
import type { Annotation } from "../api";
```

Add state and a load effect beside the existing ones:

```tsx
  const [annotations, setAnnotations] = useState<Annotation[]>([]);
  useEffect(() => {
    listAnnotations(sha).then(setAnnotations).catch(() => setAnnotations([]));
  }, [sha]);
```

In the files list (around line 201), add a Read link per file. Replace
the existing row body with:

```tsx
          {sidecar.files.map((f) => {
            const readable = book.readable.find((r) => r.path === f.path);
            return (
              <li key={f.path}>
                <a href={fileUrl(sha, f.path)}>{f.path}</a>{" "}
                <span className="muted">{f.format} · {f.role}</span>{" "}
                {readable && (
                  <Link to={`/read/${sha}?file_sha=${readable.file_sha}`}>
                    {readable.engine ? "Read" : `Convert & read`}
                  </Link>
                )}
              </li>
            );
          })}
```

Add a section after the files list:

```tsx
        {annotations.length > 0 && (
          <section>
            <h3>Highlights and notes</h3>
            {book.readable
              .filter((f) => annotations.some((a) => a.file_sha === f.file_sha))
              .map((f) => (
                <div key={f.file_sha}>
                  <h4>
                    {f.format.toUpperCase()} · {f.role}{" "}
                    <Link to={`/read/${sha}?file_sha=${f.file_sha}`}>open</Link>
                  </h4>
                  <ul>
                    {annotations
                      .filter((a) => a.file_sha === f.file_sha)
                      .map((a) => (
                        <li key={a.id}>
                          {a.type === "bookmark" ? "⚑ " : ""}
                          {typeof (a.anchor as { page?: number }).page === "number"
                            ? `p.${(a.anchor as { page: number }).page} `
                            : ""}
                          {typeof (a.anchor as { text?: string }).text === "string" && (
                            <q>{(a.anchor as { text: string }).text}</q>
                          )}{" "}
                          {a.note && <span className="note">{a.note}</span>}
                        </li>
                      ))}
                  </ul>
                </div>
              ))}
          </section>
        )}
```

- [ ] **Step 2: Typecheck, lint and build**

```bash
cd web && npm run build && npm run lint
```

- [ ] **Step 3: Commit**

```bash
git add web/src/routes/BookDetail.tsx
git commit -m "feat(web): read links and annotation list on the book page"
```

---

## Task 12: Verification against the real library

**Files:**
- Modify: `README.md`
- Create: `docs/superpowers/plans/2026-09-30-reader-smoke.md`

Engine integration cannot be unit-tested honestly — pdf.js in jsdom
needs a canvas, and the thing being verified is whether a highlight lands
where the user drew it. This task is the checklist that substitutes, run
against real files from the live library.

- [ ] **Step 1: Run the full automated suite**

```bash
.venv/bin/python -m pytest -q
cd web && npm test && npm run build && npm run lint
```

Expected: all Python tests pass; vitest passes; the build emits to
`src/reshelf/web/static/`.

- [ ] **Step 2: Start the server**

```bash
rm -f db/.lock && .venv/bin/reshelf serve
```

- [ ] **Step 3: Work the checklist**

Write the results into `docs/superpowers/plans/2026-09-30-reader-smoke.md`
as a table with a pass/fail and a note per row.

| # | Case | How to find one | Expected |
|---|---|---|---|
| 1 | Text-layer PDF opens | filter format=pdf, pick one whose text selects | pages render, text selectable |
| 2 | Scanned PDF opens | most PDFs in this library | pages render, no text selection, area tool works |
| 3 | Large scanned PDF (>50MB) | sort by size on disk | first page appears without downloading the whole file (check the network panel for 206s) |
| 4 | EPUB opens | filter format=epub | paginated, text selectable |
| 5 | Converted-from-MOBI EPUB | convert a MOBI, then read it | opens; annotations bind to the derived file's `file_sha` |
| 6 | Area highlight persists | draw a box, reload | same place, same colour |
| 7 | **Area highlight at a different zoom** | draw at 100%, reload at 200% | same region of the page, not offset |
| 8 | **Area highlight after a window resize** | draw, resize, reload | same region |
| 9 | Mixed-size pages | a PDF whose pages differ in size | a highlight on a later page lands correctly |
| 10 | Rotated page | a sideways scan | the box lands where it was drawn |
| 11 | Text highlight stores its quote | select text, check the sidebar | the quote shows |
| 12 | Note add / edit / delete | any highlight | persists across reload |
| 13 | Bookmark | toolbar button, then click it in the sidebar | jumps back |
| 14 | Progress resumes | read to p.40, leave, reopen | opens at p.40 |
| 15 | Progress on a hard close | read, close the tab, reopen | at most 5s lost |
| 16 | Two files, two annotation sets | a book with an original and a derived file | switching files switches the sidebar |
| 17 | MOBI convert-and-read | open a MOBI in the reader | convert button, then the EPUB opens |
| 18 | DJVU | one of the 3 | offers Convert to PDF |
| 19 | Annotations survive a reindex | `rm db/books.sqlite3 && reshelf reindex` | every annotation still there |
| 20 | Save failure keeps the mark | stop the server, draw a box | the box stays, a retry banner appears |

- [ ] **Step 4: Fix anything the checklist fails**

Each failure gets its own commit. If a failure is in `anchors.ts`, add
the case to `anchors.test.ts` first — that is the module that has tests
precisely so geometry bugs get pinned.

- [ ] **Step 5: Document the reader in the README**

Add to the Web app section:

```markdown
### Reading

Open a book and click **Read** on any PDF or EPUB file.

- **Select text** to highlight it. About two-thirds of this library's
  PDFs are scans with no text layer, so on those use the **Area** tool
  instead: click it, then drag a box over the region.
- Any highlight takes a **note**. **Bookmark** marks a place without a
  mark. Reading position is saved automatically.
- MOBI, AZW3 and TXT offer **Convert to EPUB & read**; DJVU offers
  **Convert to PDF**.

Highlights and notes live in the book's JSON sidecar under
`metadata/`, never in the database — deleting `db/books.sqlite3` and
running `reshelf reindex` cannot lose them.

The SPA must be built before `reshelf serve` will show any of this:

    cd web && npm install && npm run build
```

- [ ] **Step 6: Commit**

```bash
git add README.md docs/superpowers/plans/2026-09-30-reader-smoke.md
git commit -m "docs: reader usage and smoke-test results"
```

---

## Definition of Done

Mirrors the spec's §10. Check each before declaring the branch finished:

- [ ] `/read/:sha` opens a text-layer PDF, a scanned PDF and an EPUB from
      the live library, resuming at the stored position.
- [ ] Dragging a box on a scanned PDF page creates a highlight that
      persists across a reload and lands in the same place at a different
      zoom level and window size.
- [ ] Selecting text on a text-layer PDF or an EPUB creates a highlight
      that stores its quoted text.
- [ ] A note can be attached to, edited on and removed from any
      highlight; a bookmark can be added and jumped to.
- [ ] The sidebar lists the active file's annotations and clicking one
      jumps to it.
- [ ] Killing the server mid-session loses at most the last 5 seconds of
      reading position and no annotations.
- [ ] Opening a MOBI offers "Convert to EPUB & read" and, after the job,
      reads the derived EPUB.
- [ ] Deleting `db/books.sqlite3` and running `reshelf reindex` leaves
      every annotation intact.
- [ ] A's CLI, API and test suite still pass unchanged.
