import json

from reshelf.db.database import Database
from reshelf.metadata.models import Author, Candidate, Edition, Work
from reshelf.planner.planner import generate_plan


def test_generate_plan_actions(tmp_path):
    with Database(tmp_path / "db" / "books.sqlite3") as db:
        db.init_schema()
        fid, _ = db.upsert_file("/x/tbp.epub", 10, 1, "epub")
        db.set_hash(fid, "aaa")
        eid = db.save_candidate(
            Candidate(
                provider="openlibrary",
                provider_id="/books/OL26831316M",
                edition=Edition(
                    work=Work(title="The Three-Body Problem",
                              authors=[Author(name="Liu Cixin")]),
                    isbn13="9780765382030",
                    publisher="Tor Books",
                    publication_date="2014",
                ),
            )
        )
        db.set_file_match(fid, eid, 0.99, "MATCHED")

        did, _ = db.upsert_file("/x/dup.epub", 10, 1, "epub")
        db.set_hash(did, "aaa")  # duplicate of tbp.epub
        uid, _ = db.upsert_file("/x/unknown.epub", 5, 1, "epub")
        db.set_hash(uid, "bbb")
        db.set_status(uid, "UNRESOLVED")
        db.conn.commit()

        out = generate_plan(db, tmp_path / "reports")

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
