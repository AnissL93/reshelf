from typer.testing import CliRunner

from book_organizer.cli import app

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
