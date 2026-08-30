import json

from typer.testing import CliRunner

from book_organizer.cli import app
from book_organizer.providers.cache import FileCache
from tests.helpers import make_epub, make_pdf
from tests.test_cli import OL_ISBN_RESPONSE

runner = CliRunner()


def test_full_pipeline_offline(tmp_path):
    assert runner.invoke(app, ["init", str(tmp_path)]).exit_code == 0
    incoming = tmp_path / "incoming"
    make_epub(incoming / "tbp.epub", title="The Three-Body Problem",
              author="Liu Cixin", isbn="9780765382030", language="en")
    # different bytes (extra publisher field), same ISBN — an edition-level
    # duplicate, NOT a binary duplicate
    make_epub(incoming / "tbp-copy.epub", title="The Three-Body Problem",
              author="Liu Cixin", isbn="9780765382030", language="en",
              publisher="Tor Books")
    make_pdf(incoming / "unknown.pdf", title="", author="")
    # binary duplicate: identical bytes
    (incoming / "tbp-exact-dup.epub").write_bytes((incoming / "tbp.epub").read_bytes())

    FileCache(tmp_path / "cache" / "openlibrary").put(
        "isbn:9780765382030", OL_ISBN_RESPONSE
    )

    for cmd in (
        ["scan", "--root", str(tmp_path)],
        ["extract", "--root", str(tmp_path)],
        ["match", "--root", str(tmp_path), "--offline"],
    ):
        result = runner.invoke(app, cmd)
        assert result.exit_code == 0, (cmd, result.output)

    # scan is idempotent / resumable
    r = runner.invoke(app, ["scan", "--root", str(tmp_path)])
    assert "added=0 changed=0" in r.output

    r = runner.invoke(app, ["report", "--root", str(tmp_path), "--json"])
    rep = json.loads(r.output)
    assert rep["files_scanned"] == 4
    assert rep["exact_isbn_matches"] == 2  # tbp + tbp-copy
    assert rep["duplicates"] == 1
    assert rep["unresolved"] == 1  # unknown.pdf, offline, no candidates

    r = runner.invoke(app, ["plan", "--root", str(tmp_path)])
    assert r.exit_code == 0 and "No files were modified" in r.output
    plan_file = next((tmp_path / "reports").glob("plan-*.json"))
    plan = json.loads(plan_file.read_text())
    by_action = {}
    for a in plan["actions"]:
        by_action.setdefault(a["action"], []).append(a)
        assert a["preconditions"]["sha256"]
    assert len(by_action["import"]) == 2
    assert len(by_action["mark_duplicate"]) == 1
    assert len(by_action["quarantine"]) == 1

    # originals untouched
    names = sorted(p.name for p in incoming.iterdir())
    assert names == ["tbp-copy.epub", "tbp-exact-dup.epub", "tbp.epub", "unknown.pdf"]
