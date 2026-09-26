"""Pipeline stages as plain functions.

Command bodies used to live in cli.py, where nothing else could call
them. Everything here takes an open Database and a progress callback, so
the CLI and the web job runner share one implementation. The callback
raises JobCancelled to stop a run cooperatively.
"""

import re
from collections.abc import Callable
from pathlib import Path

import httpx

from reshelf import __version__
from reshelf.ai.resolver import AIError, build_resolver
from reshelf.config import Config
from reshelf.db.database import Database
from reshelf.extractors.base import ExtractionError
from reshelf.extractors.epub import extract_epub
from reshelf.extractors.mobi import extract_mobi
from reshelf.extractors.pdf import extract_pdf
from reshelf.matching.scorer import (
    LocalBook,
    band,
    confidence_from_score,
    same_work,
    score_candidate,
)
from reshelf.metadata.isbn import find_isbns
from reshelf.metadata.models import Candidate
from reshelf.metadata.normalization import search_author, short_title, title_from_filename
from reshelf.providers.cache import FileCache
from reshelf.providers.douban import DoubanProvider
from reshelf.providers.openlibrary import OpenLibraryProvider
from reshelf.scanner.hashing import sha256_file
from reshelf.scanner.scanner import iter_files
from reshelf.store import index
from reshelf.store.models import Book, FileEntry, now
from reshelf.store.sidecar import SidecarStore

Progress = Callable[[int, int | None, str], None]


class JobCancelled(Exception):
    """Raised by a progress callback to stop a running stage."""


EXTRACTORS = {
    "epub": extract_epub,
    "pdf": extract_pdf,
    "mobi": extract_mobi,
    "azw": extract_mobi,
    "azw3": extract_mobi,
}


def scan(
    cfg: Config, db: Database, store: SidecarStore, target: Path, progress: Progress
) -> dict:
    seen = added = changed = duplicates = 0
    run_id = db.start_scan_run(str(target))
    for fi in iter_files(Path(target), cfg.scan.formats, cfg.scan.recursive):
        seen += 1
        progress(seen, None, fi.path)
        previous = db.conn.execute(
            "SELECT sha256 FROM files WHERE path = ?", (fi.path,)
        ).fetchone()
        old_sha = previous["sha256"] if previous else None

        fid, state = db.upsert_file(fi.path, fi.size, fi.mtime, fi.extension)
        if state == "unchanged":
            continue
        added += state == "new"
        changed += state == "changed"
        new_sha = sha256_file(Path(fi.path))
        if db.set_hash(fid, new_sha):
            duplicates += 1
        if old_sha and old_sha != new_sha:
            _carry_sidecar(db, store, old_sha, new_sha, fi)
        db.conn.commit()
    db.finish_scan_run(run_id, seen, added, changed)
    db.conn.commit()
    return {"seen": seen, "added": added, "changed": changed, "duplicates": duplicates}


def _carry_sidecar(db, store, old_sha: str, new_sha: str, fi) -> None:
    """A file's content changed at a known path - keep its metadata.

    # ponytail: keyed on the path staying the same. Proper edition linking
    # only if re-downloads with renames turn out to be common.
    """
    old = store.load(old_sha, fi.path)
    if old is None:
        return
    # Under "hash" layout, load(new_sha, ...) genuinely looks up the new
    # hash's own file, so a hit here means one already exists - don't
    # clobber it. Under "sidecar"/"library" layout, path_for ignores the
    # sha argument entirely, so this load returns the same not-yet-rewritten
    # sidecar as `old` above; checking its own .sha256 tells them apart.
    existing = store.load(new_sha, fi.path)
    if existing is not None and existing.sha256 == new_sha:
        return
    carried = old.model_copy(deep=True)
    carried.sha256 = new_sha
    for entry in carried.files:
        if entry.path == fi.path:
            entry.size, entry.mtime = fi.size, fi.mtime
    carried.files = [f for f in carried.files if f.role != "converted"]

    old_path = store.path_for(old_sha, fi.path)
    new_path = store.path_for(new_sha, fi.path)
    store.save(carried, fi.path)

    # Duplicates share a hash. If another path still carries old_sha (set_hash
    # has already recorded new_sha for this path, so this only matches other
    # files), its sidecar and index row are still legitimately in use.
    still_used = db.conn.execute(
        "SELECT 1 FROM files WHERE sha256 = ? AND path != ? LIMIT 1",
        (old_sha, fi.path),
    ).fetchone()
    # Under "sidecar"/"library" layout old_path == new_path: deleting after
    # save would destroy the file we just wrote.
    if not still_used and old_path != new_path:
        store.delete(old_sha, fi.path)
    if not still_used:
        index.remove(db.conn, old_sha)
    index.sync(db.conn, carried)


