# reshelf

Organize large collections of ebooks (EPUB, PDF, MOBI/AZW3, TXT, DjVu):
scan, extract metadata, match against Douban and Open Library, detect
duplicates, and produce a reviewable organization plan — without ever
modifying your original files. A local web app lets you browse the
library, fix metadata, run the pipeline, and read books with highlights
and notes.

See `spec.md` for the full specification. Pipeline: scan → extract →
match (Douban + Open Library) → AI resolve → report → plan → commit,
with rollback. The only write steps are `commit` (copies matched books
into `library/`; moves are opt-in flags) and `rollback` (undoes a commit).

## Install

Requires Python ≥ 3.11, plus Node.js for building the web UI.

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
(cd web && npm install && npm run build)   # only needed for `reshelf serve`
```

Optional: `ddjvu` (Debian/Ubuntu: `djvulibre-bin`) to read DjVu files,
which are converted to PDF. MOBI/AZW3/TXT conversion to EPUB needs no
external tools.

The `reshelf` command is then available at `.venv/bin/reshelf`
(or on PATH with the venv activated).

## Usage

### 1. Initialize a library root

```bash
reshelf init /mnt/data/Books
```

Creates the directory layout (`incoming/ library/ quarantine/ duplicates/
covers/ cache/ reports/ db/`), a `config.yaml`, and the SQLite database.
Idempotent — safe to re-run.

Drop your ebooks into `/mnt/data/Books/incoming/`.

All later commands take `--root /mnt/data/Books` (defaults to the current
directory, so you can also just `cd` into the library root).

### 2. Scan

```bash
reshelf scan --root /mnt/data/Books          # scans incoming/
reshelf scan /some/other/dir --root /mnt/data/Books
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
reshelf extract --root /mnt/data/Books
reshelf extract --root /mnt/data/Books --force   # re-try ERROR files too
```

Reads embedded metadata (EPUB OPF; PDF info plus an ISBN scan of the first
5 pages — no OCR). Unreadable files are marked ERROR and skipped until
`--force`.

### 4. Match against Open Library and Douban

```bash
reshelf match --root /mnt/data/Books
reshelf match --root /mnt/data/Books --offline            # local cache only, no network
reshelf match --root /mnt/data/Books --retry-unresolved   # also re-match UNRESOLVED files
```

Looks up each identified file by ISBN, falling back to title/author
search. When embedded metadata is missing or junk (placeholder titles,
uploader account names as authors), the author and a clean title are
taken from the filename instead; multi-volume titles are also searched
by series name and by title without its subtitle. Candidates are scored candidates deterministically (spec §14). Books with
Chinese titles/authors query Douban first, everything else Open Library
first. Every match stores its score, confidence, and evidence. Results
land in confidence bands: exact-ISBN and high-confidence matches become
MATCHED; ambiguous ones go to REVIEW; the rest stay UNRESOLVED. All API
responses are cached under `cache/` for 30 days.

### 5. AI-resolve ambiguous matches (optional)

```bash
reshelf resolve --root /mnt/data/Books
reshelf resolve --root /mnt/data/Books --limit 20            # sample first
reshelf resolve --root /mnt/data/Books --include-unresolved  # also retry UNRESOLVED
```

Requires `ai.provider` in config.yaml (off by default): `claude-cli` uses
the `claude` CLI and your Claude Code subscription; `api` calls the API
with `ai.api_key` or `RESHELF_AI_API_KEY`. Asks Claude to judge books the deterministic matcher left in REVIEW:
translated titles, transliterated authors, marketing-subtitle noise. The
AI only picks among real provider candidates — it can never invent ISBNs
or metadata — its confidence is capped below auto-accept, and its
reasoning is stored in each match's evidence trail. Model is configurable
(`ai.model` in config.yaml, default `haiku`).

### 6. Report

```bash
reshelf report --root /mnt/data/Books
reshelf report --root /mnt/data/Books --json
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
reshelf plan --root /mnt/data/Books
```

Writes `reports/plan-<id>.json` describing what commit *would* do —
`import` for matched files, `mark_duplicate`, `quarantine` — each action
carrying `preconditions` (sha256/size/mtime) so commit can verify nothing
changed since planning. Planning itself never touches your files.

### 8. Commit the plan

```bash
reshelf commit --root /mnt/data/Books --dry-run   # preview
reshelf commit --root /mnt/data/Books             # execute imports
```

Executes the latest plan (or `--plan PATH`). Matched books are **copied**
(never moved) into `library/{author}/{title} ({year})/{title}.ext`;
originals stay exactly where they are. Each file's sha256/size/mtime is
re-verified first — anything changed since planning is skipped and
reported. Re-running is safe: already-committed books are skipped.

Two action types genuinely relocate files and are therefore opt-in:

```bash
reshelf commit --root /mnt/data/Books --duplicates   # move binary duplicates to duplicates/
reshelf commit --root /mnt/data/Books --quarantine   # move unresolved files to quarantine/
```

Every run writes a journal to `reports/commit-<id>.json`.

### 9. Rollback (if needed)

```bash
reshelf rollback <commit-id> --root /mnt/data/Books
```

Undoes a commit using its journal: deletes the copies it made (cleaning
up empty directories) and restores any quarantine/duplicate moves. The
`<commit-id>` is in the journal filename and in commit's output.

### 10. Covers

```bash
reshelf covers --root /mnt/data/Books
```

Extracts missing cover images into `covers/` for every EPUB/PDF book.
New books get covers during extract; this backfills older ones.

### 11. Export to Calibre

```bash
reshelf calibre-export --root /mnt/data/Books --library "/path/to/Calibre Library" --dry-run
reshelf calibre-export --root /mnt/data/Books --library "/path/to/Calibre Library"
```

`library/` is a plain folder tree, not a Calibre library -- Calibre
identifies a library by the `metadata.db` at its root and refuses to
adopt a non-empty folder. This imports the committed books into a
Calibre library instead, feeding `calibredb` the title, authors, ISBN,
language, publisher, pubdate, and provider identifiers we already
matched, rather than letting Calibre guess from file contents.

Points at a new folder to create a library, or an existing one to merge
into (Calibre skips title/author duplicates by default). **Close Calibre
first** -- `calibredb` refuses to write to a library while the GUI holds it.

## Web app

```bash
reshelf serve --root /mnt/data/Books
```

Runs a browser UI over the same library the CLI operates on -- library
browsing, a keyboard-driven review queue, metadata editing, and a Jobs
page that drives the whole pipeline (scan/extract/match/resolve/plan/
reindex, plus the gated commit/rollback below) with live progress.

Books open in an in-browser reader: pdf.js for PDF (and DjVu, via its
PDF conversion), foliate-js for EPUB (and MOBI/AZW3/TXT, via their EPUB
conversion). Highlights, notes, bookmarks and reading progress are saved
into the book's sidecar. Metadata edits can optionally be written back
by renaming the library copy and/or embedding into the file itself.

For frontend development, `cd web && npm run dev` starts Vite on
port 5173 and proxies `/api` to a running `reshelf serve`.

By default it binds to `127.0.0.1:8080`, i.e. this machine only.
**There is no authentication** -- anyone who can reach that address and
port can browse, edit, and commit/rollback the library. Do not point
`web.host` at `0.0.0.0` or a public interface without putting your own
auth (a reverse proxy, an SSH tunnel, ...) in front of it.

Only one `reshelf` process (CLI or `serve`) may hold the library's lock
at a time, so stop any running CLI command before starting the server.

If you already have a library from before the web app existed, bootstrap
it once before serving:

```bash
reshelf migrate-json --root /mnt/data/Books   # writes a sidecar for every hashed file
reshelf reindex --root /mnt/data/Books        # (re)builds the search index the UI queries
```

The per-book JSON sidecars under `metadata/` are the source of truth for
everything the UI shows; the sqlite database (`db/`) is a disposable,
rebuildable search index over them -- `reshelf reindex` regenerates it
from the sidecars at any time, so losing or deleting it loses nothing.

AI-assisted matching and resolve stay off, everywhere in the UI, until
`ai.provider` is set on the Settings page (or in `config.yaml`) -- there
is no default provider.

Docker packaging for the web app is a later sub-project; for now, run it
the same way as the CLI, inside your own venv.

## Configuration

`config.yaml` in the library root (created by `init`). Notable settings:

```yaml
library:
  commit_mode: copy    # `move` is an explicit opt-in
