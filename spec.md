# Book Organizer Skill — Specification v0.2

## 1. Overview

### Name

`book-organizer`

### Purpose

Automatically organize large collections of EPUB/PDF ebooks by:

* scanning local ebook directories
* extracting embedded metadata
* identifying books and editions
* enriching metadata from external book databases
* detecting duplicates
* resolving multilingual titles/authors
* assigning confidence scores
* generating a reviewable organization plan
* optionally updating ebook metadata
* optionally importing books into Calibre

The Skill MUST prioritize data integrity.

It MUST NOT modify, rename, move, delete, or overwrite original ebook files unless explicitly instructed by the user.

### Non-Goals

The Skill does NOT:

* remove or circumvent DRM
* download book content
* act as a reading application
* delete user files (it may only propose moves in a reviewable plan)
* guarantee correct identification without review for books lacking identifiers

---

## 2. Primary Use Cases

The Skill should support requests such as:

```text
扫描这个电子书目录
```

```text
整理我的电子书
```

```text
找出缺少 metadata 的书
```

```text
识别这些 EPUB/PDF
```

```text
帮我找出重复书籍
```

```text
给这些书补充 ISBN、作者、出版社和封面
```

```text
把整理好的书导入 Calibre
```

```text
检查哪些书匹配结果不确定
```

```text
整理 /mnt/data/Books/incoming
```

---

## 3. Design Principles

### 3.1 Never trust filenames alone

Filename is only one metadata source.

Preferred evidence order:

1. ISBN embedded in ebook
2. ISBN found inside book content
3. EPUB OPF metadata
4. PDF metadata
5. title + author metadata
6. filename
7. directory name
8. external database search
9. AI inference

AI inference MUST NOT override a strong identifier match without explicit evidence.

---

### 3.2 Work and Edition are separate concepts

The data model MUST distinguish:

```text
WORK
    三体
    The Three-Body Problem

EDITION
    Chinese 2008 edition
    English 2014 Tor edition
    Chinese ebook edition
```

A Work represents the intellectual work.

An Edition represents a particular publication.

---

### 3.3 Originals must be preserved

Default behavior:

```text
incoming/
    original files

library/
    managed files

quarantine/
    unresolved files
```

The Skill MUST NOT modify files under `incoming/` during scan or match operations.

Files enter `quarantine/` only via a committed plan action, never automatically. A file becomes eligible for a quarantine action when its status is UNRESOLVED after matching (and AI resolution, if enabled) has run.

---

### 3.4 Deterministic matching before AI

The Skill MUST first use:

* ISBN matching
* identifier matching
* normalized title matching
* normalized author matching
* fuzzy matching
* publication information
* language information

LLM/AI matching should only be used when deterministic matching is ambiguous.

---

## 4. Recommended Directory Layout

Example root:

```text
/mnt/data/Books/
```

Structure:

```text
Books/
├── incoming/
│   ├── ebook1.epub
│   ├── random-name.pdf
│   └── ...
│
├── library/
│
├── quarantine/
│
├── duplicates/
│
├── covers/
│
├── cache/
│   ├── openlibrary/
│   ├── google-books/
│   └── crossref/
│
├── reports/
│
├── db/
│   └── books.sqlite3
│
└── config.yaml
```

---

## 5. Supported Formats

v1 REQUIRED:

```text
EPUB
PDF
```

Future:

```text
MOBI
AZW
AZW3
CBZ
CBR
DJVU
FB2
TXT
```

---

## 6. Core Architecture