def extract(
    cfg: Config, db: Database, store: SidecarStore, force: bool, progress: Progress
) -> dict:
    statuses = ["SCANNED"] + (["ERROR", "IDENTIFIED"] if force else [])
    rows = [r for s in statuses for r in db.files_with_status(s)]
    total = len(rows)
    done = errors = 0
    for n, row in enumerate(rows, 1):
        progress(n, total, row["path"])
        path = Path(row["path"])
        extractor = EXTRACTORS.get(row["format"])
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
        _write_extracted_sidecar(db, store, row, meta, isbns)
        done += 1
    db.conn.commit()
    return {"extracted": done, "errors": errors}


def _write_extracted_sidecar(db, store, row, meta, isbns) -> None:
    """Every hashed file gets a sidecar, even an empty one - see Task 4."""
    sha = row["sha256"]
    if not sha:
        return
    book = store.load(sha, row["path"]) or Book(sha256=sha)
    if book.is_human:
        return
    if not any(f.path == row["path"] for f in book.files):
        book.files.append(
            FileEntry(
                path=row["path"],
                format=row["format"] or "",
                size=row["size"] or 0,
                mtime=row["mtime"] or 0,
            )
        )
    if book.source.resolver in ("embedded",):
        book.metadata.title = meta.title or book.metadata.title
        if meta.authors:
            book.metadata.authors = list(meta.authors)
        book.metadata.language = meta.language or book.metadata.language
        if isbns:
            book.metadata.isbn13 = isbns[0]
    store.save(book, row["path"])
    index.sync(db.conn, book)


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


def build_providers(cfg: Config, client) -> list:
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


def _match_file(db: Database, providers: list, mcfg, row) -> str:
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


class AIDisabledError(RuntimeError):
    """Raised when an AI path is requested but ai.provider is unset."""


def match(
    cfg: Config, db: Database, store: SidecarStore, offline: bool, progress: Progress
) -> dict[str, int]:
    client = (
        None if offline
        else httpx.Client(headers={"User-Agent": f"reshelf/{__version__}"})
    )
    providers = build_providers(cfg, client)
    if not providers:
        raise RuntimeError("all providers disabled in config")
    counts: dict[str, int] = {}
    try:
        rows = db.files_with_status("IDENTIFIED")
        total = len(rows)
        for i, row in enumerate(rows, 1):
            progress(i, total, row["path"])
            book = store.load(row["sha256"], row["path"]) if row["sha256"] else None
            if book is not None and book.is_human:
                counts["SKIPPED_HUMAN"] = counts.get("SKIPPED_HUMAN", 0) + 1
                continue
            try:
                status = _match_file(db, providers, cfg.matching, row)
            except httpx.HTTPError:
                # leave the file IDENTIFIED so a later run retries it
                status = "NETWORK_ERROR"
            counts[status] = counts.get(status, 0) + 1
            if status == "MATCHED" and book is not None:
                _sync_matched_sidecar(db, store, row, book)
            db.conn.commit()
    finally:
        if client is not None:
            client.close()
    return counts


def _sync_matched_sidecar(
    db: Database,
    store: SidecarStore,
    row,
    book: Book,
    resolver: str = "deterministic",
) -> None:
    """Write the just-matched edition's metadata into the book's sidecar.

    Reuses `Database.edition_metadata`, which aggregates ALL co-authors -
    a hand-rolled `... LIMIT 1` author join here silently drops co-authors.
    """
    frow = db.conn.execute(
        "SELECT matched_edition_id, match_confidence FROM files WHERE id = ?",
        (row["id"],),
    ).fetchone()
    if frow is None or frow["matched_edition_id"] is None:
        return
    edition = db.edition_metadata(frow["matched_edition_id"])
    if edition is None:
        return
    m = book.metadata
    m.title = edition["title"] or m.title
    authors = [a.strip() for a in (edition["authors"] or "").split(";") if a.strip()]
    if authors:
        m.authors = authors
    m.isbn13 = edition["isbn13"] or m.isbn13
    m.isbn10 = edition["isbn10"] or m.isbn10
    m.publisher = edition["publisher"] or m.publisher
    m.pubdate = edition["publication_date"] or m.pubdate
    m.language = edition["language"] or m.language
    book.source.resolver = resolver
    book.source.confidence = frow["match_confidence"] or 0.0
    book.source.decided_at = now()
    store.save(book, row["path"])
    index.sync(db.conn, book)


def _row_for(db: Database, sha256: str):
    row = db.conn.execute(
        "SELECT * FROM files WHERE sha256 = ? ORDER BY id LIMIT 1", (sha256,)
    ).fetchone()
    if row is None:
        raise KeyError(sha256)
    return row


