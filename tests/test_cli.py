from typer.testing import CliRunner

from book_organizer.cli import app
from tests.helpers import make_epub

runner = CliRunner()


def test_help_runs():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "ebooks" in result.output


def test_init_creates_layout(tmp_path):
    result = runner.invoke(app, ["init", str(tmp_path)])
    assert result.exit_code == 0, result.output
    for sub in [
        "incoming", "library", "quarantine", "duplicates", "covers",
        "cache/openlibrary", "reports", "db",
    ]:
        assert (tmp_path / sub).is_dir(), sub
    assert (tmp_path / "config.yaml").is_file()
    assert (tmp_path / "db" / "books.sqlite3").is_file()
    # idempotent
    assert runner.invoke(app, ["init", str(tmp_path)]).exit_code == 0


def _init_root(tmp_path):
    runner.invoke(app, ["init", str(tmp_path)])
    return tmp_path


def test_scan_incremental_and_duplicates(tmp_path):
    root = _init_root(tmp_path)
    (root / "incoming" / "a.epub").write_bytes(b"AAA")
    (root / "incoming" / "copy-of-a.epub").write_bytes(b"AAA")
    (root / "incoming" / "b.pdf").write_bytes(b"BBB")

    r1 = runner.invoke(app, ["scan", "--root", str(root)])
    assert r1.exit_code == 0, r1.output
    assert "seen=3 added=3" in r1.output and "duplicates=1" in r1.output

    r2 = runner.invoke(app, ["scan", "--root", str(root)])
    assert "added=0 changed=0" in r2.output


def test_extract_sets_metadata_and_states(tmp_path):
    root = _init_root(tmp_path)
    make_epub(
        root / "incoming" / "tbp.epub",
        title="The Three-Body Problem",
        author="Liu Cixin",
        isbn="9780765382030",
        language="en",
    )
    (root / "incoming" / "bad.epub").write_bytes(b"not a zip")
    runner.invoke(app, ["scan", "--root", str(root)])

    r = runner.invoke(app, ["extract", "--root", str(root)])
    assert r.exit_code == 0, r.output

    from book_organizer.config import load_config
    from book_organizer.db.database import Database

    cfg = load_config(root)
    with Database(cfg.database.path) as db:
        rows = {r["path"].split("/")[-1]: r for r in db.conn.execute("SELECT * FROM files")}
    assert rows["tbp.epub"]["status"] == "IDENTIFIED"
    assert rows["tbp.epub"]["title_raw"] == "The Three-Body Problem"
    assert rows["tbp.epub"]["author_raw"] == "Liu Cixin"
    assert rows["tbp.epub"]["isbn_raw"] == "9780765382030"
    assert rows["tbp.epub"]["language_raw"] == "en"
    assert rows["bad.epub"]["status"] == "ERROR"


OL_ISBN_RESPONSE = {
    "ISBN:9780765382030": {
        "key": "/books/OL26831316M",
        "title": "The Three-Body Problem",
        "authors": [{"name": "Liu Cixin"}],
        "publishers": [{"name": "Tor Books"}],
        "publish_date": "2014",
        "identifiers": {"isbn_13": ["9780765382030"]},
    }
}


def test_match_offline_with_seeded_cache(tmp_path):
    from book_organizer.config import load_config
    from book_organizer.db.database import Database
    from book_organizer.providers.cache import FileCache

    root = _init_root(tmp_path)
    make_epub(
        root / "incoming" / "tbp.epub",
        title="The Three-Body Problem",
        author="Liu Cixin",
        isbn="9780765382030",
        language="en",
    )
    make_epub(root / "incoming" / "mystery.epub", title="zzz no such book qqq",
              author="Nobody")
    runner.invoke(app, ["scan", "--root", str(root)])
    runner.invoke(app, ["extract", "--root", str(root)])

    FileCache(root / "cache" / "openlibrary").put(
        "isbn:9780765382030", OL_ISBN_RESPONSE
    )
    r = runner.invoke(app, ["match", "--root", str(root), "--offline"])
    assert r.exit_code == 0, r.output

    cfg = load_config(root)
    with Database(cfg.database.path) as db:
        rows = {r["path"].split("/")[-1]: r for r in db.conn.execute("SELECT * FROM files")}
        assert rows["tbp.epub"]["status"] == "MATCHED"
        assert rows["tbp.epub"]["match_confidence"] == 0.99
        assert rows["mystery.epub"]["status"] == "UNRESOLVED"
        m = db.conn.execute("SELECT * FROM matches").fetchone()
        assert m["status"] == "AUTO_ACCEPT" and m["resolver"] == "deterministic"