```text
                 ┌─────────────────────┐
                 │    File Scanner     │
                 └──────────┬──────────┘
                            │
                            ▼
                 ┌─────────────────────┐
                 │ Metadata Extractor  │
                 └──────────┬──────────┘
                            │
                            ▼
                 ┌─────────────────────┐
                 │ Identifier Engine   │
                 │ ISBN / DOI / etc.   │
                 └──────────┬──────────┘
                            │
                            ▼
                 ┌─────────────────────┐
                 │ Local Metadata DB   │
                 └──────────┬──────────┘
                            │
               cache miss   │
                            ▼
       ┌────────────────────────────────┐
       │ External Metadata Providers    │
       │                                │
       │ Open Library                   │
       │ Google Books                   │
       │ Crossref                       │
       └───────────────┬────────────────┘
                       │
                       ▼
                ┌─────────────┐
                │ Candidate   │
                │ Generator   │
                └──────┬──────┘
                       │
                       ▼
                ┌─────────────┐
                │ Match Engine│
                └──────┬──────┘
                       │
              ambiguous│
                       ▼
                ┌─────────────┐
                │ AI Resolver │
                └──────┬──────┘
                       │
                       ▼
                ┌─────────────┐
                │ Review Queue│
                └──────┬──────┘
                       │
                       ▼
                ┌─────────────┐
                │ Commit      │
                │ Engine      │
                └──────┬──────┘
                       │
              ┌────────┴─────────┐
              ▼                  ▼
        Metadata write       Calibre import
```

---

## 7. Components

### 7.1 Scanner

Responsible for discovering ebook files.

Input:

```text
directory path
```

Output:

```json
{
  "path": "/mnt/data/Books/incoming/book.epub",
  "filename": "book.epub",
  "extension": "epub",
  "size": 1839284,
  "mtime": 1788119200,
  "sha256": "..."
}
```

Requirements:

* recursive scan
* skip hidden files optionally
* configurable extensions
* SHA256 duplicate detection
* incremental scanning
* avoid hashing unchanged files when possible

Suggested fast identity:

```text
path
size
mtime
```

Full identity:

```text
SHA256
```

`sha256` MAY be null until hashing runs. Any operation that deduplicates, mutates, or commits a file MUST require its hash to be present.

---

## 8. Metadata Extraction

### EPUB

Extract:

```text
title
creator
identifier
ISBN
language
publisher
date
description
subject
series
series_index
cover
```

Sources:

```text
META-INF/container.xml
OPF package document
```

Potential libraries:

```text
ebooklib
lxml
zipfile
```

Calibre CLI may also be used:

```bash
ebook-meta book.epub
```

---

### PDF

Extract:

```text
title
author
subject
keywords
creation date
ISBN candidates
page count
```

Possible tools:

```text
PyMuPDF
pypdf
pdfinfo
```

Do NOT perform OCR by default.

OCR should only run when:

```text
metadata absent
AND
book cannot otherwise be identified
AND
user enables OCR
```

---

## 9. ISBN Extraction

Normalize all ISBNs.

Accepted forms:

```text
ISBN-10
ISBN-13
```

Strip:

```text
spaces
hyphens
ISBN:
ISBN-10:
ISBN-13:
```

Validate checksum.

Canonical form is ISBN-13: valid ISBN-10 input MUST be converted (prepend 978, recompute check digit). The original ISBN-10 is kept as a secondary identifier.

Example:

```python
normalize_isbn("ISBN 978-0-123456-47-2")
```

returns:

```text
9780123456472
```

Invalid checksum MUST NOT be treated as a strong identifier.

---

## 10. External Metadata Providers

Provider interface:

```python
class MetadataProvider:

    def lookup_isbn(self, isbn: str) -> list[Candidate]:
        ...

    def search(
        self,
        title: str,
        author: str | None = None,
        language: str | None = None
    ) -> list[Candidate]:
        ...
```

Initial providers:

```text
OpenLibraryProvider
GoogleBooksProvider
CrossrefProvider
```

Optional future:

```text
ISBNdbProvider
WorldCatProvider
DoubanProvider
CNKIProvider
LibraryOfCongressProvider
InternetArchiveProvider
```

Note: Open Library, Google Books, and Crossref all have weak coverage of Chinese-language ISBNs. Since the primary use cases include Chinese books, a provider with CN coverage (e.g. Douban or an ISBN database covering CIP data) SHOULD be prioritized for Phase 2 rather than treated as optional.

---

## 11. Provider Priority

Recommended fallback:

