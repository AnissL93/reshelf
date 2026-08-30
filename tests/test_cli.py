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
