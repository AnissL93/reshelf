from pathlib import Path
from typing import Optional

import typer

from book_organizer.config import default_config, load_config, save_config
from book_organizer.db.database import Database
from book_organizer.extractors.base import ExtractionError
from book_organizer.extractors.epub import extract_epub
from book_organizer.extractors.pdf import extract_pdf
from book_organizer.metadata.isbn import find_isbns
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


_EXTRACTORS = {"epub": extract_epub, "pdf": extract_pdf}


@app.command()
def extract(
    root: Path = ROOT_OPTION,
    force: bool = typer.Option(False, "--force", help="Re-extract ERROR/IDENTIFIED files"),
) -> None:
    """Extract embedded metadata from scanned files."""
    cfg = load_config(root)
    statuses = ["SCANNED"] + (["ERROR", "IDENTIFIED"] if force else [])
    done = errors = 0
    with Database(cfg.database.path) as db:
        rows = [r for s in statuses for r in db.files_with_status(s)]
        for row in rows:
            extractor = _EXTRACTORS.get(row["format"])
            path = Path(row["path"])
            try:
                if extractor is None:
                    raise ExtractionError(f"unsupported format {row['format']}")
                meta = extractor(path)
            except ExtractionError:
                db.set_status(row["id"], "ERROR")
                errors += 1
                continue
            isbns = meta.isbns or find_isbns(path.name)
            db.set_raw_metadata(
                row["id"],
                title=meta.title,
                author="; ".join(meta.authors) or None,
                isbn=isbns[0] if isbns else None,
                language=meta.language,
            )
            db.set_status(row["id"], "IDENTIFIED")
            done += 1
        db.conn.commit()
    typer.echo(f"extracted={done} errors={errors}")


def main() -> None:
    app()
