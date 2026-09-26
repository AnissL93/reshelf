import warnings

import pytest

# Importing fastapi.testclient itself triggers Starlette's httpx2 nag, at
# collection time (before a filterwarnings mark on a test would apply) - so
# it's silenced here, before the import, rather than as a test mark.
with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    from fastapi.testclient import TestClient

from reshelf.config import default_config, save_config
from reshelf.db.database import Database
from reshelf.metadata.models import Author, Candidate, Edition, Work
from reshelf.store import index
from reshelf.store.models import Book, FileEntry
from reshelf.store.sidecar import SidecarStore
from reshelf.web.app import create_app

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")

SHA = "a" * 64


@pytest.fixture
def client(tmp_path):
    cfg = default_config(tmp_path)
    for sub in ("incoming", "library", "db", "metadata", "derived", "reports", "covers"):
        (tmp_path / sub).mkdir(parents=True, exist_ok=True)
    save_config(cfg, tmp_path)

    db = Database(cfg.database.path)
    db.init_schema()
    store = SidecarStore(cfg)
    for n, (sha, title, status) in enumerate(
        [(SHA, "Dune", "MATCHED"), ("b" * 64, "Neuromancer", "UNRESOLVED")]
    ):
        book = Book(
            sha256=sha,
            files=[FileEntry(path=f"incoming/{title}.epub", format="epub")],
        )
        book.metadata.title = title
        book.metadata.authors = ["Frank Herbert" if n == 0 else "William Gibson"]
        book.metadata.tags = ["sci-fi"]
        store.save(book)
        db.conn.execute(
            "INSERT INTO files (path, sha256, format, status)"
            " VALUES (?,?,'epub',?)",
            (f"incoming/{title}.epub", sha, status),
        )
        index.sync(db.conn, book)
    db.conn.commit()
    db.close()

    with TestClient(create_app(tmp_path)) as c:
        yield c


def test_list_returns_both_books(client):
    body = client.get("/api/books").json()
    assert body["total"] == 2
    assert {b["title"] for b in body["items"]} == {"Dune", "Neuromancer"}


def test_list_includes_pipeline_status(client):
    body = client.get("/api/books").json()
    statuses = {b["title"]: b["status"] for b in body["items"]}
    assert statuses["Neuromancer"] == "UNRESOLVED"


def test_search(client):
    assert client.get("/api/books", params={"q": "Gibson"}).json()["total"] == 1


def test_filter_by_status(client):
    body = client.get("/api/books", params={"status": "MATCHED"}).json()
    assert body["total"] == 1
    assert body["items"][0]["title"] == "Dune"


def test_paging(client):
    body = client.get("/api/books", params={"page_size": 1, "page": 2}).json()
    assert body["total"] == 2
    assert len(body["items"]) == 1


def test_an_invalid_sort_is_rejected(client):
    assert client.get("/api/books", params={"sort": "nope"}).status_code == 422


def test_detail_returns_the_whole_sidecar(client):
    body = client.get(f"/api/books/{SHA}").json()
    assert body["sidecar"]["metadata"]["title"] == "Dune"
    assert body["sidecar"]["schema"] == 1
    assert body["status"] == "MATCHED"
    assert body["paths"] == ["incoming/Dune.epub"]


def test_detail_for_an_unknown_book_is_404(client):
    assert client.get("/api/books/" + "f" * 64).status_code == 404


def test_tags_endpoint(client):
    assert client.get("/api/tags").json() == ["sci-fi"]


def test_duplicate_paths_collapse_to_one_book(client):
    """Byte-identical files (same sha256) must be one row in the list, with
    every duplicate path surfaced on the detail response."""
    state = client.app.state.reshelf
    with state.db.lock:
        state.db.conn.execute(
            "INSERT INTO files (path, sha256, format, status)"
            " VALUES (?,?,'epub','MATCHED')",
            ("library/Dune-copy.epub", SHA),
        )
        state.db.conn.commit()

    body = client.get("/api/books").json()
    assert body["total"] == 2
    assert len([b for b in body["items"] if b["title"] == "Dune"]) == 1

    detail = client.get(f"/api/books/{SHA}").json()
    assert sorted(detail["paths"]) == ["incoming/Dune.epub", "library/Dune-copy.epub"]


def test_candidates_keep_every_co_author(client):
    """A LIMIT-1 author join silently drops co-authors; this must not regress."""
    state = client.app.state.reshelf
    candidate = Candidate(
        provider="openlibrary",
        provider_id="OL1W",
        edition=Edition(
            work=Work(
                title="Good Omens",
                authors=[Author(name="Terry Pratchett"), Author(name="Neil Gaiman")],
            ),
            isbn13="9780060853983",
        ),
        score=95.0,
        confidence=0.95,
    )
    with state.db.lock:
        file_id = state.db.conn.execute(
            "SELECT id FROM files WHERE sha256 = ?", (SHA,)
        ).fetchone()["id"]
        edition_id = state.db.save_candidate(candidate)
        state.db.record_match(
            file_id, edition_id, candidate.score, candidate.confidence,
            "deterministic", ["title:exact"], "MATCHED",
        )
        state.db.conn.commit()

    for body in (
        client.get(f"/api/books/{SHA}").json()["candidates"],
        client.get(f"/api/books/{SHA}/candidates").json(),
    ):
        assert len(body) == 1
        authors = {a.strip() for a in body[0]["authors"].split(";")}
        assert authors == {"Terry Pratchett", "Neil Gaiman"}