```text
ISBN available
    │
    ├── local DB
    │
    ├── Open Library
    │
    ├── Google Books
    │
    └── Crossref
    │
    ▼
candidate merge
```

Without ISBN:

```text
normalized title + author
    │
    ├── local DB
    ├── Open Library
    ├── Google Books
    └── Crossref
```

Academic content:

```text
DOI / ISBN
    ↓
Crossref
```

### Candidate merge

Candidates from all providers are pooled and deduplicated:

* primary key: canonical ISBN-13
* fallback key: (normalized title, normalized author, publisher, year)

When merged candidates disagree on a field, the value is taken in provider priority order (local DB > Open Library > Google Books > Crossref, configurable), and every field keeps its per-provider provenance (see the Provenance section).

---

## 12. Candidate Model

Canonical internal representation:

```json
{
  "provider": "openlibrary",
  "provider_id": "OL12345M",

  "work": {
    "title": "The Three-Body Problem",
    "original_title": "三体",
    "authors": [
      {
        "name": "Liu Cixin",
        "canonical_name": "刘慈欣"
      }
    ]
  },

  "edition": {
    "isbn10": null,
    "isbn13": "9780765382030",
    "publisher": "Tor Books",
    "publication_date": "2014",
    "language": "eng"
  },

  "subjects": [],
  "description": null,
  "cover_url": null
}
```

Provider-specific results MUST be converted to this structure before matching.

Field conventions:

* `language`: ISO 639-1 where possible (`zh`, `en`); provider codes such as `eng` MUST be normalized
* `publication_date`: ISO 8601; year-only values (`2014`) are allowed

---

## 13. Metadata Normalization

### Title normalization

Input:

```text
The Three-Body Problem: A Novel
```

Normalized:

```text
three body problem
```

Normalization MAY remove:

```text
case
punctuation
extra whitespace
edition markers
ebook markers
filename garbage
```

Be conservative with subtitles.

Store BOTH:

```text
raw_title
normalized_title
```

---

### Author normalization

Examples considered potentially equivalent:

```text
Gabriel García Márquez
Gabriel Garcia Marquez
加西亚·马尔克斯
```

AI MAY assist multilingual resolution.

Store aliases:

```json
{
  "canonical": "Gabriel García Márquez",
  "aliases": [
    "Gabriel Garcia Marquez",
    "加西亚·马尔克斯"
  ]
}
```

---

## 14. Matching Engine

Matching MUST produce:

```text
score
confidence
evidence
warnings
```

Example deterministic scoring:

```text
Exact ISBN                     +100
Exact title                    +40
Title similarity >= 0.95       +35
Title similarity >= 0.85       +25
Exact author                   +30
Author similarity >= 0.90      +25
Language match                 +10
Publisher match                 +8
Publication year exact          +7
Publication year ±1             +5
Series match                    +5
```

Negative evidence:

```text
ISBN conflict                   -100
different author                -40
language conflict               -15
year difference > 20            -10
```

Score is mapped to confidence by an explicit, tunable function. Initial policy:

```text
confidence = clamp(score / 100, 0.0, 1.0)
```

The divisor is chosen so the maximum non-ISBN evidence stack (exact
title 40 + exact author 30 + language 10 + publisher 8 + year 7 = 95)
can reach HIGH_CONFIDENCE; a mapping that caps ISBN-less matches below
the review threshold would force every book without an identifier into
manual review.

Overrides:

* valid exact ISBN match with no negative evidence → confidence = 0.99
* any ISBN conflict → confidence capped at 0.40

Tie-breaking: if the top two candidates score within 10 points of each other, the match is ambiguous regardless of absolute score and is routed to AI_RESOLUTION (or REVIEW when AI is disabled).

---

## 15. Confidence Policy

Suggested initial policy:

```text
>= 0.98
AUTO_ACCEPT

0.90–0.979
HIGH_CONFIDENCE

0.75–0.899
REVIEW_RECOMMENDED

0.50–0.749
AI_RESOLUTION

< 0.50
UNRESOLVED
```

Important:

An exact valid ISBN match may directly produce:

