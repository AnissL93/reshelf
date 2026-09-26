import pytest
from fastapi.testclient import TestClient

from reshelf.web.app import create_app
from tests.test_api_metadata import SHA, build


@pytest.fixture
def client(tmp_path):
    build(tmp_path)
    with TestClient(create_app(tmp_path)) as c:
        yield c


def test_whole_file_download(client):
    r = client.get(f"/api/books/{SHA}/file")
    assert r.status_code == 200
    assert r.content[:2] == b"PK"
    assert r.headers["accept-ranges"] == "bytes"


def test_range_request_returns_206_and_the_right_bytes(client):
    whole = client.get(f"/api/books/{SHA}/file").content
    r = client.get(f"/api/books/{SHA}/file", headers={"Range": "bytes=0-9"})
    assert r.status_code == 206
    assert r.content == whole[:10]
    assert r.headers["content-range"] == f"bytes 0-9/{len(whole)}"


def test_open_ended_range(client):
    whole = client.get(f"/api/books/{SHA}/file").content
    r = client.get(f"/api/books/{SHA}/file", headers={"Range": "bytes=5-"})
    assert r.status_code == 206
    assert r.content == whole[5:]


def test_suffix_range(client):
    whole = client.get(f"/api/books/{SHA}/file").content
    r = client.get(f"/api/books/{SHA}/file", headers={"Range": "bytes=-10"})
    assert r.status_code == 206
    assert r.content == whole[-10:]


def test_an_unsatisfiable_range_is_416(client):
    r = client.get(f"/api/books/{SHA}/file", headers={"Range": "bytes=999999999-"})
    assert r.status_code == 416


def test_a_malformed_range_falls_back_to_the_whole_file(client):
    r = client.get(f"/api/books/{SHA}/file", headers={"Range": "pages=1-2"})
    assert r.status_code == 200


def test_file_for_an_unknown_book_is_404(client):
    assert client.get("/api/books/" + "f" * 64 + "/file").status_code == 404


def test_a_sidecar_pointing_outside_the_library_root_is_refused(tmp_path):
    """Defence in depth: the served path is always inside the root."""
    build(tmp_path)
    from reshelf.config import load_config
    from reshelf.store.sidecar import SidecarStore

    store = SidecarStore(load_config(tmp_path))
    store.update(SHA, lambda b: setattr(b.files[0], "path", "/etc/passwd"))
    with TestClient(create_app(tmp_path)) as c:
        assert c.get(f"/api/books/{SHA}/file").status_code == 404


def test_a_symlink_escaping_the_library_root_is_refused(tmp_path):
    """The same guard must catch a symlink, not just a literal '..' path."""
    build(tmp_path)
    from reshelf.config import load_config
    from reshelf.store.sidecar import SidecarStore

    outside = tmp_path.parent / "outside-secret.epub"
    outside.write_bytes(b"top secret")
    link = tmp_path / "library" / "escape.epub"
    link.symlink_to(outside)

    store = SidecarStore(load_config(tmp_path))
    store.update(SHA, lambda b: setattr(b.files[0], "path", str(link)))
    with TestClient(create_app(tmp_path)) as c:
        assert c.get(f"/api/books/{SHA}/file").status_code == 404


def test_cover_is_404_before_extraction_and_200_after(tmp_path):
    cfg, path = build(tmp_path)
    with TestClient(create_app(tmp_path)) as c:
        assert c.get(f"/api/books/{SHA}/cover").status_code == 404

    from reshelf.covers import cover_path, thumb_path

    for p in (cover_path(tmp_path / "covers", SHA), thumb_path(tmp_path / "covers", SHA)):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"\xff\xd8\xff\xdb fake jpeg")

    with TestClient(create_app(tmp_path)) as c:
        assert c.get(f"/api/books/{SHA}/cover").status_code == 200
        assert c.get(f"/api/books/{SHA}/cover", params={"size": "thumb"}).status_code == 200
