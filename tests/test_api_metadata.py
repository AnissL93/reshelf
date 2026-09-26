import hashlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from reshelf.config import default_config, save_config
from reshelf.db.database import Database
from reshelf.store import index
from reshelf.store.models import Book, FileEntry
from reshelf.store.sidecar import SidecarStore
from reshelf.web.app import create_app
from tests.helpers import make_epub

SHA = "a" * 64


def build(tmp_path, fmt="epub", status="COMMITTED", location="library", layout="hash"):
    """`tmp_path` is the library root here - callers pass a subdirectory of
    the real tmp_path when the root's own *name* is what is under test."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    cfg = default_config(tmp_path)
    cfg.metadata.layout = layout
    for sub in ("incoming", "library", "db", "metadata", "derived", "reports", "covers"):
        (tmp_path / sub).mkdir(parents=True, exist_ok=True)
    save_config(cfg, tmp_path)

    path = tmp_path / location / f"Old Title.{fmt}"
    if fmt == "epub":
        make_epub(path, "Old Title", "Old Author")
    else:
        path.write_bytes(b"x")

    db = Database(cfg.database.path)
    db.init_schema()
    store = SidecarStore(cfg)
    book = Book(sha256=SHA, files=[FileEntry(path=str(path), format=fmt)])
    book.metadata.title = "Old Title"
    book.metadata.authors = ["Old Author"]
    store.save(book, str(path))
    db.conn.execute(
        "INSERT INTO files (path, sha256, format, status) VALUES (?,?,?,?)",
        (str(path), SHA, fmt, status),
    )
    index.sync(db.conn, book)
    db.conn.commit()
    db.close()
    return cfg, path


@pytest.fixture
def client(tmp_path):
    build(tmp_path)
    with TestClient(create_app(tmp_path)) as c:
        yield c


PATCH = {"metadata": {"title": "New Title", "authors": ["New Author"]}}


def test_patch_writes_the_sidecar_and_marks_it_human(client):
    body = client.patch(f"/api/books/{SHA}/metadata", json=PATCH).json()
    assert body["sidecar"]["metadata"]["title"] == "New Title"
    assert body["sidecar"]["source"]["resolver"] == "human"


def test_patch_updates_the_search_index(client):
    client.patch(f"/api/books/{SHA}/metadata", json=PATCH)
    assert client.get("/api/books", params={"q": "New Title"}).json()["total"] == 1


def test_patch_alone_does_not_touch_the_file(client, tmp_path):
    from reshelf.extractors.epub import extract_epub

    client.patch(f"/api/books/{SHA}/metadata", json=PATCH)
    path = tmp_path / "library" / "Old Title.epub"
    assert path.exists()
    assert extract_epub(path).title == "Old Title"


def test_embed_writes_into_the_file(client, tmp_path):
    from reshelf.extractors.epub import extract_epub

    payload = {**PATCH, "write_back": {"embed": True}}
    body = client.patch(f"/api/books/{SHA}/metadata", json=payload).json()
    assert body["embedded"] is True
    assert extract_epub(tmp_path / "library" / "Old Title.epub").title == "New Title"


def test_library_rename_moves_the_file(client, tmp_path):
    payload = {**PATCH, "write_back": {"library_file": True}}
    body = client.patch(f"/api/books/{SHA}/metadata", json=payload).json()
    assert body["library_file"] is not None
    assert "New Title" in body["library_file"]
    assert not (tmp_path / "library" / "Old Title.epub").exists()


def test_embed_on_an_unconverted_kindle_file_is_422_with_a_reason(tmp_path):
    build(tmp_path, fmt="azw3")
    with TestClient(create_app(tmp_path)) as c:
        r = c.patch(
            f"/api/books/{SHA}/metadata",
            json={**PATCH, "write_back": {"embed": True}},
        )
        assert r.status_code == 422
        assert r.json()["detail"]["reason"] == "convert_first"


def test_embed_on_an_incoming_only_book_is_422_and_leaves_the_original_untouched(
    tmp_path,
):
    """A book that is only under incoming/ (never committed) has an EMBEDDABLE
    format but no safe copy to write into - embedding would rewrite (and
    re-hash) the user's source file."""
    _cfg, path = build(tmp_path, status="MATCHED", location="incoming")
    before = hashlib.sha256(path.read_bytes()).digest()
    with TestClient(create_app(tmp_path)) as c:
        r = c.patch(
            f"/api/books/{SHA}/metadata",
            json={**PATCH, "write_back": {"embed": True}},
        )
        assert r.status_code == 422
        assert r.json()["detail"]["reason"] == "not_in_library"
        assert hashlib.sha256(path.read_bytes()).digest() == before


def test_embed_and_rename_together_on_an_incoming_only_book_is_422_without_renaming(
    tmp_path,
):
    _cfg, path = build(tmp_path, status="MATCHED", location="incoming")
    with TestClient(create_app(tmp_path)) as c:
        r = c.patch(
            f"/api/books/{SHA}/metadata",
            json={**PATCH, "write_back": {"embed": True, "library_file": True}},
        )
        assert r.status_code == 422
        assert r.json()["detail"]["reason"] == "not_in_library"
        assert path.exists()
        assert not (tmp_path / "library" / "New Title.epub").exists()


