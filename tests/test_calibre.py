import json

from book_organizer.calibre.export import (
    CalibreBook,
    add_command,
    books_to_export,
    normalize_pubdate,
    parse_added_ids,
    set_metadata_command,
)
from book_organizer.db.database import Database


def test_normalize_pubdate_handles_douban_and_openlibrary_forms():
    assert normalize_pubdate("Jul 11, 2018") == "2018-07-11"
    assert normalize_pubdate("2018-11-30") == "2018-11-30"
    assert normalize_pubdate("2018-10-1") == "2018-10-01"
    assert normalize_pubdate("2010-12") == "2010-12-01"
    assert normalize_pubdate("2014") == "2014-01-01"


def test_normalize_pubdate_returns_none_for_unparseable():
    assert normalize_pubdate("") is None
    assert normalize_pubdate(None) is None
    assert normalize_pubdate("no date here") is None


def test_add_command_carries_matched_metadata():
    book = CalibreBook(
        path="/lib/Liu Cixin/The Three-Body Problem (2014)/The Three-Body Problem.epub",
        title="The Three-Body Problem",
        authors="Liu Cixin",
        publisher="Tor",
        pubdate="2014-11-11",
        language="en",
        isbn="9780765382030",
        identifiers={"douban": "26647206"},
    )
    cmd = add_command(book, "/mnt/win-d/Books")

    assert cmd[:2] == ["calibredb", "add"]
    assert cmd[-1] == book.path
    assert "--library-path" in cmd and "/mnt/win-d/Books" in cmd
    assert "--title" in cmd and "The Three-Body Problem" in cmd
    assert "--authors" in cmd and "Liu Cixin" in cmd
    assert "--isbn" in cmd and "9780765382030" in cmd
    assert "--languages" in cmd and "en" in cmd
    assert "--identifier" in cmd and "douban:26647206" in cmd


def test_add_command_omits_absent_fields():
    book = CalibreBook(path="/lib/a.pdf", title="A", authors=None, publisher=None,
                       pubdate=None, language=None, isbn=None, identifiers={})
    cmd = add_command(book, "/lib")

    assert "--authors" not in cmd
    assert "--isbn" not in cmd
    assert "--languages" not in cmd
    assert "--identifier" not in cmd
    assert "--title" in cmd


def test_set_metadata_command_sets_publisher_and_pubdate():
    book = CalibreBook(path="/lib/a.pdf", title="A", authors=None, publisher="科学出版社",
                       pubdate="2018-11-30", language=None, isbn=None, identifiers={})
    cmd = set_metadata_command(42, book, "/lib")

    assert cmd[:3] == ["calibredb", "set_metadata", "42"]
    assert "publisher:科学出版社" in cmd
    assert "pubdate:2018-11-30" in cmd


def test_set_metadata_command_is_none_when_nothing_to_set():
    book = CalibreBook(path="/lib/a.pdf", title="A", authors=None, publisher=None,
                       pubdate=None, language=None, isbn=None, identifiers={})
    assert set_metadata_command(42, book, "/lib") is None


def test_parse_added_ids_reads_calibredb_output():
    assert parse_added_ids("Added book ids: 1234\n") == [1234]
    assert parse_added_ids("Added book ids: 7, 8, 9\n") == [7, 8, 9]
    assert parse_added_ids("The following books were not added...\n") == []


def _seed(tmp_path):
    """A database with one COMMITTED file matched to a full edition."""
    db_path = tmp_path / "books.sqlite3"
    with Database(db_path) as db:
        db.init_schema()
        c = db.conn
        c.execute("INSERT INTO works (id, canonical_title) VALUES (1, 'The Three-Body Problem')")
        c.execute("INSERT INTO authors (id, canonical_name) VALUES (1, 'Liu Cixin')")
        c.execute("INSERT INTO work_authors (work_id, author_id) VALUES (1, 1)")
        c.execute(
            "INSERT INTO editions (id, work_id, isbn13, publisher, publication_date, language)"
            " VALUES (1, 1, '9780765382030', 'Tor', 'Nov 11, 2014', 'en')"
        )
        c.execute("INSERT INTO identifiers (edition_id, type, value, source)"
                  " VALUES (1, 'douban', '26647206', 'douban')")
        c.execute(
            "INSERT INTO files (id, path, status, matched_edition_id, format)"
            " VALUES (1, '/src/tbp.epub', 'COMMITTED', 1, 'epub')"
        )
        c.commit()
    return db_path


