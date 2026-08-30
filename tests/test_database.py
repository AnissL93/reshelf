import pytest

from book_organizer.db.database import Database, LockError


def test_init_schema_creates_tables(tmp_path):
    with Database(tmp_path / "db" / "books.sqlite3") as db:
        db.init_schema()
        names = {
            r["name"]
            for r in db.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert {
        "files", "works", "editions", "authors", "author_aliases",
        "work_authors", "identifiers", "metadata_sources", "matches",
        "scan_runs",
    } <= names


def test_lock_prevents_second_instance(tmp_path):
    path = tmp_path / "db" / "books.sqlite3"
    with Database(path):
        with pytest.raises(LockError):
            Database(path)
    # released on close
    Database(path).close()


def test_wal_mode(tmp_path):
    with Database(tmp_path / "db" / "books.sqlite3") as db:
        mode = db.conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode == "wal"


def _mkdb(tmp_path):
    db = Database(tmp_path / "db" / "books.sqlite3")
    db.init_schema()
    return db


def test_upsert_file_lifecycle(tmp_path):
    with _mkdb(tmp_path) as db:
        fid, state = db.upsert_file("/x/a.epub", 100, 111, "epub")
        assert state == "new"
        assert db.upsert_file("/x/a.epub", 100, 111, "epub") == (fid, "unchanged")
        fid2, state2 = db.upsert_file("/x/a.epub", 200, 222, "epub")
        assert (fid2, state2) == (fid, "changed")
        row = db.conn.execute("SELECT * FROM files WHERE id=?", (fid,)).fetchone()
        assert row["sha256"] is None and row["status"] == "NEW"


def test_set_hash_marks_duplicates(tmp_path):
    with _mkdb(tmp_path) as db:
        a, _ = db.upsert_file("/x/a.epub", 1, 1, "epub")
        b, _ = db.upsert_file("/x/b.epub", 1, 1, "epub")
        assert db.set_hash(a, "abc") is False
        assert db.set_hash(b, "abc") is True
        rows = {r["path"]: r["status"] for r in db.conn.execute("SELECT * FROM files")}
        assert rows["/x/a.epub"] == "SCANNED"
        assert rows["/x/b.epub"] == "DUPLICATE"


def test_raw_metadata_and_status_queries(tmp_path):
    with _mkdb(tmp_path) as db:
        fid, _ = db.upsert_file("/x/a.epub", 1, 1, "epub")
        db.set_hash(fid, "abc")
        db.set_raw_metadata(fid, title="三体", author="刘慈欣", isbn="9780765382030", language="zh")
        db.set_status(fid, "IDENTIFIED")
        rows = db.files_with_status("IDENTIFIED")
        assert len(rows) == 1 and rows[0]["title_raw"] == "三体"


def test_scan_runs(tmp_path):
    with _mkdb(tmp_path) as db:
        rid = db.start_scan_run("/x")
        db.finish_scan_run(rid, seen=3, added=2, changed=1)
        row = db.conn.execute("SELECT * FROM scan_runs WHERE id=?", (rid,)).fetchone()
        assert row["files_seen"] == 3 and row["completed_at"] is not None


from book_organizer.metadata.models import Author, Candidate, Edition, Work  # noqa: E402


def _tbp_candidate():
    return Candidate(
        provider="openlibrary",
        provider_id="/books/OL26831316M",
        edition=Edition(
            work=Work(title="The Three-Body Problem", authors=[Author(name="Liu Cixin")]),
            isbn13="9780765382030",
            publisher="Tor Books",
            publication_date="2014",
        ),
    )


def test_save_candidate_dedupes(tmp_path):
    with _mkdb(tmp_path) as db:
        e1 = db.save_candidate(_tbp_candidate())
        e2 = db.save_candidate(_tbp_candidate())
        assert e1 == e2
        assert db.conn.execute("SELECT COUNT(*) c FROM works").fetchone()["c"] == 1
        assert db.conn.execute("SELECT COUNT(*) c FROM authors").fetchone()["c"] == 1
        ident = db.conn.execute("SELECT * FROM identifiers").fetchone()
        assert (ident["type"], ident["value"]) == ("isbn13", "9780765382030")
        src = db.conn.execute("SELECT * FROM metadata_sources").fetchone()
        assert src["provider"] == "openlibrary" and src["edition_id"] == e1


def test_record_match_and_file_link(tmp_path):
    with _mkdb(tmp_path) as db:
        fid, _ = db.upsert_file("/x/a.epub", 1, 1, "epub")
        eid = db.save_candidate(_tbp_candidate())
        db.record_match(fid, eid, 140.0, 0.99, "deterministic",
                        ["exact_isbn", "exact_title"], "AUTO_ACCEPT")
        db.set_file_match(fid, eid, 0.99, "MATCHED")
        m = db.conn.execute("SELECT * FROM matches").fetchone()
        assert m["status"] == "AUTO_ACCEPT" and '"exact_isbn"' in m["evidence_json"]
        f = db.conn.execute("SELECT * FROM files WHERE id=?", (fid,)).fetchone()
        assert f["matched_edition_id"] == eid and f["status"] == "MATCHED"
