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
