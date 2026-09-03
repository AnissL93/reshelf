import json as _json
import re
from pathlib import Path
from typing import Optional

import httpx
import typer
from rich.console import Console
from rich.table import Table

from book_organizer import __version__
from book_organizer.ai.resolver import AIError, ClaudeCLIResolver
from book_organizer.config import default_config, load_config, save_config
from book_organizer.matching.scorer import (
    LocalBook,
    band,
    confidence_from_score,
    same_work,
    score_candidate,
)
from book_organizer.planner.committer import apply_plan, rollback_journal
from book_organizer.planner.planner import generate_plan
from book_organizer.providers.cache import FileCache
from book_organizer.providers.douban import DoubanProvider
from book_organizer.providers.openlibrary import OpenLibraryProvider
from book_organizer.reports.report import build_report
from book_organizer.db.database import Database
from book_organizer.extractors.base import ExtractionError
from book_organizer.extractors.epub import extract_epub
from book_organizer.extractors.pdf import extract_pdf
from book_organizer.metadata.isbn import find_isbns
from book_organizer.metadata.normalization import (
    search_author,
    short_title,
    title_from_filename,
)
from book_organizer.scanner.hashing import sha256_file
from book_organizer.scanner.scanner import iter_files

app = typer.Typer(no_args_is_help=True)

ROOT_OPTION = typer.Option(Path("."), "--root", help="Library root (with config.yaml)")