def test_books_to_export_joins_metadata_with_journal_destinations(tmp_path):
    db_path = _seed(tmp_path)
    dest = tmp_path / "library" / "Liu Cixin" / "The Three-Body Problem (2014)" / "tbp.epub"
    dest.parent.mkdir(parents=True)
    dest.write_bytes(b"EPUB")
    journal = {"actions": [{"action": "import", "src": "/src/tbp.epub", "dest": str(dest)}]}

    with Database(db_path) as db:
        books = books_to_export(db, [journal])

    assert len(books) == 1
    b = books[0]
    assert b.path == str(dest)
    assert b.title == "The Three-Body Problem"
    assert b.authors == "Liu Cixin"
    assert b.publisher == "Tor"
    assert b.pubdate == "2014-11-11"
    assert b.language == "en"
    assert b.isbn == "9780765382030"
    assert b.identifiers == {"douban": "26647206"}


def test_books_to_export_skips_destinations_missing_on_disk(tmp_path):
    db_path = _seed(tmp_path)
    journal = {"actions": [{"action": "import", "src": "/src/tbp.epub", "dest": "/gone/tbp.epub"}]}

    with Database(db_path) as db:
        assert books_to_export(db, [journal]) == []


def test_books_to_export_ignores_non_import_actions(tmp_path):
    db_path = _seed(tmp_path)
    dest = tmp_path / "q" / "tbp.epub"
    dest.parent.mkdir(parents=True)
    dest.write_bytes(b"EPUB")
    journal = {"actions": [{"action": "quarantine", "src": "/src/tbp.epub", "dest": str(dest)}]}

    with Database(db_path) as db:
        assert books_to_export(db, [journal]) == []


# --- CLI ---------------------------------------------------------------------

from typer.testing import CliRunner  # noqa: E402

from book_organizer.cli import app  # noqa: E402
from book_organizer.config import load_config  # noqa: E402

runner = CliRunner()


def _committed_root(tmp_path):
    """A root whose single COMMITTED book sits in library/ with a commit journal."""
    runner.invoke(app, ["init", str(tmp_path)])
    cfg = load_config(tmp_path)
    dest = tmp_path / "library" / "Liu Cixin" / "The Three-Body Problem (2014)" / "tbp.epub"
    dest.parent.mkdir(parents=True)
    dest.write_bytes(b"EPUB")
    with Database(cfg.database.path) as db:
        c = db.conn
        c.execute("INSERT INTO works (id, canonical_title) VALUES (1, 'The Three-Body Problem')")
        c.execute("INSERT INTO authors (id, canonical_name) VALUES (1, 'Liu Cixin')")
        c.execute("INSERT INTO work_authors (work_id, author_id) VALUES (1, 1)")
        c.execute("INSERT INTO editions (id, work_id, isbn13, publisher, publication_date)"
                  " VALUES (1, 1, '9780765382030', 'Tor', '2014')")
        c.execute("INSERT INTO files (id, path, status, matched_edition_id, format)"
                  " VALUES (1, '/src/tbp.epub', 'COMMITTED', 1, 'epub')")
        c.commit()
    (tmp_path / "reports" / "commit-20260903-000000-aaaaaaaa.json").write_text(
        json.dumps({"actions": [{"action": "import", "src": "/src/tbp.epub",
                                 "dest": str(dest)}]})
    )
    return tmp_path


def test_calibre_export_dry_run_reports_books_without_running_calibredb(tmp_path):
    root = _committed_root(tmp_path)
    r = runner.invoke(app, ["calibre-export", "--root", str(root),
                            "--library", str(tmp_path / "cal"), "--dry-run"])
    assert r.exit_code == 0, r.output
    assert "1" in r.output
    assert not (tmp_path / "cal").exists()


def test_calibre_export_errors_when_no_journal(tmp_path):
    runner.invoke(app, ["init", str(tmp_path)])
    r = runner.invoke(app, ["calibre-export", "--root", str(tmp_path),
                            "--library", str(tmp_path / "cal"), "--dry-run"])
    assert r.exit_code == 1
    assert "no commit journal" in r.output
