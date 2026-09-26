import json

from reshelf.config import default_config
from reshelf.db.database import Database
from reshelf.planner.planner import generate_plan
from reshelf.store.models import Book, FileEntry
from reshelf.store.sidecar import SidecarStore


def test_generate_plan_actions(tmp_path):
    cfg = default_config(tmp_path)
    store = SidecarStore(cfg)
    with Database(cfg.database.path) as db:
        db.init_schema()
        fid, _ = db.upsert_file("/x/tbp.epub", 10, 1, "epub")
        db.set_hash(fid, "aaa")
        db.set_file_match(fid, None, 0.99, "MATCHED")

        book = Book(sha256="aaa", files=[FileEntry(path="/x/tbp.epub", format="epub")])
        book.metadata.title = "The Three-Body Problem"
        book.metadata.authors = ["Liu Cixin"]
        book.metadata.isbn13 = "9780765382030"
        book.metadata.publisher = "Tor Books"
        book.metadata.pubdate = "2014"
        store.save(book, "/x/tbp.epub")

        did, _ = db.upsert_file("/x/dup.epub", 10, 1, "epub")
        db.set_hash(did, "aaa")  # duplicate of tbp.epub
        uid, _ = db.upsert_file("/x/unknown.epub", 5, 1, "epub")
        db.set_hash(uid, "bbb")
        db.set_status(uid, "UNRESOLVED")
        db.conn.commit()

        out = generate_plan(db, store, tmp_path / "reports")

    plan = json.loads(out.read_text())
    assert out.name.startswith("plan-") and plan["plan_id"] in out.name
    actions = {a["file"]: a for a in plan["actions"]}
    assert actions["/x/tbp.epub"]["action"] == "import"
    assert actions["/x/tbp.epub"]["preconditions"] == {
        "sha256": "aaa", "size": 10, "mtime": 1,
    }
    assert actions["/x/tbp.epub"]["metadata_changes"]["isbn13"] == "9780765382030"
    assert actions["/x/tbp.epub"]["metadata_changes"]["title"] == "The Three-Body Problem"
    assert actions["/x/tbp.epub"]["metadata_changes"]["author"] == "Liu Cixin"
    assert actions["/x/dup.epub"]["action"] == "mark_duplicate"
    assert actions["/x/unknown.epub"]["action"] == "quarantine"


def test_plan_metadata_comes_from_the_sidecar_not_the_edition_tables(tmp_path):
    """A human correction in the sidecar must drive the destination path."""
    cfg = default_config(tmp_path)
    db = Database(cfg.database.path)
    db.init_schema()
    store = SidecarStore(cfg)

    book = Book(
        sha256="a" * 64,
        files=[FileEntry(path="incoming/x.epub", format="epub")],
    )
    book.metadata.title = "Corrected Title"
    book.metadata.authors = ["Real Author"]
    book.source.resolver = "human"
    store.save(book)

    db.conn.execute(
        "INSERT INTO files (path, sha256, format, size, mtime, status)"
        " VALUES ('incoming/x.epub', ?, 'epub', 1, 1, 'MATCHED')",
        ("a" * 64,),
    )
    db.conn.commit()

    plan = json.loads(generate_plan(db, store, tmp_path / "reports").read_text())
    imports = [a for a in plan["actions"] if a["action"] == "import"]
    assert imports[0]["metadata_changes"]["title"] == "Corrected Title"
    assert imports[0]["metadata_changes"]["author"] == "Real Author"
    db.close()
