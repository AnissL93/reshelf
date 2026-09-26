import json

import pytest

from reshelf.config import default_config
from reshelf.store.models import Book, FileEntry
from reshelf.store.sidecar import SidecarStore

SHA = "a" * 64


def store_for(tmp_path, layout="hash"):
    cfg = default_config(tmp_path)
    cfg.metadata.layout = layout
    return SidecarStore(cfg)


def a_book(path="incoming/dune.epub"):
    return Book(
        sha256=SHA,
        files=[FileEntry(path=path, format="epub", size=10, mtime=1)],
    )


def test_round_trip(tmp_path):
    store = store_for(tmp_path)
    book = a_book()
    book.metadata.title = "Dune"
    book.metadata.authors = ["Frank Herbert"]
    store.save(book)
    loaded = store.load(SHA)
    assert loaded.metadata.title == "Dune"
    assert loaded.metadata.authors == ["Frank Herbert"]
    assert loaded.sha256 == SHA


def test_missing_book_loads_as_none(tmp_path):
    assert store_for(tmp_path).load(SHA) is None


def test_schema_key_is_written_as_schema_not_schema_version(tmp_path):
    store = store_for(tmp_path)
    store.save(a_book())
    raw = json.loads(store.path_for(SHA).read_text())
    assert raw["schema"] == 1
    assert "schema_version" not in raw


def test_unknown_keys_survive_a_rewrite(tmp_path):
    """Sub-project B adds keys this code has never heard of."""
    store = store_for(tmp_path)
    store.save(a_book())
    path = store.path_for(SHA)
    raw = json.loads(path.read_text())
    raw["future_feature"] = {"kept": True}
    raw["reading"]["future_locator"] = "x"
    path.write_text(json.dumps(raw))

    store.update(SHA, lambda b: setattr(b.metadata, "title", "Changed"))

    raw = json.loads(path.read_text())
    assert raw["future_feature"] == {"kept": True}
    assert raw["reading"]["future_locator"] == "x"
    assert raw["metadata"]["title"] == "Changed"


def test_write_is_atomic_and_leaves_no_temp_file(tmp_path):
    store = store_for(tmp_path)
    store.save(a_book())
    assert list(store.path_for(SHA).parent.glob("*.tmp")) == []


def test_a_failed_write_leaves_the_old_content_intact(tmp_path, monkeypatch):
    store = store_for(tmp_path)
    book = a_book()
    book.metadata.title = "Original"
    store.save(book)

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr("os.replace", boom)
    book.metadata.title = "Doomed"
    with pytest.raises(OSError):
        store.save(book)
    assert store.load(SHA).metadata.title == "Original"


def test_hash_layout_never_touches_the_book_folder(tmp_path):
    store = store_for(tmp_path)
    assert store.path_for(SHA) == tmp_path.resolve() / "metadata" / f"{SHA}.json"


def test_sidecar_layout_sits_beside_the_file(tmp_path):
    store = store_for(tmp_path, layout="sidecar")
    p = store.path_for(SHA, "incoming/dune.epub")
    assert p == tmp_path.resolve() / "incoming" / "dune.epub.json"


def test_non_hash_layout_requires_a_file_path(tmp_path):
    store = store_for(tmp_path, layout="sidecar")
    with pytest.raises(ValueError, match="file_path"):
        store.path_for(SHA)


def test_iter_all_finds_every_book(tmp_path):
    store = store_for(tmp_path)
    for n in ("a", "b", "c"):
        store.save(Book(sha256=n * 64))
    assert {b.sha256 for b in store.iter_all()} == {"a" * 64, "b" * 64, "c" * 64}


def test_is_human_reflects_the_resolver(tmp_path):
    book = a_book()
    assert book.is_human is False
    book.source.resolver = "human"
    assert book.is_human is True


def test_primary_file_prefers_a_converted_file(tmp_path):
    book = Book(
        sha256=SHA,
        files=[
            FileEntry(path="incoming/x.azw3", format="azw3", role="original"),
            FileEntry(path="derived/x.epub", format="epub", role="converted"),
        ],
    )
    assert book.primary_file().path == "derived/x.epub"


def test_primary_file_prefers_library_over_incoming(tmp_path):
    book = Book(
        sha256=SHA,
        files=[
            FileEntry(path="incoming/x.epub", format="epub"),
            FileEntry(path="library/A/x.epub", format="epub"),
        ],
    )
    assert book.primary_file().path == "library/A/x.epub"


def test_update_applies_the_mutation_and_bumps_updated_at(tmp_path):
    store = store_for(tmp_path)
    store.save(a_book())
    before = store.load(SHA).updated_at
    after = store.update(SHA, lambda b: setattr(b.metadata, "title", "New"))
    assert after.metadata.title == "New"
    assert after.updated_at >= before


def test_update_on_a_missing_book_raises(tmp_path):
    with pytest.raises(KeyError):
        store_for(tmp_path).update(SHA, lambda b: None)
