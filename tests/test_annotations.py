import json
import threading

import pytest

from reshelf import annotations as anns
from reshelf.config import default_config, save_config
from reshelf.store.models import Annotation, Book, FileEntry
from reshelf.store.sidecar import SidecarStore

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
    """The `anchor` field is stored verbatim and an unrecognised kind is not interpreted."""
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
            {"id": "no-file-sha"},
            {"id": "good", "type": "highlight", "file_sha": SHA, "anchor": {}},
        ],
    })
    assert [a.id for a in book.annotations] == ["good"]
    assert "annotation" in caplog.text.lower()


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


def test_concurrent_adds_all_land(store, monkeypatch):
    """20 threads add simultaneously, forcing overlapped mutation windows.
    A barrier ensures all threads reach add() together, and a delayed _write
    keeps the window open long enough for a naive load-mutate-save (outside
    the update callback) to lose 19 of the 20 annotations. Mutations inside
    update()'s callback are atomic; this test would fail against that naive
    implementation but passes with our callback-based approach."""
    barrier = threading.Barrier(20)
    errors = []

    # Patch _write to delay, forcing concurrent mutation windows to overlap
    original_write = store._write
    def delayed_write(*args, **kwargs):
        import time
        time.sleep(0.01)  # 10ms delay
        return original_write(*args, **kwargs)
    monkeypatch.setattr(store, "_write", delayed_write)

    def go(i):
        try:
            barrier.wait()  # All threads start at once
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


def test_set_reading_preserves_extra_keys(store):
    """Reading allows extra keys so a newer version's fields survive a
    rewrite by this one. Mutating in-place preserves them; replacing would
    lose them."""
    # Craft a sidecar with an unknown key in reading
    book = store.load(SHA)
    book.reading = {"locator": "old", "percent": 0.5, "future_field": "future_value"}
    store.save(book)

    # Update reading
    reading = anns.set_reading(store, SHA, "new_locator", 0.75)

    # Reload and check both new and old fields are present
    reloaded = store.load(SHA)
    assert reloaded.reading.locator == "new_locator"
    assert reloaded.reading.percent == 0.75
    assert reloaded.reading.__pydantic_extra__.get("future_field") == "future_value"
