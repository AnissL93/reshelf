from pathlib import Path
from typing import Optional

import typer

from book_organizer.config import default_config, load_config, save_config
from book_organizer.db.database import Database
from book_organizer.scanner.hashing import sha256_file
from book_organizer.scanner.scanner import iter_files

app = typer.Typer(no_args_is_help=True)

ROOT_OPTION = typer.Option(Path("."), "--root", help="Library root (with config.yaml)")

SUBDIRS = [
    "incoming", "library", "quarantine", "duplicates", "covers",
    "cache/openlibrary", "reports", "db",
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
    seen = added = changed = dups = 0
    with Database(cfg.database.path) as db:
        run_id = db.start_scan_run(str(target))
        for fi in iter_files(Path(target), cfg.scan.formats, cfg.scan.recursive):
            seen += 1
            fid, state = db.upsert_file(fi.path, fi.size, fi.mtime, fi.extension)
            if state == "unchanged":
                continue
            added += state == "new"
            changed += state == "changed"
            if db.set_hash(fid, sha256_file(Path(fi.path))):
                dups += 1
            db.conn.commit()
        db.finish_scan_run(run_id, seen, added, changed)
        db.conn.commit()
    typer.echo(f"seen={seen} added={added} changed={changed} duplicates={dups}")


def main() -> None:
    app()