```text
confidence >= 0.99
```

unless conflicting evidence exists.

Band actions:

* AUTO_ACCEPT and HIGH_CONFIDENCE matches enter the plan without manual review (HIGH_CONFIDENCE is flagged in the report)
* REVIEW_RECOMMENDED requires `book-organizer review`
* AI_RESOLUTION is sent to the AI resolver; the resulting confidence is re-classified into these same bands, but an AI-resolved match MUST NOT reach AUTO_ACCEPT — its best outcome is HIGH_CONFIDENCE
* UNRESOLVED files are reported and may receive a quarantine action in the plan

---

## 16. AI Resolver

The AI resolver MUST receive structured information.

Example:

```json
{
  "local_book": {
    "filename": "村上春树 Norwegian wood.epub",
    "title": "Norwegian wood",
    "author": "村上春树",
    "language": "zh"
  },

  "candidates": [
    {...},
    {...},
    {...}
  ]
}
```

Required output:

```json
{
  "decision": "candidate_2",
  "confidence": 0.91,
  "reasons": [
    "author corresponds across languages",
    "title corresponds to known translated title",
    "candidate language matches ebook"
  ],
  "uncertainties": []
}
```

AI MUST NOT create nonexistent:

```text
ISBN
publisher
publication date
edition
```

AI may normalize or infer relationships, but factual metadata MUST retain provenance.

---

## 17. Provenance

Every metadata field SHOULD retain its source.

Example:

```json
{
  "title": {
    "value": "三体",
    "source": "epub"
  },

  "isbn13": {
    "value": "978xxxxxxxxxx",
    "source": "openlibrary"
  },

  "publisher": {
    "value": "重庆出版社",
    "source": "google_books"
  }
}
```

This makes later corrections possible.

---

## 18. SQLite Schema

### Concurrency model

The database assumes a SINGLE process at a time:

* the CLI takes an exclusive lock file (e.g. `db/.lock`) at startup and refuses to run if another instance holds it
* SQLite is opened in WAL mode with a busy timeout, so internal worker threads (hashing, provider fetches) may share the connection pool safely
* multi-process or networked access is explicitly out of scope for v1

### Suggested schema

```sql
CREATE TABLE files (
    id INTEGER PRIMARY KEY,
    path TEXT UNIQUE NOT NULL,
    sha256 TEXT,
    format TEXT,
    size INTEGER,
    mtime INTEGER,
    status TEXT,

    title_raw TEXT,
    author_raw TEXT,
    isbn_raw TEXT,
    language_raw TEXT,

    matched_edition_id INTEGER,
    match_confidence REAL,

    created_at TEXT,
    updated_at TEXT,

    FOREIGN KEY(matched_edition_id)
        REFERENCES editions(id)
);

CREATE INDEX idx_files_sha256 ON files(sha256);
```

```sql
CREATE TABLE works (
    id INTEGER PRIMARY KEY,
    canonical_title TEXT,
    original_title TEXT,
    original_language TEXT,
    description TEXT
);
```

```sql
CREATE TABLE editions (
    id INTEGER PRIMARY KEY,
    work_id INTEGER,

    isbn10 TEXT,
    isbn13 TEXT,

    publisher TEXT,
    publication_date TEXT,
    language TEXT,

    edition_name TEXT,

    FOREIGN KEY(work_id)
        REFERENCES works(id)
);

CREATE INDEX idx_editions_isbn13 ON editions(isbn13);
```

`editions.isbn10` / `isbn13` are denormalized conveniences; the `identifiers` table is the authoritative identifier store.

```sql
CREATE TABLE authors (
    id INTEGER PRIMARY KEY,
    canonical_name TEXT
);
```

```sql
CREATE TABLE author_aliases (
    id INTEGER PRIMARY KEY,
    author_id INTEGER,
    alias TEXT,

    FOREIGN KEY(author_id)
        REFERENCES authors(id)
);
```

```sql
CREATE TABLE work_authors (
    work_id INTEGER,
    author_id INTEGER,

    PRIMARY KEY(work_id, author_id)
);
```

