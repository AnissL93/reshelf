import hashlib
import json

from typer.testing import CliRunner

from reshelf.cli import app
from reshelf.config import default_config, load_config, save_config
from reshelf.db.database import Database
from reshelf.planner.committer import dest_for

runner = CliRunner()


def test_dest_for_naming():
    action = {
        "file": "/x/tbp.EPUB",
        "metadata_changes": {
            "title": "The Three-Body Problem",
            "author": "Liu Cixin",
            "publication_date": "2014",
        },
    }
    from pathlib import Path

    dest = dest_for(action, Path("/lib"))
    assert dest == Path("/lib/Liu Cixin/The Three-Body Problem (2014)/The Three-Body Problem.epub")


def test_dest_for_sanitizes_and_handles_missing():
    from pathlib import Path

    action = {"file": "/x/a.pdf", "metadata_changes": {"title": "三体：黑暗森林? *", "author": None}}
    dest = dest_for(action, Path("/lib"))
    assert dest.parts[2] == "Unknown Author"
    assert "?" not in str(dest) and "*" not in str(dest)
    assert "三体" in dest.name


def _setup_committed_root(tmp_path, content=b"BOOKDATA", filename="tbp.epub"):
    """init root, one MATCHED file with edition, generate plan; returns (root, src)."""
    root = tmp_path
    runner.invoke(app, ["init", str(root)])
    src = root / "incoming" / filename
    src.write_bytes(content)
    cfg = load_config(root)
    with Database(cfg.database.path) as db:
        st = src.stat()
        fid, _ = db.upsert_file(
            str(src), st.st_size, int(st.st_mtime), src.suffix.lstrip(".")
        )
        db.set_hash(fid, hashlib.sha256(content).hexdigest())
        from reshelf.metadata.models import Author, Candidate, Edition, Work

        eid = db.save_candidate(
            Candidate(
                provider="openlibrary",
                provider_id="/books/OL1M",
                edition=Edition(
                    work=Work(title="The Three-Body Problem", authors=[Author(name="Liu Cixin")]),
                    isbn13="9780765382030",
                    publication_date="2014",
                ),
            )
        )
        db.set_file_match(fid, eid, 0.99, "MATCHED")
        db.conn.commit()
    r = runner.invoke(app, ["plan", "--root", str(root)])
    assert r.exit_code == 0, r.output
    return root, src


def test_commit_copies_and_journals(tmp_path):
    root, src = _setup_committed_root(tmp_path)
    r = runner.invoke(app, ["commit", "--root", str(root)])
    assert r.exit_code == 0, r.output

    dest = root / "library" / "Liu Cixin" / "The Three-Body Problem (2014)" / "The Three-Body Problem.epub"
    assert dest.read_bytes() == b"BOOKDATA"
    assert src.exists()  # original untouched

    cfg = load_config(root)
    with Database(cfg.database.path) as db:
        f = db.conn.execute("SELECT status FROM files").fetchone()
        assert f["status"] == "COMMITTED"

    journal_file = next((root / "reports").glob("commit-*.json"))
    journal = json.loads(journal_file.read_text())
    done = [a for a in journal["actions"] if a["action"] == "import"]
    assert done and done[0]["dest"] == str(dest)


def test_commit_skips_changed_file(tmp_path):
    root, src = _setup_committed_root(tmp_path)
    src.write_bytes(b"MODIFIED AFTER PLAN")  # violate preconditions
    r = runner.invoke(app, ["commit", "--root", str(root)])
    assert r.exit_code == 0, r.output
    assert "skipped=1" in r.output
    assert not (root / "library" / "Liu Cixin").exists()
    cfg = load_config(root)
    with Database(cfg.database.path) as db:
        assert db.conn.execute("SELECT status FROM files").fetchone()["status"] == "MATCHED"


def test_commit_dry_run_touches_nothing(tmp_path):
    root, src = _setup_committed_root(tmp_path)
    r = runner.invoke(app, ["commit", "--root", str(root), "--dry-run"])
    assert r.exit_code == 0, r.output
    assert not (root / "library" / "Liu Cixin").exists()
    assert not list((root / "reports").glob("commit-*.json"))


def test_commit_is_idempotent(tmp_path):
    root, src = _setup_committed_root(tmp_path)
    assert runner.invoke(app, ["commit", "--root", str(root)]).exit_code == 0
    # second run: file already COMMITTED, plan action skips as already done
    r = runner.invoke(app, ["commit", "--root", str(root)])
    assert r.exit_code == 0, r.output
    dests = list((root / "library").rglob("*.epub"))
    assert len(dests) == 1


def test_commit_converts_kindle_formats(tmp_path, monkeypatch):
    root, src = _setup_committed_root(tmp_path, content=b"MOBIDATA", filename="tbp.mobi")

    def fake_convert(source, dest, timeout=600):
        from pathlib import Path

        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(b"CONVERTED-EPUB")

    monkeypatch.setattr("reshelf.planner.committer.convert_to_epub", fake_convert)
    r = runner.invoke(app, ["commit", "--root", str(root)])
    assert r.exit_code == 0, r.output

    dest = root / "library" / "Liu Cixin" / "The Three-Body Problem (2014)" / "The Three-Body Problem.epub"
    assert dest.read_bytes() == b"CONVERTED-EPUB"
    assert src.exists() and src.read_bytes() == b"MOBIDATA"  # original untouched

    journal = json.loads(next((root / "reports").glob("commit-*.json")).read_text())
    entry = journal["actions"][0]
    assert entry["converted"] is True and entry["dest"] == str(dest)


def test_commit_conversion_failure_skips(tmp_path, monkeypatch):
    from reshelf.calibre.convert import ConversionError

    root, src = _setup_committed_root(tmp_path, content=b"MOBIDATA", filename="tbp.mobi")

    def broken_convert(source, dest, timeout=600):
        raise ConversionError("boom")

    monkeypatch.setattr("reshelf.planner.committer.convert_to_epub", broken_convert)
    r = runner.invoke(app, ["commit", "--root", str(root)])
    assert r.exit_code == 0, r.output
    assert "skipped=1" in r.output
    assert not list((root / "library").rglob("*.epub"))
    cfg = load_config(root)
    with Database(cfg.database.path) as db:
        assert db.conn.execute("SELECT status FROM files").fetchone()["status"] == "MATCHED"


def test_rollback_removes_copies(tmp_path):
    root, src = _setup_committed_root(tmp_path)
    runner.invoke(app, ["commit", "--root", str(root)])
    journal_file = next((root / "reports").glob("commit-*.json"))
    commit_id = json.loads(journal_file.read_text())["commit_id"]

    r = runner.invoke(app, ["rollback", commit_id, "--root", str(root)])
    assert r.exit_code == 0, r.output
    assert not list((root / "library").rglob("*.epub"))
    assert src.exists()
    cfg = load_config(root)
    with Database(cfg.database.path) as db:
        assert db.conn.execute("SELECT status FROM files").fetchone()["status"] == "MATCHED"
