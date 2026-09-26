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
        "store": SidecarStore(cfg),
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
    cfg = load_config(root)
    sha256 = hashlib.sha256(src.read_bytes()).hexdigest()
    before = SidecarStore(cfg).load(sha256, str(src)).files

    r = runner.invoke(app, ["commit", "--root", str(root), "--dry-run"])

    assert r.exit_code == 0, r.output
    assert not (root / "library" / "Liu Cixin").exists()
    assert not list((root / "reports").glob("commit-*.json"))
    after = SidecarStore(cfg).load(sha256, str(src)).files
    assert after == before  # dry-run must not touch the sidecar either


def test_commit_dry_run_in_move_mode_keeps_the_original_sidecar_entry(tmp_path):
    """A dry run must not pre-emptively drop the source location from the sidecar."""
    root, src = _setup_committed_root(tmp_path)
    cfg = load_config(root)
    cfg.library.commit_mode = "move"
    save_config(cfg, root)
    sha256 = hashlib.sha256(src.read_bytes()).hexdigest()

    r = runner.invoke(app, ["commit", "--root", str(root), "--dry-run"])

    assert r.exit_code == 0, r.output
    book = SidecarStore(cfg).load(sha256, str(src))
    assert any(f.path == str(src) for f in book.files)
    assert src.exists()  # and the file itself was never moved


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

    result = rollback_journal(
        env["journal"], env["db"], env["library_dir"], env["store"]
    )
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


def _cli_committed(tmp_path, mode):
    """init -> plan -> commit through the CLI, so pipeline.commit's own
    sidecar bookkeeping (_record_committed_path) runs too."""
    root, src = _setup_committed_root(tmp_path)
    if mode == "move":
        cfg = load_config(root)
        cfg.library.commit_mode = "move"
        save_config(cfg, root)
    assert runner.invoke(app, ["commit", "--root", str(root)]).exit_code == 0
    cfg = load_config(root)
    sha256 = json.loads(
        next((root / "metadata").glob("*.json")).read_text()
    )["sha256"]
    journal = json.loads(next((root / "reports").glob("commit-*.json")).read_text())
    dest = Path(journal["actions"][0]["dest"])
    return root, cfg, src, dest, sha256, journal["commit_id"]


def test_move_mode_repoints_files_path_at_the_destination(tmp_path):
    """I5: move mode deletes the source but left files.path naming it.

    Harmless under metadata.layout=hash, wrong under the others (the
    sidecar locator *is* files.path) and a permanent falsehood either way.
    move_into, three branches over, has always done this.
    """
    root, cfg, src, dest, _sha, _cid = _cli_committed(tmp_path, "move")
    assert not src.exists()
    with Database(cfg.database.path) as db:
        paths = [r["path"] for r in db.conn.execute("SELECT path FROM files")]
    assert paths == [str(dest)]


def test_copy_mode_still_leaves_files_path_on_the_original(tmp_path):
    root, cfg, src, dest, _sha, _cid = _cli_committed(tmp_path, "copy")
    with Database(cfg.database.path) as db:
        paths = [r["path"] for r in db.conn.execute("SELECT path FROM files")]
    assert paths == [str(src)]


def test_rollback_of_a_moved_import_repairs_the_sidecar(tmp_path):
    """I4: rollback restored the bytes but never the sidecar, so files[]
    kept naming a destination that no longer existed - /file 404s, and
    reindex cannot repair it because it rebuilds *from* the sidecars."""
    root, cfg, src, dest, sha256, commit_id = _cli_committed(tmp_path, "move")
    store = SidecarStore(cfg)
    assert [f.path for f in store.load(sha256, str(src)).files] == [str(dest)]

    r = runner.invoke(app, ["rollback", commit_id, "--root", str(root)])
    assert r.exit_code == 0, r.output

    assert src.exists() and not dest.exists()
    assert [f.path for f in store.load(sha256, str(src)).files] == [str(src)]
    with Database(cfg.database.path) as db:
        row = db.conn.execute("SELECT path, status FROM files").fetchone()
        assert row["path"] == str(src)
        assert row["status"] == "MATCHED"


def test_rollback_of_a_copied_import_drops_the_library_entry(tmp_path):
    root, cfg, src, dest, sha256, commit_id = _cli_committed(tmp_path, "copy")
    store = SidecarStore(cfg)
    assert sorted(f.path for f in store.load(sha256, str(src)).files) == sorted(
        [str(src), str(dest)]
    )

    assert runner.invoke(app, ["rollback", commit_id, "--root", str(root)]).exit_code == 0

    assert not dest.exists()
    assert [f.path for f in store.load(sha256, str(src)).files] == [str(src)]


def test_rollback_sidecar_repair_is_idempotent(tmp_path):
    root, cfg, src, dest, sha256, commit_id = _cli_committed(tmp_path, "move")
    store = SidecarStore(cfg)
    for _ in range(2):
        runner.invoke(app, ["rollback", commit_id, "--root", str(root)])
    assert [f.path for f in store.load(sha256, str(src)).files] == [str(src)]


def test_rollback_leaves_the_rest_of_the_sidecar_alone(tmp_path):
    """Only files[] is repaired - another resolver's decision is not."""
    root, cfg, src, dest, sha256, commit_id = _cli_committed(tmp_path, "move")
    store = SidecarStore(cfg)

    def decide(b):
        b.source.resolver = "human"
        b.source.confidence = 1.0
        b.metadata.title = "Hand-corrected"

    store.update(sha256, decide, str(src))
    runner.invoke(app, ["rollback", commit_id, "--root", str(root)])

    book = store.load(sha256, str(src))
    assert book.source.resolver == "human"
    assert book.metadata.title == "Hand-corrected"
    assert [f.path for f in book.files] == [str(src)]
