# Sub-project A — Library Web App

Date: 2026-09-26
Parent: `2026-09-26-web-app-decomposition.md`
Status: approved (design)

## 1. Scope

**In:** pipeline refactor, JSON sidecar store, SQLite index, cover
extraction, FastAPI backend, React SPA, browse/search/filter, manual
metadata editing with three write-back tiers, per-book "Fix metadata"
(search, optionally AI), job runner driving the full pipeline from the
UI, `reshelf serve`.

**Out (later sub-projects):** in-browser reading, highlights, notes,
Obsidian export, Docker. **Out entirely:** auth, multi-user, Calibre.

## 2. Storage model

### 2.1 Sidecar — the source of truth

One JSON file per book, keyed by content hash.

Path resolution is a single function `sidecar_path(sha256, file_path,
cfg) -> Path`, branching on `metadata.layout`:

| Layout | Path | Notes |
|---|---|---|
| `hash` (default) | `<root>/<metadata.dir>/<sha256>.json` | Never pollutes book folders; survives renames |
| `sidecar` | `<file>.json` beside every file | Writes into `incoming/`, which is Syncthing-synced |
| `library` | beside the committed copy in `library/` | Only valid for committed books |

Schema:

```json
{
  "schema": 1,
  "sha256": "…",
  "files": [
    {"path": "incoming/foo.pdf", "format": "pdf", "size": 0, "mtime": 0}
  ],
  "metadata": {
    "title": null, "subtitle": null, "authors": [], "translators": [],
    "series": null, "series_index": null,
    "publisher": null, "pubdate": null, "language": null,
    "isbn10": null, "isbn13": null,
    "tags": [], "description": null, "cover": null
  },
  "source": {
    "resolver": "embedded|deterministic|ai|human",
    "provider": null, "provider_id": null,
    "confidence": 0.0, "decided_at": "2026-09-26T00:00:00Z"
  },
  "reading": {"locator": null, "percent": 0.0, "updated_at": null},
  "annotations": [],
  "created_at": "…", "updated_at": "…"
}
```

`annotations` and `reading` are written by sub-project B; A creates the
keys empty and preserves anything it does not understand on rewrite
(forward compatibility: load, merge, dump).

Writes are atomic — serialize to `<path>.tmp`, `os.replace` onto the
target — under a per-`sha256` `threading.Lock`.

### 2.2 Invariants

1. `source.resolver == "human"` is **sticky**. `match` and `resolve`
   must skip such books unless explicitly forced.
2. A sidecar write is followed by an index write. If the index write
   fails, the sidecar still wins; `reshelf reindex` repairs.
3. A file with no `sha256` (scan interrupted before hashing) has no
   sidecar and is listed in the UI as *unhashed* with a rescan action.
4. Nothing under `incoming/` is modified unless
   `library.commit_mode == "move"` (a move) or `metadata.layout ==
   "sidecar"` (a `.json` beside the original). Book bytes in
   `incoming/` are never rewritten.

### 2.3 Index — derived, deletable

`db/books.sqlite3` — the existing `database.path`, kept as-is so no
config or path migration is needed. Existing tables (`files`, `works`, `editions`,
`authors`, `identifiers`, `metadata_sources`, `matches`, `scan_runs`)
are retained but demoted: they are now a cache of scan results and
provider candidates, not the metadata of record.

New:

```sql
CREATE VIRTUAL TABLE books_fts USING fts5(
    sha256 UNINDEXED, title, authors, series, publisher, tags, description
);

CREATE TABLE jobs (
    id INTEGER PRIMARY KEY,
    command TEXT NOT NULL,
    args_json TEXT,
    status TEXT NOT NULL,          -- queued|running|done|failed|cancelled|interrupted
    progress INTEGER DEFAULT 0,
    total INTEGER,
    message TEXT,
    log TEXT,
    error TEXT,
    created_at TEXT, started_at TEXT, finished_at TEXT
);
```

Schema evolution via `PRAGMA user_version` and an ordered list of
migration steps in `db/migrations.py`.

### 2.4 Commands

- `reshelf migrate-json` — one shot; emits sidecars from the current
  SQLite for every file with a match or extracted metadata.
- `reshelf reindex` — drops and rebuilds `books_fts` and the sidecar
  side of `files` from `<metadata.dir>` plus a rescan. Must be safe to
  run at any time.

## 3. Pipeline refactor (prerequisite)

Pipeline logic currently lives inside typer command bodies in
`cli.py`, so nothing else can call it. Extract to `reshelf/pipeline.py`:

```python
Progress = Callable[[int, int | None, str], None]   # done, total, message

def scan(cfg, paths, progress: Progress) -> ScanResult
def extract(cfg, force: bool, progress: Progress) -> ExtractResult
def match(cfg, offline: bool, progress: Progress) -> MatchResult
def resolve(cfg, limit, include_unresolved, progress: Progress) -> ResolveResult
def plan(cfg) -> Plan
def commit(cfg, plan, mode, progress: Progress) -> Journal
def rollback(cfg, journal_id, progress: Progress) -> None
def match_one(cfg, sha256, query=None, use_ai=False) -> list[Candidate]
```

`cli.py` wraps these with `rich` progress bars; the job runner wraps
them with log append plus a `jobs` row update. Behaviour must not
change — the existing CLI tests are the regression net.

The progress callback raises `JobCancelled` to stop a run cooperatively.

## 4. Cover extraction

Needed for the library grid; nothing populates `covers/` today.

| Format | Source | Files covered |
|---|---|---|
| EPUB | cover image referenced by the OPF, read from the zip | 619 |
| PDF | first page rendered by `pymupdf` (already a dependency) | 1129 |
| MOBI/AZW3 | none without Calibre — placeholder | 85 |
| Matched books with no local cover | provider cover URL, cached | — |

Written to `covers/<sha256>.jpg`, longest edge 600px, plus a 200px
thumbnail. Runs as part of `extract`, and on demand per book.

## 5. HTTP API

JSON under `/api`; the built SPA is served at `/`. Books are addressed
by `sha256` throughout, matching the sidecar key.

```
GET    /api/capabilities              ai enabled?, provider, commit_mode, writable formats
GET    /api/stats                     counts by status (the `report` numbers)

GET    /api/books                     q, status, format, tag, sort, page, page_size
GET    /api/books/{sha}               sidecar + index-derived paths and candidates
PATCH  /api/books/{sha}/metadata      {metadata: {...}, write_back: {...}}
GET    /api/books/{sha}/candidates    ranked candidates from the last match
POST   /api/books/{sha}/rematch       {query?: {title, author, isbn}, ai?: bool} -> job id
POST   /api/books/{sha}/choose        {candidate_id} -> writes sidecar, resolver="human"
GET    /api/books/{sha}/cover         image bytes (?size=thumb|full)
GET    /api/books/{sha}/file          Range-capable download of the primary file
GET    /api/tags

POST   /api/jobs                      {command, args} -> 202 {id}
GET    /api/jobs                      recent jobs
GET    /api/jobs/{id}                 status snapshot
GET    /api/jobs/{id}/events          SSE progress stream
DELETE /api/jobs/{id}                 cooperative cancel

GET    /api/settings                  the editable config subset
PUT    /api/settings
```

The **primary file** of a book is the first entry in `files[]` that
exists on disk, preferring a path under `library/` over one under
`incoming/`.

Because byte-identical duplicates share a hash, `/api/books` returns
one record per `sha256`, not per path; the duplicate paths are listed
on the detail response.

`GET /api/books/{sha}/file` is specified in A because download is
useful immediately and because sub-project B's reader depends on the
same Range-capable handler.

## 6. Metadata editing

`PATCH /api/books/{sha}/metadata` always writes the sidecar and sets
`source.resolver = "human"`. `write_back` selects additional effects:

| Tier | Key | Effect |
|---|---|---|
| 1 | — | Sidecar only. Always. Nothing on disk moves. |
| 2 | `library_file` | Re-derive the library path from the corrected metadata via the planner, rename/move the committed copy, append the operation to the commit journal so `rollback` undoes it. No-op (with a warning in the response) if the book is not committed. |
| 3 | `embed` | Rewrite metadata inside the **library copy only**. EPUB: rewrite the OPF in the zip, preserving a stored, first `mimetype` entry. PDF: `pymupdf` document metadata. |

Tier 3 returns **422 with a reason** for MOBI, AZW3, DJVU and TXT — no
writer exists without Calibre. The SPA disables the checkbox for those
formats using `/api/capabilities`.

Defaults for the two checkboxes come from `write_back.*` in config.

`library.commit_mode: copy | move` (default `copy`) is library-wide and
settable from the UI. `move` updates `files[].path` in the sidecar and
journals the move.

## 7. Fix metadata

`POST /api/books/{sha}/rematch` enqueues a job — one code path, so it
inherits provider rate limiting, cancellation and logging. The SPA
subscribes to that job's SSE and renders it inline as a spinner on the
book page rather than sending the user to the Jobs screen.

Two tiers:

- **Search** — always available. Optional `query` override lets the
  user re-query with a corrected title/author/ISBN when the embedded
  metadata was garbage. Returns ranked candidates.
- **Search + AI judge** — only when AI is configured. The model picks
  among the candidates the providers actually returned and records its
  reasoning in the evidence trail. It can never invent metadata.

`POST /api/books/{sha}/choose` commits a candidate to the sidecar with
`resolver: "human"`, which makes it sticky.

### AI configuration