```sql
CREATE TABLE identifiers (
    id INTEGER PRIMARY KEY,

    edition_id INTEGER,
    type TEXT,
    value TEXT,
    source TEXT,

    UNIQUE(type, value)
);
```

```sql
CREATE TABLE metadata_sources (
    id INTEGER PRIMARY KEY,

    edition_id INTEGER,

    provider TEXT,
    provider_id TEXT,

    retrieved_at TEXT,
    raw_json TEXT,

    UNIQUE(provider, provider_id),

    FOREIGN KEY(edition_id)
        REFERENCES editions(id)
);
```

```sql
CREATE TABLE matches (
    id INTEGER PRIMARY KEY,

    file_id INTEGER,
    edition_id INTEGER,

    score REAL,
    confidence REAL,

    resolver TEXT,

    evidence_json TEXT,
    status TEXT,

    created_at TEXT,

    FOREIGN KEY(file_id)
        REFERENCES files(id),
    FOREIGN KEY(edition_id)
        REFERENCES editions(id)
);

CREATE INDEX idx_matches_file ON matches(file_id);
```

```sql
CREATE TABLE scan_runs (
    id INTEGER PRIMARY KEY,
    started_at TEXT,
    completed_at TEXT,

    root_path TEXT,

    files_seen INTEGER,
    files_added INTEGER,
    files_changed INTEGER
);
```

---

## 19. Status Model

File states:

```text
NEW

SCANNED

IDENTIFIED

MATCHED

REVIEW

UNRESOLVED

DUPLICATE

READY

COMMITTED

ERROR
```

State transition:

```text
NEW
 ↓
SCANNED
 ↓
IDENTIFIED
 ↓
MATCHED
 ↓
READY
 ↓
COMMITTED
```

Alternative:

```text
MATCHED
 ↓
REVIEW
 ↓
READY
```

or:

```text
IDENTIFIED
 ↓
UNRESOLVED
```

Additional rules:

* DUPLICATE is assigned when a binary duplicate (same SHA256) of an already-tracked file is found; duplicates never advance to COMMITTED automatically
* ERROR is assigned when a file cannot be parsed (corrupt archive, encrypted PDF, ...); ERROR files are skipped until re-scanned with `--force`
* REVIEW → READY on user accept; REVIEW → UNRESOLVED on user reject

---

## 20. Duplicate Detection

Three levels.

### Binary duplicate

```text
SHA256 identical
```

Meaning:

```text
same physical file content
```

---

### Edition duplicate

```text
same ISBN
```

Potentially different ebook encodings of the same edition.

---

### Work duplicate

Same:

```text
work
```

but possibly:

```text
EPUB
PDF

English
Chinese

different editions
```

These MUST NOT automatically be deleted.

---

## 21. Calibre Integration

Calibre should act as the final managed library.

Useful commands:

```text
ebook-meta
calibredb
calibre-server
```

Recommended architecture:

```text
book-organizer
      ↓
clean metadata
      ↓
calibredb
      ↓
Calibre Library
```

When Calibre integration is enabled, `library/` IS the Calibre library and Calibre owns file naming and layout.

When Calibre is disabled, committed files are COPIED (default; move is opt-in per commit) into:

```text
library/{author_sort}/{title} ({year})/{title}.{ext}
```

The template is configurable (`library.naming` in config.yaml). CJK names are kept as-is; only characters illegal in filenames are replaced.

Do NOT manipulate Calibre's internal files directly.

Always use Calibre CLI/API.

---

## 22. CLI Specification

Executable:

```bash
book-organizer
```

### Initialize

```bash
book-organizer init /mnt/data/Books
```

Creates:

```text
directories
SQLite DB
config.yaml
```

---

### Scan

```bash
book-organizer scan
```

Optional:

```bash
book-organizer scan /mnt/data/Books/incoming
```

Options:

```text
--recursive
--hash
--force
--format epub,pdf
```

---

### Extract metadata

```bash
book-organizer extract
```

Single file:

```bash
book-organizer extract book.epub
```

