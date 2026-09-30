import warnings

import pytest

# Importing fastapi.testclient itself triggers Starlette's httpx2 nag, at
# collection time (before a filterwarnings mark on a test would apply) - so
# it's silenced here, before the import, rather than as a test mark.
with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    from fastapi.testclient import TestClient

from reshelf.config import default_config, save_config
from reshelf.covers import cover_path, thumb_path
from reshelf.db.database import Database
from reshelf.metadata.models import Author, Candidate, Edition, Work
from reshelf.store import index
from reshelf.store.models import Book, FileEntry
from reshelf.store.sidecar import SidecarStore
from reshelf.web.app import create_app
from tests.helpers import make_epub

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


# -- minors ---------------------------------------------------------------


def test_has_cover_is_false_until_the_thumbnail_exists_too(client, tmp_path):
    """The grid requests ?size=thumb; ensure_cover writes the 600px image
    first and can fail before the 200px one, so a flag that only tested
    the full image put a broken <img> in every card."""
    covers = tmp_path / "covers"
    covers.mkdir(exist_ok=True)
    cover_path(covers, SHA).write_bytes(b"jpegish")

    item = next(i for i in client.get("/api/books").json()["items"] if i["sha256"] == SHA)
    assert item["has_cover"] is False
    assert client.get(f"/api/books/{SHA}").json()["has_cover"] is False
    assert client.get(f"/api/books/{SHA}/cover?size=thumb").status_code == 404

    thumb_path(covers, SHA).write_bytes(b"jpegish")
    item = next(i for i in client.get("/api/books").json()["items"] if i["sha256"] == SHA)
    assert item["has_cover"] is True


def test_file_serves_the_row_that_was_clicked_not_the_first_original(tmp_path):
    """A committed book has two files[] entries with role "original" - the
    incoming source and the library copy. ?original=true could only ever
    serve the first, so clicking the library/... row downloaded the
    incoming copy."""
    cfg = default_config(tmp_path)
    for sub in ("incoming", "library", "db", "metadata", "reports", "covers"):
        (tmp_path / sub).mkdir(parents=True, exist_ok=True)
    save_config(cfg, tmp_path)

    incoming = tmp_path / "incoming" / "x.epub"
    incoming.write_bytes(b"INCOMING BYTES")
    library = tmp_path / "library" / "A" / "x.epub"
    library.parent.mkdir(parents=True)
    library.write_bytes(b"LIBRARY BYTES")

    db = Database(cfg.database.path)
    db.init_schema()
    store = SidecarStore(cfg)
    book = Book(
        sha256=SHA,
        files=[
            FileEntry(path=str(incoming), format="epub"),
            FileEntry(path=str(library), format="epub"),
        ],
    )
    store.save(book, str(incoming))
    db.conn.execute(
        "INSERT INTO files (path, sha256, format, status)"
        " VALUES (?,?,'epub','COMMITTED')",
        (str(incoming), SHA),
    )
    db.conn.commit()
    db.close()

    with TestClient(create_app(tmp_path)) as c:
        assert c.get(f"/api/books/{SHA}/file", params={"path": str(library)}).content == (
            b"LIBRARY BYTES"
        )
        assert c.get(f"/api/books/{SHA}/file", params={"path": str(incoming)}).content == (
            b"INCOMING BYTES"
        )
        # no path: the primary, which is the library copy
        assert c.get(f"/api/books/{SHA}/file").content == b"LIBRARY BYTES"
        # a path that is not one of this book's files is not served
        assert c.get(
            f"/api/books/{SHA}/file", params={"path": "/etc/passwd"}
        ).status_code == 404


def test_get_book_with_no_files_rows_is_404_under_a_non_hash_layout(tmp_path):
    """store.load(sha, None) raised ValueError under sidecar|library - a
    500 where the answer is simply "no such book"."""
    cfg = default_config(tmp_path)
    cfg.metadata.layout = "sidecar"
    for sub in ("incoming", "library", "db", "metadata", "reports", "covers"):
        (tmp_path / sub).mkdir(parents=True, exist_ok=True)
    save_config(cfg, tmp_path)
    db = Database(cfg.database.path)
    db.init_schema()
    db.close()

    with TestClient(create_app(tmp_path)) as c:
        assert c.get(f"/api/books/{SHA}").status_code == 404
        assert c.get(f"/api/books/{SHA}/file").status_code == 404


# -- readable tests -------------------------------------------------------


def build(tmp_path, fmt="epub"):
    """Build a test book with a single file."""
    cfg = default_config(tmp_path)
    for sub in ("incoming", "library", "db", "metadata", "derived", "reports", "covers"):
        (tmp_path / sub).mkdir(parents=True, exist_ok=True)
    save_config(cfg, tmp_path)

    db = Database(cfg.database.path)
    db.init_schema()
    store = SidecarStore(cfg)

    path = tmp_path / "incoming" / f"test.{fmt}"
    if fmt == "epub":
        make_epub(path, "Test Title", "Test Author")
    else:
        path.write_bytes(b"x")

    book = Book(sha256=SHA, files=[FileEntry(path=str(path), format=fmt)])
    store.save(book)
    db.conn.execute(
        "INSERT INTO files (path, sha256, format, status) VALUES (?,?,?,?)",
        (str(path), SHA, fmt, "MATCHED"),
    )
    index.sync(db.conn, book)
    db.conn.commit()
    db.close()


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
