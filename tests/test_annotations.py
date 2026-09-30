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