---

### Match

```bash
book-organizer match
```

Options:

```text
--provider openlibrary
--provider google-books
--provider crossref

--offline

--min-confidence 0.90
```

---

### AI resolution

```bash
book-organizer resolve
```

By default `resolve` processes only the AI_RESOLUTION band (see Confidence Policy). To widen the range:

```bash
book-organizer resolve --confidence-below 0.90
```

---

### Review

```bash
book-organizer review
```

Example interface:

```text
────────────────────────────────────────

File
村上春树 Norwegian wood.epub

Detected
Title: Norwegian wood
Author: 村上春树
Language: zh

Candidate

挪威的森林
村上春树
上海译文出版社

ISBN
9787532743124

Confidence
91%

Evidence

✓ author match
✓ translated title match
✓ language match
? publication year unavailable

[A] Accept
[R] Reject
[N] Next candidate
[E] Edit
[S] Skip

────────────────────────────────────────
```

---

### Report

```bash
book-organizer report
```

Example:

```text
Files scanned          4,863

Exact ISBN matches     2,184
High confidence        1,706
AI resolved              621
Needs review              97
Duplicates               211
Unresolved                44
```

Optional:

```bash
book-organizer report --json
book-organizer report --csv
book-organizer report --html
```

---

### Duplicates

```bash
book-organizer duplicates
```

Lists binary / edition / work duplicate groups (see Duplicate Detection).

---

### Status

```bash
book-organizer status
```

Shows file counts per state and pending plan / commit information.

---

## 23. Plan / Commit Model

This is REQUIRED.

Never combine metadata resolution and filesystem mutation into one step.

First:

```bash
book-organizer plan
```

Output:

```text
reports/plan-2026xxxx.json
```

Example:

```json
{
  "plan_id": "2026xxxx-...",
  "created_at": "...",
  "actions": [
    {
      "file": "/mnt/data/Books/incoming/book.epub",
      "action": "import",
      "preconditions": {
        "sha256": "...",
        "size": 1839284,
        "mtime": 1788119200
      },
      "metadata_changes": {...}
    }
  ]
}
```

Then:

```bash
book-organizer commit
```

Option:

```bash
book-organizer commit \
  --plan reports/plan.json
```

Allowed actions:

```text
import
write_metadata
copy_to_library
quarantine
mark_duplicate
```

Dry-run:

```bash
book-organizer commit --dry-run
```

Commit MUST verify each action's preconditions and skip (and report) any file that changed since the plan was generated.

Commit writes a journal to `reports/commit-<id>.json` recording every performed action and metadata backup, enabling:

```bash
book-organizer rollback <commit-id>
```

---

## 24. Safety Requirements

The Skill MUST:

* preserve original ebook files by default
* support dry-run
* maintain operation logs
* preserve old metadata
* never delete automatically based only on fuzzy matching
* never overwrite files without explicit permission
* never treat AI generated metadata as verified factual metadata
* allow rollback whenever practical

Any destructive action MUST require explicit user intent.

---

## 25. Configuration

Example `config.yaml`:

```yaml
library:
  root: /mnt/data/Books
  incoming: /mnt/data/Books/incoming
  calibre: /mnt/data/Books/library
  quarantine: /mnt/data/Books/quarantine
  naming: "{author_sort}/{title} ({year})/{title}"
  copy_on_commit: true   # copy (not move) originals into the library

database:
  path: /mnt/data/Books/db/books.sqlite3

scan:
  recursive: true
  formats:
    - epub
    - pdf

hash:
  algorithm: sha256

matching:
  # thresholds correspond to the Confidence Policy bands
  auto_accept: 0.98        # >= : AUTO_ACCEPT
  review_below: 0.90       # <  : REVIEW_RECOMMENDED
  ai_resolve_below: 0.75   # <  : AI_RESOLUTION
  unresolved_below: 0.50   # <  : UNRESOLVED

providers:
  openlibrary:
    enabled: true

  google_books:
    enabled: true
    # Google Books allows unauthenticated requests but at very low
    # quota; an API key is effectively required for real libraries.
    # Prefer the environment variable over storing the key in config.
    api_key_env: GOOGLE_BOOKS_API_KEY
    # api_key: ...          # discouraged; config files get copied around

  crossref:
    enabled: true
    # Crossref asks for a contact email ("polite pool") for better
    # rate limits.
    mailto: you@example.com

ai:
  enabled: true
  resolver_only: true

calibre:
  enabled: true

safety:
  modify_originals: false
  delete_duplicates: false
  require_commit: true
```