def test_resolve_applies_ai_decision(tmp_path, monkeypatch):
    from book_organizer.ai.resolver import AIDecision, ClaudeCLIResolver
    from book_organizer.config import load_config
    from book_organizer.db.database import Database
    from book_organizer.providers.cache import FileCache

    root = _init_root(tmp_path)
    make_epub(
        root / "incoming" / "tbp.epub",
        title="The Three-Body Problem",
        author="Liu Cixin",
        isbn="9780765382030",
        language="en",
    )
    runner.invoke(app, ["scan", "--root", str(root)])
    runner.invoke(app, ["extract", "--root", str(root)])
    FileCache(root / "cache" / "openlibrary").put(
        "isbn:9780765382030", OL_ISBN_RESPONSE
    )
    runner.invoke(app, ["match", "--root", str(root), "--offline"])

    cfg = load_config(root)
    with Database(cfg.database.path) as db:
        fid = db.conn.execute("SELECT id FROM files").fetchone()["id"]
        # force into the review queue so resolve picks it up
        db.set_file_match(fid, None, 0.80, "REVIEW")
        db.conn.commit()

    calls = []

    def fake_resolve(self, local, candidates):
        calls.append((local, len(candidates)))
        return AIDecision(
            decision=0, confidence=0.95, reasons=["same work, translated title"]
        )

    monkeypatch.setattr(ClaudeCLIResolver, "resolve", fake_resolve)
    r = runner.invoke(app, ["resolve", "--root", str(root)])
    assert r.exit_code == 0, r.output

    with Database(cfg.database.path) as db:
        f = db.conn.execute("SELECT * FROM files WHERE id=?", (fid,)).fetchone()
        assert f["status"] == "MATCHED"
        assert f["match_confidence"] == 0.95
        m = db.conn.execute(
            "SELECT * FROM matches WHERE resolver='ai' ORDER BY id DESC"
        ).fetchone()
        assert m is not None and m["status"] == "HIGH_CONFIDENCE"
        assert "ai:same work, translated title" in m["evidence_json"]
    assert len(calls) == 1


def test_resolve_null_decision_marks_unresolved(tmp_path, monkeypatch):
    from book_organizer.ai.resolver import AIDecision, ClaudeCLIResolver
    from book_organizer.config import load_config
    from book_organizer.db.database import Database
    from book_organizer.providers.cache import FileCache

    root = _init_root(tmp_path)
    make_epub(root / "incoming" / "x.epub", title="The Three-Body Problem",
              author="Liu Cixin", isbn="9780765382030", language="en")
    runner.invoke(app, ["scan", "--root", str(root)])
    runner.invoke(app, ["extract", "--root", str(root)])
    FileCache(root / "cache" / "openlibrary").put(
        "isbn:9780765382030", OL_ISBN_RESPONSE
    )
    runner.invoke(app, ["match", "--root", str(root), "--offline"])
    cfg = load_config(root)
    with Database(cfg.database.path) as db:
        fid = db.conn.execute("SELECT id FROM files").fetchone()["id"]
        db.set_file_match(fid, None, 0.80, "REVIEW")
        db.conn.commit()

    monkeypatch.setattr(
        ClaudeCLIResolver,
        "resolve",
        lambda self, local, candidates: AIDecision(
            decision=None, confidence=0.1, reasons=["different work"]
        ),
    )
    r = runner.invoke(app, ["resolve", "--root", str(root)])
    assert r.exit_code == 0, r.output
    with Database(cfg.database.path) as db:
        f = db.conn.execute("SELECT * FROM files WHERE id=?", (fid,)).fetchone()
        assert f["status"] == "UNRESOLVED"
