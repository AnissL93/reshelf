# book-organizer

Organize large collections of EPUB/PDF ebooks: scan, extract metadata,
match against Open Library, detect duplicates, and produce a reviewable
organization plan — without ever modifying your original files.

See `spec.md` for the full specification. Current status: MVP (spec §33) —
no AI resolution, no file mutation; `plan` is the terminal step.

## Install

Requires Python ≥ 3.11.

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
```

The `book-organizer` command is then available at `.venv/bin/book-organizer`
(or on PATH with the venv activated).

## Usage

### 1. Initialize a library root

```bash
book-organizer init /mnt/data/Books
```

Creates the directory layout (`incoming/ library/ quarantine/ duplicates/
covers/ cache/ reports/ db/`), a `config.yaml`, and the SQLite database.
Idempotent — safe to re-run.

Drop your ebooks into `/mnt/data/Books/incoming/`.

All later commands take `--root /mnt/data/Books` (defaults to the current
directory, so you can also just `cd` into the library root).

### 2. Scan

```bash
book-organizer scan --root /mnt/data/Books          # scans incoming/
book-organizer scan /some/other/dir --root /mnt/data/Books
```

Discovers `.epub`/`.pdf` files, records path/size/mtime, and hashes new or
changed files (SHA256). Byte-identical files are marked DUPLICATE.
Incremental: unchanged files are skipped, and an interrupted scan can
simply be re-run.

```text
seen=4863 added=4863 changed=0 duplicates=211
```

### 3. Extract metadata

```bash
book-organizer extract --root /mnt/data/Books
book-organizer extract --root /mnt/data/Books --force   # re-try ERROR files too
```

Reads embedded metadata (EPUB OPF; PDF info plus an ISBN scan of the first
5 pages — no OCR). Unreadable files are marked ERROR and skipped until
`--force`.

### 4. Match against Open Library

```bash
book-organizer match --root /mnt/data/Books
book-organizer match --root /mnt/data/Books --offline   # local cache only, no network
```

Looks up each identified file by ISBN, falling back to title/author
search, then scores candidates deterministically (spec §14). Every match
stores its score, confidence, and evidence. Results land in confidence
bands: exact-ISBN and high-confidence matches become MATCHED; ambiguous
ones go to REVIEW; the rest stay UNRESOLVED. All API responses are cached
under `cache/openlibrary/` for 30 days.

### 5. Report

```bash
book-organizer report --root /mnt/data/Books
book-organizer report --root /mnt/data/Books --json
```

```text
Metric                Count
files scanned         4,863
exact isbn matches    2,184
high confidence       1,706
needs review             97
duplicates              211
unresolved               44
errors                    3
```

### 6. Generate a plan (dry-run)

```bash
book-organizer plan --root /mnt/data/Books
```

Writes `reports/plan-<id>.json` describing what *would* be done — `import`
for matched files, `mark_duplicate`, `quarantine` — each action carrying
`preconditions` (sha256/size/mtime) so a future commit can verify nothing
changed since planning. **This is the last step in the MVP: no command
modifies, moves, or deletes your ebook files.**

## Configuration

`config.yaml` in the library root (created by `init`). Notable settings:

```yaml
matching:            # confidence bands, see spec §15
  auto_accept: 0.98
  review_below: 0.9
  ai_resolve_below: 0.75
  unresolved_below: 0.5
scan:
  formats: [epub, pdf]
  recursive: true
cache:
  ttl_days: 30
```

Only one `book-organizer` process may run against a library at a time
(enforced via `db/.lock`; delete the lock file if a process crashed).

## Development

```bash
.venv/bin/pytest        # run the test suite
```

Planned next (spec Phase 2): AI `resolve`, review TUI, `commit`/`rollback`,
Calibre import, and additional metadata providers (Google Books, Crossref,
Douban for Chinese-language coverage).