---

## 26. Python Package Layout

Recommended:

```text
book-organizer/
├── pyproject.toml
├── README.md
├── config.example.yaml
│
├── src/
│   └── book_organizer/
│       ├── __init__.py
│       ├── cli.py
│       ├── config.py
│       │
│       ├── scanner/
│       │   ├── scanner.py
│       │   └── hashing.py
│       │
│       ├── extractors/
│       │   ├── base.py
│       │   ├── epub.py
│       │   └── pdf.py
│       │
│       ├── metadata/
│       │   ├── models.py
│       │   ├── normalization.py
│       │   └── isbn.py
│       │
│       ├── providers/
│       │   ├── base.py
│       │   ├── openlibrary.py
│       │   ├── google_books.py
│       │   └── crossref.py
│       │
│       ├── matching/
│       │   ├── candidate.py
│       │   ├── scorer.py
│       │   └── resolver.py
│       │
│       ├── ai/
│       │   ├── resolver.py
│       │   └── prompts.py
│       │
│       ├── db/
│       │   ├── database.py
│       │   ├── models.py
│       │   └── migrations/
│       │
│       ├── calibre/
│       │   ├── metadata.py
│       │   └── library.py
│       │
│       ├── planner/
│       │   ├── planner.py
│       │   └── actions.py
│       │
│       ├── review/
│       │   └── tui.py
│       │
│       └── reports/
│           └── report.py
│
└── tests/
    ├── test_isbn.py
    ├── test_normalization.py
    ├── test_matching.py
    ├── test_epub.py
    └── fixtures/
```

---

## 27. Recommended Python Dependencies

Core:

```text
typer
pydantic
sqlalchemy
httpx
pyyaml
rich
rapidfuzz
```

EPUB:

```text
ebooklib
lxml
```

PDF:

```text
pymupdf
```

Optional DB migrations:

```text
alembic
```

Optional TUI:

```text
textual
```

---

## 28. Data Models

Suggested Pydantic models:

```python
class Author(BaseModel):
    name: str
    aliases: list[str] = []


class Work(BaseModel):
    title: str
    original_title: str | None = None
    authors: list[Author]
    original_language: str | None = None


class Edition(BaseModel):
    work: Work

    isbn10: str | None = None
    isbn13: str | None = None

    publisher: str | None = None
    publication_date: str | None = None
    language: str | None = None

    edition_name: str | None = None


class Candidate(BaseModel):
    provider: str
    provider_id: str

    edition: Edition

    score: float = 0
    confidence: float = 0

    evidence: list[str] = []
```

---

## 29. Caching

Every external query SHOULD be cached.

Cache key examples:

```text
isbn:9780765382030
```

or:

```text
search:three-body-problem:liu-cixin
```

Cache record:

```json
{
  "provider": "openlibrary",
  "query": {...},
  "retrieved_at": "...",
  "response": {...}
}
```

Recommended default cache TTL:

```text
30 days
```

ISBN results may reasonably be cached much longer.

---

## 30. Rate Limiting

Provider adapters MUST implement:

```text
timeouts
retries
backoff
rate limits
```

API keys and other credentials MUST NOT appear in logs, cache records, or reports — cache keys and stored queries are recorded with credentials stripped.

Example architecture:

```python
Provider
    │
    ▼
CachedProvider
    │
    ▼
RateLimitedProvider
    │
    ▼
HTTP API
```

---

## 31. Logging

Structured logging recommended.

Example:

```json
{
  "event": "book_match",
  "file_id": 302,
  "provider": "openlibrary",
  "confidence": 0.992,
  "reason": "exact_isbn"
}
```

