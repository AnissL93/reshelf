import json as _json
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from reshelf.config import default_config, load_config, save_config
from reshelf.calibre.export import books_to_export, export
from reshelf.reports.report import build_report
from reshelf.db.database import Database
from reshelf import pipeline
from reshelf.store.sidecar import SidecarStore

app = typer.Typer(no_args_is_help=True)

ROOT_OPTION = typer.Option(Path("."), "--root", help="Library root (with config.yaml)")


def _bar(label: str):
    """Progress callback that prints to stderr; the job runner uses its own."""
    console = Console(stderr=True)

    def report(done: int, total: int | None, message: str) -> None:
        suffix = f"/{total}" if total else ""
        console.print(f"{label} {done}{suffix}: {message}", end="\r", highlight=False)

    return report


SUBDIRS = [
    "incoming", "library", "quarantine", "duplicates", "covers",
    "cache/openlibrary", "cache/douban", "reports", "db", "metadata", "derived",
]


@app.callback()
def cli() -> None:
    """Organize large collections of EPUB/PDF ebooks."""


@app.command()
def init(root: Path) -> None:
    """Create the directory layout, config.yaml, and database."""
    root = root.resolve()
    for sub in SUBDIRS:
        (root / sub).mkdir(parents=True, exist_ok=True)
    cfg = default_config(root)
    if not (root / "config.yaml").exists():
        save_config(cfg, root)
    with Database(cfg.database.path) as db:
        db.init_schema()
    typer.echo(f"initialized {root}")


@app.command()
def scan(
    path: Optional[Path] = typer.Argument(None),
    root: Path = ROOT_OPTION,
) -> None:
    """Discover ebook files and record them incrementally."""
    cfg = load_config(root)
    target = path or cfg.library.incoming
    with Database(cfg.database.path) as db:
        db.init_schema()
        r = pipeline.scan(cfg, db, SidecarStore(cfg), Path(target), _bar("Scanning"))
    typer.echo(
        f"seen={r['seen']} added={r['added']} changed={r['changed']}"
        f" duplicates={r['duplicates']}"
    )


@app.command()
def extract(
    root: Path = ROOT_OPTION,
    force: bool = typer.Option(False, "--force", help="Re-extract ERROR/IDENTIFIED files"),
) -> None:
    """Extract embedded metadata from scanned files."""
    cfg = load_config(root)
    with Database(cfg.database.path) as db:
        db.init_schema()
        r = pipeline.extract(cfg, db, SidecarStore(cfg), force, _bar("Extracting"))
    typer.echo(f"extracted={r['extracted']} errors={r['errors']}")