`/api/capabilities` reports `ai: false` unless `ai.provider` is set.
When false the SPA renders the AI toggle disabled with "configure an
AI provider in Settings", and no code path contacts a model.

`ai.provider` accepts:
- `claude-cli` — the existing subscription-based path in `ai/resolver.py`
- `api` — `ai.api_key` (or `RESHELF_AI_API_KEY`), `ai.model`, `ai.base_url`

## 8. Job runner

`reshelf/web/jobs.py`: a `jobs` table plus **one** worker thread
consuming a `queue.Queue`. One job runs at a time; the single thread
*is* the lock.

```
# ponytail: single in-process worker, one job at a time. A real queue
# (RQ/Celery) only if concurrent pipeline runs are ever wanted.
```

- Cancellation: a per-job `threading.Event`; the progress callback
  checks it and raises `JobCancelled`.
- Crash recovery: on startup, any row still `queued` or `running`
  becomes `interrupted`. Unambiguous with a single worker.
- Log lines append to `jobs.log` and stream over SSE.
- `commit` and `rollback` are gated: the UI must run `plan` first,
  render the diff, and confirm before the job is accepted.

Exposed commands: `scan`, `extract`, `match`, `resolve`, `plan`,
`commit`, `rollback`, `reindex`, `rematch`.

## 9. SPA

React + Vite, built to static assets served by FastAPI. No SSR.

| Route | Purpose |
|---|---|
| `/` | Library grid/list — covers, search, status and format filters, sort |
| `/book/:sha` | Detail, metadata form, write-back checkboxes, candidates, Fix metadata |
| `/review` | The REVIEW queue, keyboard-driven, replaces the `review` CLI loop |
| `/jobs` | Job list plus live log; a persistent status bar sits in the shell |
| `/settings` | The `/api/settings` subset, including AI provider and commit mode |

Every scanned book appears, matched or not, badged by status —
including the unresolved majority. The 1,841 files on disk collapse to
fewer records once byte-identical duplicates share a hash.

## 10. Config additions

```yaml
metadata:
  layout: hash          # hash | sidecar | library
  dir: metadata
library:
  commit_mode: copy     # copy | move
web:
  host: 127.0.0.1
  port: 8080
ai:
  provider: null        # null | claude-cli | api
  model: haiku
  api_key: null         # or RESHELF_AI_API_KEY
  base_url: null
write_back:
  library_file: false
  embed: false
```

## 11. Testing

pytest, matching the existing suite's style. No JS test framework.

- `test_sidecar.py` — round trip, atomic write, all three layouts,
  unknown-key preservation, human stickiness
- `test_index.py` — `reindex` from sidecars reproduces list and search
- `test_migrations.py` — `user_version` stepping on a copy of the real DB
- `test_pipeline.py` — progress callback contract, `JobCancelled`
- `test_jobs.py` — enqueue, run, cancel, interrupted-on-startup
- `test_covers.py` — EPUB and PDF extraction, MOBI placeholder
- `test_writeback.py` — EPUB OPF rewrite round trip, PDF via pymupdf,
  422 for MOBI/AZW3/DJVU/TXT
- `test_api_*.py` — routes via httpx ASGI transport
- Existing CLI tests are the regression net for the pipeline refactor.

## 12. Known ceilings

- Hash keying means a re-downloaded copy of a book gets a new hash and
  orphans its sidecar. On scan, a changed hash at a known path offers
  to carry the sidecar across. `ponytail:` comment; proper
  edition-linking only if it bites.
- Single-slot job runner: no parallel pipeline stages.
- Sidecar and index can drift if a write is interrupted between the
  two; `reindex` is the repair.

## 13. Spec amendments

- `spec.md` §3.3 — "Originals must be preserved" becomes "preserved by
  default; `library.commit_mode: move` is an explicit opt-in".
- `spec.md` §18 — SQLite is a derived, rebuildable index, not the
  system of record. Sidecar schema (§2.1 here) is added as the record.

## 14. Definition of done

1. `reshelf serve --root <root>` starts, and `http://127.0.0.1:8080`
   lists every scanned book with covers, search and filters.
2. `reshelf migrate-json` produces a sidecar for every book that has
   metadata today; deleting the index database and running
   `reshelf reindex` restores the full UI with no data loss.
3. A metadata edit persists to its sidecar, survives a re-run of
   `match`, and optionally renames the library copy (reversible via
   `rollback`) and embeds into EPUB/PDF.
4. "Fix metadata" returns candidates for an unresolved book and a
   chosen candidate sticks.
5. With no AI configured, the app is fully usable and contacts no
   model; configuring a provider enables the AI judge.
6. Every pipeline stage runs from the UI with live progress and
   cancellation, and `commit` is reachable only through a previewed plan.
7. The existing CLI and its test suite still pass unchanged.