metadata:
  layout: hash         # where per-book JSON sidecars live (hash | sidecar | library)
  dir: metadata
web:
  host: 127.0.0.1      # no auth -- keep it local
  port: 8080
write_back:            # defaults for the metadata editor
  library_file: false  # rename the library copy after edits
  embed: false         # write metadata into the file itself
matching:            # confidence bands, see spec §15
  auto_accept: 0.98
  review_below: 0.9
  ai_resolve_below: 0.75
  unresolved_below: 0.5
scan:
  formats: [epub, pdf, mobi, azw, azw3, txt, djvu]
  recursive: true
cache:
  ttl_days: 30
ai:
  provider: null       # claude-cli | api; null disables AI everywhere
  model: haiku         # haiku, sonnet, opus
  api_key: null        # for provider: api (or set RESHELF_AI_API_KEY)
  timeout_seconds: 180
providers:
  douban:
    enabled: true
    apikey: "..."      # community key by default; replace with your own
```

Only one `reshelf` process may run against a library at a time
(enforced via `db/.lock`; delete the lock file if a process crashed).

## Development

```bash
.venv/bin/pytest                # backend tests
(cd web && npm test)            # frontend tests (vitest)
```

Design docs live in `docs/superpowers/` (specs and plans per sub-project).
Done: A (library web app) and B (reader and annotations). Planned next:
C — Obsidian export of annotations and Docker packaging.