SUBDIRS = [
    "incoming", "library", "quarantine", "duplicates", "covers",
    "cache/openlibrary", "cache/douban", "reports", "db",
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


_HAS_CJK = re.compile(r"[一-鿿]")


def _order_providers(providers: list, local: LocalBook) -> list:
    """Chinese-looking books query Douban first; everything else Open Library."""
    text = (local.title or "") + " ".join(local.authors)
    if local.language == "zh" or _HAS_CJK.search(text):
        return sorted(providers, key=lambda p: p.name != "douban")
    return providers


def _gather_candidates(providers: list, local: LocalBook) -> list:
    for provider in providers:
        found = []
        for isbn in local.isbn13s:
            found += provider.lookup_isbn(isbn)
        if found:
            return found
    for provider in providers:
        if not local.title:
            break
        title_q = short_title(local.title)
        author_q = search_author(local.authors[0]) if local.authors else None
        found = provider.search(title_q, author_q)
        if not found and author_q:
            found = provider.search(title_q)
        if found:
            return found
    return []


def _local_book(row) -> LocalBook:
    return LocalBook(
        title=row["title_raw"] or title_from_filename(Path(row["path"]).stem),
        authors=[a.strip() for a in (row["author_raw"] or "").split(";") if a.strip()],
        isbn13s=[row["isbn_raw"]] if row["isbn_raw"] else [],
        language=row["language_raw"],
    )


def _build_providers(cfg, client) -> list:
    providers = []
    if cfg.providers.openlibrary.enabled:
        providers.append(
            OpenLibraryProvider(
                client=client,
                cache=FileCache(
                    cfg.library.root / "cache" / "openlibrary", cfg.cache.ttl_days
                ),
            )
        )
    if cfg.providers.douban.enabled:
        providers.append(
            DoubanProvider(
                client=client,
                cache=FileCache(
                    cfg.library.root / "cache" / "douban", cfg.cache.ttl_days
                ),
                apikey=cfg.providers.douban.apikey,
            )
        )
    return providers


def _match_file(db, providers, mcfg, row) -> str:
    local = _local_book(row)
    candidates = _gather_candidates(_order_providers(providers, local), local)
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
        and not same_work(best, candidates[1])
    ):
        b = "REVIEW_RECOMMENDED"
        best.evidence.append("ambiguous:tie")
    for provider in providers:
        if provider.name == best.provider:
            best = provider.enrich(best)
            break
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
    client = None if offline else httpx.Client(
        headers={"User-Agent": f"book-organizer/{__version__}"}
    )
    providers = _build_providers(cfg, client)
    if not providers:
        typer.echo("all providers disabled in config", err=True)
        raise typer.Exit(1)
    counts: dict[str, int] = {}
    try:
        with Database(cfg.database.path) as db:
            rows = db.files_with_status("IDENTIFIED")
            total = len(rows)
            for i, row in enumerate(rows, 1):
                try:
                    status = _match_file(db, providers, cfg.matching, row)
                except httpx.HTTPError as e:
                    # leave the file IDENTIFIED so a later run retries it
                    status = "NETWORK_ERROR"
                    typer.echo(f"network error on {row['path']}: {e}", err=True)
                counts[status] = counts.get(status, 0) + 1
                db.conn.commit()
                if i % 25 == 0 or i == total:
                    typer.echo(
                        f"[{i}/{total}] "
                        + " ".join(f"{k}={v}" for k, v in sorted(counts.items()))
                    )
    finally:
        if client is not None:
            client.close()
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
    if not cfg.ai.enabled:
        typer.echo("ai disabled in config", err=True)
        raise typer.Exit(1)
    resolver = ClaudeCLIResolver(model=cfg.ai.model, timeout=cfg.ai.timeout_seconds)
    providers = _build_providers(cfg, client=None)  # cache-only candidate regathering
    statuses = ["REVIEW"] + (["UNRESOLVED"] if include_unresolved else [])
    counts: dict[str, int] = {}

    def bump(key: str) -> None:
        counts[key] = counts.get(key, 0) + 1

    with Database(cfg.database.path) as db:
        rows = [r for s in statuses for r in db.files_with_status(s)]
        if limit:
            rows = rows[:limit]
        total = len(rows)
        for i, row in enumerate(rows, 1):
            local = _local_book(row)
            candidates = _gather_candidates(_order_providers(providers, local), local)
            if not candidates:
                bump("NO_CANDIDATES")
                continue
            for cand in candidates:
                cand.score, cand.evidence = score_candidate(local, cand)
            try:
                decision = resolver.resolve(
                    {
                        "filename": Path(row["path"]).name,
                        "title": local.title,
                        "authors": local.authors,
                        "isbn13": local.isbn13s[0] if local.isbn13s else None,
                        "language": local.language,
                    },
                    candidates,
                )
            except AIError as e:
                bump("AI_ERROR")
                typer.echo(f"AI error on {row['path']}: {e}", err=True)
                continue
            if decision.decision is None:
                db.set_status(row["id"], "UNRESOLVED")
                bump("UNRESOLVED")
            else:
                best = candidates[decision.decision]
                conf = min(max(decision.confidence, 0.0), 0.97)  # never AUTO_ACCEPT
                b = band(conf, cfg.matching)
                if b == "AUTO_ACCEPT":
                    b = "HIGH_CONFIDENCE"
                evidence = (
                    best.evidence
                    + [f"ai:{r}" for r in decision.reasons]
                    + [f"ai_uncertain:{u}" for u in decision.uncertainties]
                )
                for provider in providers:
                    if provider.name == best.provider:
                        best = provider.enrich(best)
                        break
                edition_id = db.save_candidate(best)
                db.record_match(
                    row["id"], edition_id, best.score, conf, "ai", evidence, b
                )
                status = _BAND_TO_STATUS[b]
                db.set_file_match(
                    row["id"],
                    edition_id if status == "MATCHED" else None,
                    conf,
                    status,
                )
                bump(status)
            db.conn.commit()
            if i % 10 == 0 or i == total:
                typer.echo(
                    f"[{i}/{total}] "
                    + " ".join(f"{k}={v}" for k, v in sorted(counts.items()))
                )
    typer.echo(" ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "nothing to resolve")


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
        typer.echo("no plan found; run `book-organizer plan` first", err=True)
        raise typer.Exit(1)
    plan_data = _json.loads(Path(plan_path).read_text())
    with Database(cfg.database.path) as db:
        journal = apply_plan(
            plan_data,
            db,
            library_dir=cfg.library.root / "library",
            quarantine_dir=cfg.library.quarantine,
            duplicates_dir=cfg.library.root / "duplicates",
            reports_dir=reports_dir,
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
    journal_path = cfg.library.root / "reports" / f"commit-{commit_id}.json"
    if not journal_path.exists():
        typer.echo(f"no journal at {journal_path}", err=True)
        raise typer.Exit(1)
    journal = _json.loads(journal_path.read_text())
    with Database(cfg.database.path) as db:
        result = rollback_journal(journal, db, cfg.library.root / "library")
    typer.echo(f"reverted={result['reverted']} skipped={result['skipped']}")


def main() -> None:
    app()
