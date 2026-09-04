import json

from typer.testing import CliRunner

from reshelf.cli import app
from reshelf.db.database import Database
from reshelf.reports.report import build_report

runner = CliRunner()


def _seed(tmp_path):
    db = Database(tmp_path / "db" / "books.sqlite3")
    db.init_schema()
    for i, status in enumerate(
        ["MATCHED", "MATCHED", "REVIEW", "DUPLICATE", "UNRESOLVED", "ERROR"]
    ):
        fid, _ = db.upsert_file(f"/x/{i}.epub", 1, 1, "epub")
        db.set_status(fid, status)
    for _ in range(3):
        db.conn.execute("INSERT INTO editions (work_id) VALUES (NULL)")
    db.record_match(1, 1, 140, 0.99, "deterministic", ["exact_isbn"], "AUTO_ACCEPT")
    db.record_match(2, 2, 90, 0.93, "deterministic", ["exact_title", "exact_author"],
                    "HIGH_CONFIDENCE")
    db.record_match(3, 3, 60, 0.80, "deterministic", ["title_sim>=0.85"],
                    "REVIEW_RECOMMENDED")
    db.conn.commit()
    return db


def test_build_report_counts(tmp_path):
    with _seed(tmp_path) as db:
        rep = build_report(db)
    assert rep == {
        "files_scanned": 6,
        "exact_isbn_matches": 1,
        "high_confidence": 1,
        "needs_review": 1,
        "duplicates": 1,
        "unresolved": 1,
        "errors": 1,
    }


def test_report_json_command(tmp_path):
    _seed(tmp_path).close()
    from reshelf.config import default_config, save_config

    save_config(default_config(tmp_path), tmp_path)
    r = runner.invoke(app, ["report", "--root", str(tmp_path), "--json"])
    assert r.exit_code == 0, r.output
    assert json.loads(r.output)["files_scanned"] == 6
