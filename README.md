# book-organizer

Organize large collections of EPUB/PDF ebooks: scan, extract metadata,
match against Open Library, detect duplicates, and produce a reviewable
organization plan — without ever modifying your original files.

See `spec.md` for the full specification. Pipeline: scan → extract →
match (Douban + Open Library) → AI resolve → report → plan → commit,
with rollback. The only write steps are `commit` (copies matched books
into `library/`; moves are opt-in flags) and `rollback` (undoes a commit).

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

### 4. Match against Open Library and Douban

```bash
book-organizer match --root /mnt/data/Books
book-organizer match --root /mnt/data/Books --offline   # local cache only, no network
```

Looks up each identified file by ISBN, falling back to title/author
search, then scores candidates deterministically (spec §14). Books with
Chinese titles/authors query Douban first, everything else Open Library
first. Every match stores its score, confidence, and evidence. Results
land in confidence bands: exact-ISBN and high-confidence matches become
MATCHED; ambiguous ones go to REVIEW; the rest stay UNRESOLVED. All API
responses are cached under `cache/` for 30 days.

### 5. AI-resolve ambiguous matches (optional)

```bash
book-organizer resolve --root /mnt/data/Books
book-organizer resolve --root /mnt/data/Books --limit 20            # sample first
book-organizer resolve --root /mnt/data/Books --include-unresolved  # also retry UNRESOLVED
```

Asks Claude (via the `claude` CLI — uses your Claude Code subscription, no
API key needed) to judge books the deterministic matcher left in REVIEW:
translated titles, transliterated authors, marketing-subtitle noise. The
AI only picks among real provider candidates — it can never invent ISBNs
or metadata — its confidence is capped below auto-accept, and its
reasoning is stored in each match's evidence trail. Model is configurable
(`ai.model` in config.yaml, default `haiku`).

### 6. Report

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

### 7. Generate a plan

```bash
book-organizer plan --root /mnt/data/Books
```

Writes `reports/plan-<id>.json` describing what commit *would* do —
`import` for matched files, `mark_duplicate`, `quarantine` — each action
carrying `preconditions` (sha256/size/mtime) so commit can verify nothing
changed since planning. Planning itself never touches your files.

### 8. Commit the plan

```bash
book-organizer commit --root /mnt/data/Books --dry-run   # preview
book-organizer commit --root /mnt/data/Books             # execute imports
```

Executes the latest plan (or `--plan PATH`). Matched books are **copied**
(never moved) into `library/{author}/{title} ({year})/{title}.ext`;
originals stay exactly where they are. Each file's sha256/size/mtime is
re-verified first — anything changed since planning is skipped and
reported. Re-running is safe: already-committed books are skipped.

Two action types genuinely relocate files and are therefore opt-in:

```bash
book-organizer commit --root /mnt/data/Books --duplicates   # move binary duplicates to duplicates/
book-organizer commit --root /mnt/data/Books --quarantine   # move unresolved files to quarantine/
```

Every run writes a journal to `reports/commit-<id>.json`.

### 9. Rollback (if needed)

```bash
book-organizer rollback <commit-id> --root /mnt/data/Books
```

Undoes a commit using its journal: deletes the copies it made (cleaning
up empty directories) and restores any quarantine/duplicate moves. The
`<commit-id>` is in the journal filename and in commit's output.

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
ai:
  enabled: true
  model: haiku         # any `claude --model` value: haiku, sonnet, opus
  timeout_seconds: 180
providers:
  douban:
    enabled: true
    apikey: "..."      # community key by default; replace with your own
```

Only one `book-organizer` process may run against a library at a time
(enforced via `db/.lock`; delete the lock file if a process crashed).

## Development

```bash
.venv/bin/pytest        # run the test suite
```

Planned next (spec Phase 2): review TUI, metadata write-back, Calibre
import, and additional metadata providers (Google Books, Crossref).