def match_one(
    cfg: Config,
    db: Database,
    store: SidecarStore,
    sha256: str,
    query: dict | None = None,
    use_ai: bool = False,
) -> list[Candidate]:
    """Rank candidates for one book. Never writes a decision - `choose` does that."""
    if use_ai and not cfg.ai.enabled:
        raise AIDisabledError("ai.provider is not configured")
    row = _row_for(db, sha256)
    local = _local_book(row)
    if query:
        local = LocalBook(
            title=query.get("title") or local.title,
            authors=[query["author"]] if query.get("author") else local.authors,
            isbn13s=[query["isbn"]] if query.get("isbn") else local.isbn13s,
            language=local.language,
            publisher=local.publisher,
            year=local.year,
        )
    client = httpx.Client(headers={"User-Agent": f"reshelf/{__version__}"})
    try:
        providers = build_providers(cfg, client)
        candidates = _gather_candidates(_order_providers(providers, local), local)
        for cand in candidates:
            cand.score, cand.evidence = score_candidate(local, cand)
            cand.confidence = confidence_from_score(cand.score, cand.evidence)
        candidates.sort(key=lambda c: c.score, reverse=True)
        for cand in candidates:
            cand.edition_id = db.save_candidate(cand)
        db.conn.commit()
    finally:
        client.close()
    if use_ai and candidates:
        candidates = _ai_rank(cfg, row, local, candidates)
    return candidates


def _ai_rank(
    cfg: Config, row, local: LocalBook, candidates: list[Candidate]
) -> list[Candidate]:
    """Ask the configured model to pick among real candidates. Never invents."""
    resolver = build_resolver(cfg.ai)
    local_payload = {
        "filename": Path(row["path"]).name,
        "title": local.title,
        "authors": local.authors,
        "isbn13": local.isbn13s[0] if local.isbn13s else None,
        "language": local.language,
    }
    try:
        decision = resolver.resolve(local_payload, candidates)
    except AIError:
        return candidates
    if decision.decision is None:
        return candidates
    chosen = candidates[decision.decision]
    rest = [c for i, c in enumerate(candidates) if i != decision.decision]
    return [chosen] + rest


def resolve(
    cfg: Config,
    db: Database,
    store: SidecarStore,
    limit: int,
    include_unresolved: bool,
    progress: Progress,
) -> dict:
    """Ask the configured AI model to judge ambiguous matches (review queue)."""
    if not cfg.ai.enabled:
        raise AIDisabledError("ai.provider is not configured")
    resolver = build_resolver(cfg.ai)
    providers = build_providers(cfg, client=None)  # cache-only candidate regathering
    statuses = ["REVIEW"] + (["UNRESOLVED"] if include_unresolved else [])
    resolved = skipped = errors = 0
    rows = [r for s in statuses for r in db.files_with_status(s)]
    if limit:
        rows = rows[:limit]
    total = len(rows)
    for i, row in enumerate(rows, 1):
        progress(i, total, row["path"])
        book = store.load(row["sha256"], row["path"]) if row["sha256"] else None
        if book is not None and book.is_human:
            skipped += 1
            continue
        local = _local_book(row)
        candidates = _gather_candidates(_order_providers(providers, local), local)
        if not candidates:
            skipped += 1
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
        except AIError:
            errors += 1
            continue
        if decision.decision is None:
            db.set_status(row["id"], "UNRESOLVED")
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
            db.record_match(row["id"], edition_id, best.score, conf, "ai", evidence, b)
            status = _BAND_TO_STATUS[b]
            db.set_file_match(
                row["id"], edition_id if status == "MATCHED" else None, conf, status,
            )
            if status == "MATCHED" and book is not None:
                _sync_matched_sidecar(db, store, row, book, resolver="ai")
        resolved += 1
        db.conn.commit()
    return {"resolved": resolved, "skipped": skipped, "errors": errors}


def choose(
    cfg: Config, db: Database, store: SidecarStore, sha256: str, candidate_id: int
) -> Book:
    """Commit a human's pick as the sticky decision for a book.

    Reuses `Database.edition_metadata` for the same reason `_sync_matched_sidecar`
    does: a hand-rolled `... LIMIT 1` author join silently drops co-authors.
    """
    row = _row_for(db, sha256)
    edition = db.edition_metadata(candidate_id)
    if edition is None:
        raise KeyError(candidate_id)
    authors = [a.strip() for a in (edition["authors"] or "").split(";") if a.strip()]
    book = store.load(sha256, row["path"]) or Book(sha256=sha256)
    m = book.metadata
    m.title = edition["title"] or m.title
    if authors:
        m.authors = authors
    m.isbn13 = edition["isbn13"] or m.isbn13
    m.isbn10 = edition["isbn10"] or m.isbn10
    m.publisher = edition["publisher"] or m.publisher
    m.pubdate = edition["publication_date"] or m.pubdate
    m.language = edition["language"] or m.language
    book.source.resolver = "human"
    book.source.confidence = 1.0
    book.source.decided_at = now()
    store.save(book, row["path"])
    db.record_match(row["id"], candidate_id, 100.0, 1.0, "human", [], "AUTO_ACCEPT")
    db.set_file_match(row["id"], candidate_id, 1.0, "MATCHED")
    db.conn.commit()
    index.sync(db.conn, book)
    return book