def test_a_failed_embed_still_leaves_the_sidecar_written(tmp_path):
    """Tier 1 is not rolled back by a tier-3 failure - it is the source of truth."""
    build(tmp_path, fmt="azw3")
    with TestClient(create_app(tmp_path)) as c:
        c.patch(
            f"/api/books/{SHA}/metadata",
            json={**PATCH, "write_back": {"embed": True}},
        )
        detail = c.get(f"/api/books/{SHA}").json()
        assert detail["sidecar"]["metadata"]["title"] == "New Title"


def test_rename_on_an_uncommitted_book_warns_instead_of_failing(tmp_path):
    build(tmp_path, status="MATCHED")
    with TestClient(create_app(tmp_path)) as c:
        body = c.patch(
            f"/api/books/{SHA}/metadata",
            json={**PATCH, "write_back": {"library_file": True}},
        ).json()
        assert body["library_file"] is None
        assert body["warnings"]


def test_patch_on_an_unknown_book_is_404(client):
    r = client.patch("/api/books/" + "f" * 64 + "/metadata", json=PATCH)
    assert r.status_code == 404


# -- C1: the guard that keeps tier 3 off an original -----------------------


def test_embed_never_reaches_an_original_when_the_library_root_is_named_library(
    tmp_path,
):
    """A root literally called `library` - /mnt/d/library, ~/library.

    Every path under it then has "library" as a path component, so a bare
    `"library" in Path(p).parts` test calls the incoming original a
    library copy, lets tier 3 through, and rewrites (and re-hashes) the
    user's source file. Spec invariant 4: book bytes in incoming/ are
    never rewritten.
    """
    root = tmp_path / "library"
    _cfg, path = build(root, status="MATCHED", location="incoming")
    assert "library" in path.parts  # the trap this test exists for
    before = hashlib.sha256(path.read_bytes()).digest()

    with TestClient(create_app(root)) as c:
        r = c.patch(
            f"/api/books/{SHA}/metadata",
            json={**PATCH, "write_back": {"embed": True}},
        )
        assert r.status_code == 422
        assert r.json()["detail"]["reason"] == "not_in_library"
    assert hashlib.sha256(path.read_bytes()).digest() == before


def test_a_real_library_copy_under_a_library_root_still_embeds(tmp_path):
    """The other half of C1: the guard must not now refuse everything."""
    from reshelf.extractors.epub import extract_epub

    root = tmp_path / "library"
    build(root, location="library")
    with TestClient(create_app(root)) as c:
        body = c.patch(
            f"/api/books/{SHA}/metadata",
            json={**PATCH, "write_back": {"embed": True}},
        ).json()
        assert body["embedded"] is True
    assert extract_epub(root / "library" / "Old Title.epub").title == "New Title"


def test_detail_reports_the_primary_format_resolved_against_the_real_root(tmp_path):
    root = tmp_path / "library"
    build(root, status="MATCHED", location="incoming")
    with TestClient(create_app(root)) as c:
        assert c.get(f"/api/books/{SHA}").json()["primary_format"] == "epub"


def test_embed_on_a_file_deleted_out_of_band_is_422_not_500(tmp_path):
    """files[] is user-editable JSON and the file it names can vanish."""
    _cfg, path = build(tmp_path)
    path.unlink()
    with TestClient(create_app(tmp_path)) as c:
        r = c.patch(
            f"/api/books/{SHA}/metadata",
            json={**PATCH, "write_back": {"embed": True}},
        )
        assert r.status_code == 422
        assert r.json()["detail"]["reason"] == "file_missing"
        # tier 1 still landed
        detail = c.get(f"/api/books/{SHA}").json()
        assert detail["sidecar"]["metadata"]["title"] == "New Title"


# -- C2: one sidecar locator, kept on the original -------------------------


def test_library_rename_under_a_sidecar_layout_does_not_lose_the_book(tmp_path):
    """The tier-2 crash: the sidecar is loaded keyed on row["path"] and
    stored keyed on the new library path. Under metadata.layout=sidecar
    those are different files, so store.update raised KeyError *after*
    shutil.move and the journal write had already run - the file ended up
    somewhere nothing recorded, and the user saw a 500.
    """
    _cfg, path = build(tmp_path, layout="sidecar")
    with TestClient(create_app(tmp_path)) as c:
        r = c.patch(
            f"/api/books/{SHA}/metadata",
            json={**PATCH, "write_back": {"library_file": True}},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        new_path = body["library_file"]
        assert new_path is not None and "New Title" in new_path
        assert Path(new_path).exists()
        assert not path.exists()
        # The sidecar is still where it always was, beside the old path,
        # and now names where the bytes actually are.
        sidecar = json.loads(Path(str(path) + ".json").read_text())
        assert [f["path"] for f in sidecar["files"]] == [new_path]
        # ...and the response round-trips through the same locator.
        assert [f["path"] for f in body["sidecar"]["files"]] == [new_path]
        assert c.get(f"/api/books/{SHA}/file").status_code == 200
