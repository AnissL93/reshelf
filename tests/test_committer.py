import hashlib
import json
from pathlib import Path

from typer.testing import CliRunner

from reshelf.cli import app
from reshelf.config import default_config, load_config, save_config
from reshelf.db.database import Database
from reshelf.planner.committer import apply_plan, dest_for
from reshelf.store.models import Book, FileEntry
from reshelf.store.sidecar import SidecarStore

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
    """init root, one MATCHED file with a matching sidecar, generate plan; returns (root, src)."""
    root = tmp_path
    runner.invoke(app, ["init", str(root)])
    src = root / "incoming" / filename
    src.write_bytes(content)
    cfg = load_config(root)
    sha256 = hashlib.sha256(content).hexdigest()
    with Database(cfg.database.path) as db:
        st = src.stat()
        fid, _ = db.upsert_file(
            str(src), st.st_size, int(st.st_mtime), src.suffix.lstrip(".")
        )
        db.set_hash(fid, sha256)
        db.set_file_match(fid, None, 0.99, "MATCHED")
        db.conn.commit()

    book = Book(sha256=sha256, files=[FileEntry(path=str(src), format=src.suffix.lstrip("."))])
    book.metadata.title = "The Three-Body Problem"
    book.metadata.authors = ["Liu Cixin"]
    book.metadata.isbn13 = "9780765382030"
    book.metadata.pubdate = "2014"
    SidecarStore(cfg).save(book, str(src))

    r = runner.invoke(app, ["plan", "--root", str(root)])
    assert r.exit_code == 0, r.output
    return root, src


def committed_env(tmp_path, mode: str = "copy", fmt: str = "epub"):
    """Plan then commit one MATCHED book directly (not via the CLI) so `mode` can vary."""
    content = f"{fmt.upper()}DATA".encode()
    root, src = _setup_committed_root(tmp_path, content=content, filename=f"tbp.{fmt}")
    cfg = load_config(root)
    db = Database(cfg.database.path)
    plan_path = next((root / "reports").glob("plan-*.json"))
    journal = apply_plan(
        json.loads(plan_path.read_text()),
        db,
        library_dir=root / "library",
        quarantine_dir=cfg.library.quarantine,
        duplicates_dir=root / "duplicates",
        reports_dir=root / "reports",
        mode=mode,
    )
    dest = Path(journal["actions"][0]["dest"])
    return {
        "src": src,
        "dest": dest,
        "db": db,
        "journal": journal,
        "reports": root / "reports",
        "library_dir": root / "library",
    }


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


def test_copy_mode_leaves_the_original_in_place(tmp_path):
    env = committed_env(tmp_path, mode="copy")
    assert env["src"].exists()
    assert env["dest"].exists()


def test_move_mode_removes_the_original(tmp_path):
    env = committed_env(tmp_path, mode="move")
    assert not env["src"].exists()
    assert env["dest"].exists()


def test_rollback_restores_a_moved_original(tmp_path):
    env = committed_env(tmp_path, mode="move")
    from reshelf.planner.committer import rollback_journal

    result = rollback_journal(env["journal"], env["db"], env["library_dir"])
    assert result == {"reverted": 1, "skipped": 0}
    assert env["src"].exists()
    assert not env["dest"].exists()


def test_a_kindle_file_is_committed_as_is_not_converted(tmp_path):
    """Conversion is a per-book action now; commit must not silently convert."""
    env = committed_env(tmp_path, mode="copy", fmt="azw3")
    assert env["dest"].suffix == ".azw3"
    assert env["dest"].read_bytes() == b"AZW3DATA"


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
