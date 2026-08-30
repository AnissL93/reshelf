from typer.testing import CliRunner

from book_organizer.cli import app

runner = CliRunner()


def test_help_runs():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "ebooks" in result.output