@app.command()
def match(
    root: Path = ROOT_OPTION,
    offline: bool = typer.Option(False, "--offline", help="Use only the local cache"),
    retry_unresolved: bool = typer.Option(
        False, "--retry-unresolved", help="Also re-match files left UNRESOLVED"
    ),
) -> None:
    """Match identified files against Open Library and Douban."""
    cfg = load_config(root)
    with Database(cfg.database.path) as db:
        try:
            counts = pipeline.match(
                cfg, db, SidecarStore(cfg), offline, _bar("Matching"), retry_unresolved
            )
        except RuntimeError as e:
            typer.echo(str(e), err=True)
            raise typer.Exit(1)
    typer.echo(" ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "nothing to match")


@app.command()
def resolve(
    root: Path = ROOT_OPTION,
    limit: int = typer.Option(0, "--limit", help="Max files to process (0 = all)"),
    include_unresolved: bool = typer.Option(
        False, "--include-unresolved", help="Also retry UNRESOLVED files"
    ),
) -> None:
    """Ask Claude to judge ambiguous matches (review queue) using cached candidates."""
    cfg = load_config(root)
    with Database(cfg.database.path) as db:
        try:
            result = pipeline.resolve(
                cfg, db, SidecarStore(cfg), limit, include_unresolved, _bar("Resolving")
            )
        except pipeline.AIDisabledError:
            typer.echo("ai.provider is not set in config.yaml", err=True)
            raise typer.Exit(1)
    typer.echo(
        " ".join(f"{k}={v}" for k, v in sorted(result.items())) or "nothing to resolve"
    )


@app.command()
def report(
    root: Path = ROOT_OPTION,
    as_json: bool = typer.Option(False, "--json", help="Print raw JSON"),
) -> None:
    """Summarize library processing state."""
    cfg = load_config(root)
    with Database(cfg.database.path) as db:
        rep = build_report(db)
    if as_json:
        typer.echo(_json.dumps(rep, indent=2))
        return
    table = Table(title="reshelf report")
    table.add_column("Metric")
    table.add_column("Count", justify="right")
    for key, value in rep.items():
        table.add_row(key.replace("_", " "), f"{value:,}")
    Console().print(table)


@app.command()
def plan(root: Path = ROOT_OPTION) -> None:
    """Generate a reviewable dry-run plan (writes reports/plan-*.json only)."""
    cfg = load_config(root)
    with Database(cfg.database.path) as db:
        out = pipeline.plan(cfg, db, SidecarStore(cfg))
        n = len(_json.loads(out.read_text())["actions"])
    typer.echo(f"plan written: {out} ({n} actions). No files were modified.")


def _latest_plan(reports_dir: Path) -> Path | None:
    plans = sorted(reports_dir.glob("plan-*.json"))
    return plans[-1] if plans else None


@app.command()
def commit(
    root: Path = ROOT_OPTION,
    plan_file: Optional[Path] = typer.Option(None, "--plan", help="Plan file (default: latest)"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    quarantine: bool = typer.Option(
        False, "--quarantine", help="Also MOVE unresolved files into quarantine/"
    ),
    duplicates: bool = typer.Option(
        False, "--duplicates", help="Also MOVE binary duplicates into duplicates/"
    ),
) -> None:
    """Execute a plan: copy matched books into library/ (originals untouched)."""
    cfg = load_config(root)
    reports_dir = cfg.library.root / "reports"
    plan_path = plan_file or _latest_plan(reports_dir)
    if plan_path is None:
        typer.echo("no plan found; run `reshelf plan` first", err=True)
        raise typer.Exit(1)
    with Database(cfg.database.path) as db:
        journal = pipeline.commit(
            cfg,
            db,
            SidecarStore(cfg),
            plan_path,
            _bar("Committing"),
            dry_run=dry_run,
            do_quarantine=quarantine,
            do_duplicates=duplicates,
        )
    verb = "would perform" if dry_run else "performed"
    typer.echo(
        f"{verb}={len(journal['actions'])} skipped={len(journal['skipped'])}"
        + ("" if dry_run else f" journal=reports/commit-{journal['commit_id']}.json")
    )


@app.command()
def rollback(
    commit_id: str = typer.Argument(..., help="Commit id from the journal filename"),
    root: Path = ROOT_OPTION,
) -> None:
    """Undo a commit using its journal (removes copies, restores moves)."""
    cfg = load_config(root)
    with Database(cfg.database.path) as db:
        try:
            result = pipeline.rollback(
                cfg, db, SidecarStore(cfg), commit_id, _bar("Rolling back")
            )
        except FileNotFoundError as e:
            typer.echo(str(e), err=True)
            raise typer.Exit(1)
    typer.echo(f"reverted={result['reverted']} skipped={result['skipped']}")


@app.command("calibre-export")
def calibre_export(
    library: Path = typer.Option(..., "--library", help="Calibre library path (metadata.db lives here)"),
    root: Path = ROOT_OPTION,
    dry_run: bool = typer.Option(False, "--dry-run", help="Print what would be imported"),
) -> None:
    """Import committed books into a Calibre library with their matched metadata."""
    cfg = load_config(root)
    reports_dir = cfg.library.root / "reports"
    journal_files = sorted(reports_dir.glob("commit-*.json"))
    if not journal_files:
        typer.echo("no commit journal found; run `reshelf commit` first", err=True)
        raise typer.Exit(1)
    journals = [_json.loads(p.read_text()) for p in journal_files]

    with Database(cfg.database.path) as db:
        books = books_to_export(db, journals)
    if not books:
        typer.echo("no committed books found in library/", err=True)
        raise typer.Exit(1)

    console = Console()
    if dry_run:
        typer.echo(f"would import {len(books)} books into {library}")
        for b in books[:5]:
            typer.echo(f"  {b.title} / {b.authors or '?'} -> {Path(b.path).name}")
        if len(books) > 5:
            typer.echo(f"  ... and {len(books) - 5} more")
        return

    with console.status("importing...") as status:
        def progress(i: int, total: int, book) -> None:
            status.update(f"[{i}/{total}] {book.title[:60]}")

        result = export(books, str(library), on_progress=progress)

    typer.echo(
        f"added={len(result['added'])} skipped={len(result['skipped'])} "
        f"failed={len(result['failed'])}"
    )
    for f in result["failed"][:10]:
        typer.echo(f"  FAILED {Path(f['path']).name}: {f['error'][:120]}", err=True)


@app.command("migrate-json")
def migrate_json_cmd(root: Path = ROOT_OPTION) -> None:
    """One shot: write a JSON sidecar for every hashed file in the database."""
    from reshelf.store.bootstrap import migrate_json
    from reshelf.store.sidecar import SidecarStore

    cfg = load_config(root)
    with Database(cfg.database.path) as db:
        db.init_schema()
        n = migrate_json(db, SidecarStore(cfg), lambda *a: None)
    typer.echo(f"sidecars={n}")


@app.command()
def serve(
    root: Path = ROOT_OPTION,
    host: Optional[str] = typer.Option(None, "--host"),
    port: Optional[int] = typer.Option(None, "--port"),
) -> None:
    """Run the web app."""
    import uvicorn

    from reshelf.db.database import LockError
    from reshelf.web.app import create_app

    cfg = load_config(root)
    try:
        fastapi_app = create_app(root)
    except LockError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1)
    # create_app already took db.lock. If uvicorn never gets as far as
    # running the lifespan - a port already in use is the everyday case -
    # nothing else would release it, and the next `reshelf <anything>`
    # refuses to start against a lock file no process holds. Database.close
    # is idempotent, so the normal shutdown path closing it first is fine.
    try:
        uvicorn.run(
            fastapi_app,
            host=host or cfg.web.host,
            port=port or cfg.web.port,
        )
    finally:
        fastapi_app.state.reshelf.db.close()


@app.command()
def reindex(root: Path = ROOT_OPTION) -> None:
    """Rebuild the derived search index from the sidecars."""
    from reshelf.store.bootstrap import reindex as _reindex
    from reshelf.store.sidecar import SidecarStore

    cfg = load_config(root)
    with Database(cfg.database.path) as db:
        db.init_schema()
        n = _reindex(db, SidecarStore(cfg), lambda *a: None)
    typer.echo(f"indexed={n}")


@app.command()
def covers(root: Path = ROOT_OPTION) -> None:
    """Extract missing covers for every book (EPUB/PDF files only)."""
    from reshelf.covers import ensure_cover

    cfg = load_config(root)
    covers_dir = Path(cfg.library.root) / "covers"
    library_dir = Path(cfg.library.root) / "library"
    done = missing = 0
    for book in SidecarStore(cfg).iter_all():
        primary = book.primary_file(library_dir)
        files = [primary] + [f for f in book.files if f is not primary] if primary else []
        if any(Path(f.path).is_file() and ensure_cover(covers_dir, book.sha256, Path(f.path)) for f in files):
            done += 1
        else:
            missing += 1
    typer.echo(f"covers={done} none={missing}")


def main() -> None:
    """Entry point. One `serve` holds db.lock all day, so every other
    command meeting it is routine - report it instead of a traceback."""
    from reshelf.db.database import LockError

    try:
        app()
    except LockError as e:
        typer.echo(str(e), err=True)
        raise SystemExit(1) from None