Log levels:

```text
DEBUG
INFO
WARNING
ERROR
```

---

## 32. Testing Strategy

Unit tests MUST cover:

```text
ISBN validation
ISBN normalization
ISBN-10 → ISBN-13 conversion
title normalization
author normalization
matching score
duplicate detection
metadata extraction
provider normalization
```

Use fixture files:

```text
simple.epub
missing-metadata.epub
unicode-title.epub
chinese-book.epub
broken-metadata.epub
sample.pdf
```

Provider API tests SHOULD use saved JSON fixtures rather than live calls.

Integration tests SHOULD cover interrupted-scan resume and plan precondition verification (file changed between plan and commit).

---

## 33. MVP

Do NOT build everything initially.

MVP should contain only:

```text
1. init

2. scan directory

3. SHA256 detection

4. EPUB metadata extraction

5. PDF metadata extraction

6. ISBN normalization

7. SQLite storage

8. Open Library provider

9. title/author fuzzy matching

10. confidence score

11. report

12. dry-run plan
```

NO AI required for MVP.

NO automatic modification required for MVP.

---

## 34. MVP Phase 2

Add:

```text
Google Books

Crossref

Douban (or equivalent CN-coverage provider)

AI resolver

review TUI

metadata write-back

Calibre import
```

---

## 35. Phase 3

Add:

```text
cover selection

series detection

multilingual author aliases

Chinese translated title resolution

automatic tags

genre classification

duplicate management

Calibre custom columns
```

---

## 36. Future AI Features

Possible AI tasks:

### Translation relationship detection

```text
Norwegian Wood
↔
挪威的森林
```

### Author alias resolution

```text
村上春树
↔
Haruki Murakami
```

### Genre classification

Input:

```text
title
description
subjects
```

Output:

```text
Science Fiction
Hard SF
First Contact
```

### Series inference

```text
The Three-Body Problem
The Dark Forest
Death's End
```

→

```text
Remembrance of Earth's Past
1
2
3
```

All inferred information MUST be tagged:

```text
source = ai_inference
```

rather than pretending to be authoritative metadata.

---

## 37. Skill Agent Behavior

When invoked, the Skill should first determine user intent.

Examples:

```text
scan
inspect
match
review
report
plan
commit
import
```

For ambiguous requests such as:

```text
整理我的电子书
```

default to:

```text
scan
extract
match
report
```

Do NOT default to commit.

---

## 38. Suggested Skill Interface

If integrating into an AI coding/agent system, expose these tools:

```text
book_scan

book_inspect

book_search_metadata

book_match

book_duplicate_scan

book_generate_plan

book_review

book_commit

book_calibre_import
```

Read-only tools:

```text
book_scan
book_inspect
book_search_metadata
book_match
book_duplicate_scan
book_generate_plan
```

Write tools:

```text
book_commit
book_calibre_import
```

This separation is important for agent safety.

---

## 39. Example Agent Workflow

User:

```text
整理 /mnt/data/Books/incoming
```

Agent:

```text
book_scan
     ↓
book_inspect
     ↓
book_match
     ↓
book_duplicate_scan
     ↓
book_generate_plan
```

Return:

```text
4863 files found

2184 ISBN exact matches
1706 high-confidence matches
621 ambiguous matches
211 duplicates
97 require review
44 unresolved

No files have been modified.
```

Only after explicit request:

```text
执行整理
```

should the agent invoke:

```text
book_commit
```

---

## 40. Definition of Done for v1

v1 is complete when the following works reliably:

```bash
book-organizer init /mnt/data/Books

book-organizer scan

book-organizer match

book-organizer report

book-organizer plan
```

for a directory containing several thousand EPUB/PDF files.

The system must:

* resume interrupted scans
* avoid duplicate processing
* cache API requests
* preserve original files
* explain every automatic match
* expose confidence
* generate a deterministic plan before modifications
* never require AI for straightforward ISBN matches
* support later Calibre integration without schema redesign

