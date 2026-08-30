import json as _json
from pathlib import Path
from typing import Optional

import httpx
import typer
from rich.console import Console
from rich.table import Table

from book_organizer import __version__
from book_organizer.config import default_config, load_config, save_config
from book_organizer.matching.scorer import (
    LocalBook,
    band,
    confidence_from_score,
    score_candidate,
)
from book_organizer.planner.planner import generate_plan
from book_organizer.providers.cache import FileCache
from book_organizer.providers.openlibrary import OpenLibraryProvider
from book_organizer.reports.report import build_report
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


_BAND_TO_STATUS = {
    "AUTO_ACCEPT": "MATCHED",
    "HIGH_CONFIDENCE": "MATCHED",
    "REVIEW_RECOMMENDED": "REVIEW",
    "AI_RESOLUTION": "REVIEW",
    "UNRESOLVED": "UNRESOLVED",
}


def _match_file(db, provider, mcfg, row) -> str:
    local = LocalBook(
        title=row["title_raw"] or Path(row["path"]).stem,
        authors=[a.strip() for a in (row["author_raw"] or "").split(";") if a.strip()],
        isbn13s=[row["isbn_raw"]] if row["isbn_raw"] else [],
        language=row["language_raw"],
    )
    candidates = []
    for isbn in local.isbn13s:
        candidates += provider.lookup_isbn(isbn)
    if not candidates and local.title:
        candidates += provider.search(
            local.title, local.authors[0] if local.authors else None
        )
    for cand in candidates:
        cand.score, cand.evidence = score_candidate(local, cand)
        cand.confidence = confidence_from_score(cand.score, cand.evidence)
    candidates.sort(key=lambda c: c.score, reverse=True)
    if not candidates:
        db.set_status(row["id"], "UNRESOLVED")
        return "UNRESOLVED"
    best = candidates[0]
    b = band(best.confidence, mcfg)
    if (
        len(candidates) > 1
        and best.score - candidates[1].score < 10
        and b in ("AUTO_ACCEPT", "HIGH_CONFIDENCE")
    ):
        b = "REVIEW_RECOMMENDED"
        best.evidence.append("ambiguous:tie")
    edition_id = db.save_candidate(best)
    db.record_match(
        row["id"], edition_id, best.score, best.confidence,
        "deterministic", best.evidence, b,
    )
    status = _BAND_TO_STATUS[b]
    db.set_file_match(
        row["id"],
        edition_id if status == "MATCHED" else None,
        best.confidence,
        status,
    )
    return status


@app.command()
def match(
    root: Path = ROOT_OPTION,
    offline: bool = typer.Option(False, "--offline", help="Use only the local cache"),
) -> None:
    """Match identified files against Open Library."""
    cfg = load_config(root)
    if not cfg.providers.openlibrary.enabled:
        typer.echo("openlibrary provider disabled in config", err=True)
        raise typer.Exit(1)
    cache = FileCache(cfg.library.root / "cache" / "openlibrary", cfg.cache.ttl_days)
    client = None if offline else httpx.Client(
        headers={"User-Agent": f"book-organizer/{__version__}"}
    )
    provider = OpenLibraryProvider(client=client, cache=cache)
    counts: dict[str, int] = {}
    try:
        with Database(cfg.database.path) as db:
            for row in db.files_with_status("IDENTIFIED"):
                status = _match_file(db, provider, cfg.matching, row)
                counts[status] = counts.get(status, 0) + 1
                db.conn.commit()
    finally:
        if client is not None:
            client.close()
    typer.echo(" ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "nothing to match")


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
    table = Table(title="book-organizer report")
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
        out = generate_plan(db, cfg.library.root / "reports")
        n = len(_json.loads(out.read_text())["actions"])
    typer.echo(f"plan written: {out} ({n} actions). No files were modified.")


def main() -> None:
    app()
