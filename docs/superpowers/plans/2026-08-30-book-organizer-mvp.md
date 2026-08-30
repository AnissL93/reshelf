# Book Organizer MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the spec §33 MVP of `book-organizer`: init, scan (with SHA256 duplicate detection), EPUB/PDF metadata extraction, ISBN normalization, SQLite storage, Open Library provider with file cache, fuzzy matching with confidence bands, report, and dry-run plan generation.

**Architecture:** A Typer CLI over a single SQLite database (spec §18 schema, raw `sqlite3` — SQLAlchemy is "recommended" in the spec, not required, and the spec's schema is literal SQL). Pipeline stages are separate modules that communicate only through DB rows and small Pydantic models. EPUB parsing uses stdlib `zipfile` + `xml.etree` (spec lists these as acceptable); PDF uses PyMuPDF. No AI, no file mutation — `plan` is the terminal write-free step.

**Tech Stack:** Python ≥3.11, typer, pydantic v2, httpx, pyyaml, rich, rapidfuzz, pymupdf, pytest.

**Spec:** `spec.md` (v0.2, repo root)

## Global Constraints

- Originals are never modified, moved, or deleted. The MVP has NO commit command; `plan` only writes a JSON file under `reports/`.
- Canonical ISBN form is ISBN-13; valid ISBN-10 is converted (978 prefix, recomputed check digit) and kept as secondary.
- Language codes normalize to ISO 639-1 (`zh`, `en`); provider codes like `eng` must be mapped.
- Confidence = `clamp(score / 150, 0, 1)`; exact valid ISBN with no conflicts → 0.99; any ISBN conflict caps confidence at 0.40; top-two scores within 10 points = ambiguous.
- Bands (config keys per spec §25): `auto_accept: 0.98`, `review_below: 0.90`, `ai_resolve_below: 0.75`, `unresolved_below: 0.50`.
- Single process: exclusive lock file `db/.lock`, SQLite WAL mode, busy timeout.
- File statuses are uppercase strings: NEW, SCANNED, IDENTIFIED, MATCHED, REVIEW, UNRESOLVED, DUPLICATE, ERROR (READY/COMMITTED are post-MVP).
- `sha256` may be NULL until hashed; dedup requires the hash.
- All timestamps stored as UTC ISO 8601 strings.
- Run all commands from repo root `/home/hy/Projects/book-org`. Tests: `.venv/bin/pytest`. The repo has no commits yet; Task 1 makes the initial commit including `spec.md` and this plan.

---

### Task 1: Project scaffolding and CLI skeleton

**Files:**
- Create: `pyproject.toml`, `src/book_organizer/__init__.py`, `src/book_organizer/cli.py`, `tests/__init__.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Produces: `book_organizer.cli.app` (Typer app), console script `book-organizer` via `cli.main()`. Every later task adds commands to `app`.

- [ ] **Step 1: Initial commit of existing docs**

```bash
git add spec.md docs/superpowers/plans/2026-08-30-book-organizer-mvp.md
git commit -m "docs: add book-organizer spec v0.2 and MVP implementation plan"
```

- [ ] **Step 2: Write pyproject.toml and package skeleton**

`pyproject.toml`:

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "book-organizer"
version = "0.1.0"
description = "Organize large collections of EPUB/PDF ebooks"
requires-python = ">=3.11"
dependencies = [
    "typer>=0.12",
    "pydantic>=2.7",
    "httpx>=0.27",
    "pyyaml>=6.0",
    "rich>=13.0",
    "rapidfuzz>=3.0",
    "pymupdf>=1.24",
]

[project.optional-dependencies]
dev = ["pytest>=8.0"]

[project.scripts]
book-organizer = "book_organizer.cli:main"

[tool.hatch.build.targets.wheel]
packages = ["src/book_organizer"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

`src/book_organizer/__init__.py`:

```python
__version__ = "0.1.0"
```

`src/book_organizer/cli.py`:

```python
import typer

app = typer.Typer(no_args_is_help=True)


@app.callback()
def cli() -> None:
    """Organize large collections of EPUB/PDF ebooks."""


def main() -> None:
    app()
```

Create empty `tests/__init__.py`.

- [ ] **Step 3: Create venv, install, write the test**

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
```

`tests/test_cli.py`:

```python
from typer.testing import CliRunner

from book_organizer.cli import app

runner = CliRunner()


def test_help_runs():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "ebooks" in result.output
```

- [ ] **Step 4: Run test, verify it passes**

Run: `.venv/bin/pytest tests/test_cli.py -v` — Expected: PASS.
(This task is scaffolding; the failing-test-first cycle starts in Task 2.)

- [ ] **Step 5: Commit**

```bash
printf '.venv/\n__pycache__/\n*.egg-info/\n.pytest_cache/\n' > .gitignore
git add .gitignore pyproject.toml src tests
git commit -m "feat: project scaffolding with typer CLI skeleton"
```

---

### Task 2: ISBN module

**Files:**
- Create: `src/book_organizer/metadata/__init__.py` (empty), `src/book_organizer/metadata/isbn.py`
- Test: `tests/test_isbn.py`

**Interfaces:**
- Produces: `is_valid_isbn10(s: str) -> bool`, `is_valid_isbn13(s: str) -> bool`, `isbn10_to_isbn13(s: str) -> str`, `normalize_isbn(raw: str) -> str | None` (canonical ISBN-13 or None), `find_isbns(text: str | None) -> list[str]` (normalized, deduped, in order found).

- [ ] **Step 1: Write the failing tests**

`tests/test_isbn.py`:

```python
from book_organizer.metadata.isbn import (
    find_isbns,
    is_valid_isbn10,
    is_valid_isbn13,
    isbn10_to_isbn13,
    normalize_isbn,
)


def test_valid_isbn13():
    assert is_valid_isbn13("9780123456472")
    assert is_valid_isbn13("9780765382030")
    assert not is_valid_isbn13("9780123456473")  # bad check digit
    assert not is_valid_isbn13("978012345647")  # too short


def test_valid_isbn10():
    assert is_valid_isbn10("0306406152")
    assert is_valid_isbn10("080442957X")
    assert not is_valid_isbn10("0306406153")


def test_isbn10_to_isbn13():
    assert isbn10_to_isbn13("0306406152") == "9780306406157"


def test_normalize_spec_example():
    assert normalize_isbn("ISBN 978-0-123456-47-2") == "9780123456472"


def test_normalize_isbn10_converts():
    assert normalize_isbn("ISBN-10: 0-306-40615-2") == "9780306406157"


def test_normalize_invalid_returns_none():
    assert normalize_isbn("9780123456473") is None
    assert normalize_isbn("not an isbn") is None


def test_find_isbns_in_text():
    text = "see ISBN 978-0-765-38203-0 and again 9780765382030, junk 12345"
    assert find_isbns(text) == ["9780765382030"]
    assert find_isbns(None) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_isbn.py -v` — Expected: FAIL (ModuleNotFoundError).

- [ ] **Step 3: Implement**

`src/book_organizer/metadata/isbn.py`:

```python
import re

_PREFIX = re.compile(r"(?i)isbn(?:-1[03])?:?\s*")
_CANDIDATE = re.compile(r"[0-9][0-9Xx\- ]{8,16}[0-9Xx]")


def _clean(raw: str) -> str:
    return re.sub(r"[\s\-]", "", _PREFIX.sub("", raw)).upper()


def is_valid_isbn10(s: str) -> bool:
    if not re.fullmatch(r"[0-9]{9}[0-9X]", s):
        return False
    total = sum(
        (10 - i) * (10 if c == "X" else int(c)) for i, c in enumerate(s)
    )
    return total % 11 == 0


def is_valid_isbn13(s: str) -> bool:
    if not re.fullmatch(r"[0-9]{13}", s):
        return False
    total = sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(s))
    return total % 10 == 0


def isbn10_to_isbn13(s: str) -> str:
    core = "978" + s[:9]
    check = (10 - sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(core)) % 10) % 10
    return core + str(check)


def normalize_isbn(raw: str) -> str | None:
    s = _clean(raw)
    if is_valid_isbn13(s):
        return s
    if is_valid_isbn10(s):
        return isbn10_to_isbn13(s)
    return None


def find_isbns(text: str | None) -> list[str]:
    out: list[str] = []
    for m in _CANDIDATE.finditer(text or ""):
        n = normalize_isbn(m.group(0))
        if n and n not in out:
            out.append(n)
    return out
```

Also create empty `src/book_organizer/metadata/__init__.py`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_isbn.py -v` — Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add src/book_organizer/metadata tests/test_isbn.py
git commit -m "feat: ISBN validation, normalization, ISBN-10 to 13 conversion, text extraction"
```

---

### Task 3: Title/author/language normalization

**Files:**
- Create: `src/book_organizer/metadata/normalization.py`
- Test: `tests/test_normalization.py`

**Interfaces:**
- Produces: `normalize_title(s: str | None) -> str`, `normalize_author(s: str | None) -> str`, `normalize_language(code: str | None) -> str | None` (ISO 639-1).

- [ ] **Step 1: Write the failing tests**

`tests/test_normalization.py`:

```python
from book_organizer.metadata.normalization import (
    normalize_author,
    normalize_language,
    normalize_title,
)


def test_title_spec_example():
    assert normalize_title("The Three-Body Problem: A Novel") == "three body problem"


def test_title_strips_edition_and_format_markers():
    assert normalize_title("Dune (EPUB) 2nd Edition") == "dune"


def test_title_keeps_cjk():
    assert normalize_title("三体") == "三体"


def test_title_handles_none():
    assert normalize_title(None) == ""


def test_author_strips_diacritics_and_case():
    assert normalize_author("Gabriel García Márquez") == "gabriel garcia marquez"


def test_author_keeps_cjk():
    assert normalize_author("刘慈欣") == "刘慈欣"


def test_language_codes():
    assert normalize_language("eng") == "en"
    assert normalize_language("zho") == "zh"
    assert normalize_language("chi") == "zh"
    assert normalize_language("zh-CN") == "zh"
    assert normalize_language("EN") == "en"
    assert normalize_language(None) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_normalization.py -v` — Expected: FAIL (ModuleNotFoundError).

- [ ] **Step 3: Implement**

`src/book_organizer/metadata/normalization.py`:

```python
import re
import unicodedata

_MARKERS = re.compile(
    r"(?i)\b(\d+(?:st|nd|rd|th)\s+edition|revised edition|edition|ebook|epub|pdf|mobi|azw3?|retail|scan(?:ned)?)\b"
)
_ARTICLES = ("the ", "a ", "an ")

_LANG_MAP = {
    "eng": "en", "zho": "zh", "chi": "zh", "jpn": "ja", "kor": "ko",
    "fre": "fr", "fra": "fr", "ger": "de", "deu": "de", "spa": "es",
    "ita": "it", "rus": "ru", "por": "pt",
}


def normalize_title(s: str | None) -> str:
    s = unicodedata.normalize("NFKC", s or "")
    if ":" in s:
        head = s.split(":", 1)[0]
        if len(head.split()) >= 2:
            s = head
    s = _MARKERS.sub(" ", s).lower()
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"[_\s]+", " ", s).strip()
    for art in _ARTICLES:
        if s.startswith(art):
            s = s[len(art):]
            break
    return s


def normalize_author(s: str | None) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^\w\s]", " ", s.lower())
    return re.sub(r"\s+", " ", s).strip()


def normalize_language(code: str | None) -> str | None:
    if not code:
        return None
    c = code.strip().lower().replace("_", "-").split("-")[0]
    if len(c) == 2:
        return c
    return _LANG_MAP.get(c, c[:2] if c else None)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_normalization.py -v` — Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add src/book_organizer/metadata/normalization.py tests/test_normalization.py
git commit -m "feat: title, author, and language normalization"
```

---

### Task 4: Database connection, lock file, schema

**Files:**
- Create: `src/book_organizer/db/__init__.py` (empty), `src/book_organizer/db/database.py`
- Test: `tests/test_database.py`

**Interfaces:**
- Produces: `Database(path: Path)` context manager exposing `.conn` (sqlite3, `Row` factory, WAL, FKs on), `.init_schema()`, `.close()`; `LockError` raised when `db/.lock` is held. Schema: tables `files, works, editions, authors, author_aliases, work_authors, identifiers, metadata_sources, matches, scan_runs` exactly as spec §18 (with FKs, `matches.created_at`, indexes `idx_files_sha256`, `idx_editions_isbn13`, `idx_matches_file`).

- [ ] **Step 1: Write the failing tests**

`tests/test_database.py`:

```python
import pytest

from book_organizer.db.database import Database, LockError


def test_init_schema_creates_tables(tmp_path):
    with Database(tmp_path / "db" / "books.sqlite3") as db:
        db.init_schema()
        names = {
            r["name"]
            for r in db.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert {
        "files", "works", "editions", "authors", "author_aliases",
        "work_authors", "identifiers", "metadata_sources", "matches",
        "scan_runs",
    } <= names


def test_lock_prevents_second_instance(tmp_path):
    path = tmp_path / "db" / "books.sqlite3"
    with Database(path):
        with pytest.raises(LockError):
            Database(path)
    # released on close
    Database(path).close()


def test_wal_mode(tmp_path):
    with Database(tmp_path / "db" / "books.sqlite3") as db:
        mode = db.conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode == "wal"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_database.py -v` — Expected: FAIL (ModuleNotFoundError).

- [ ] **Step 3: Implement**

`src/book_organizer/db/database.py`:

```python
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
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
    FOREIGN KEY(matched_edition_id) REFERENCES editions(id)
);
CREATE INDEX IF NOT EXISTS idx_files_sha256 ON files(sha256);

CREATE TABLE IF NOT EXISTS works (
    id INTEGER PRIMARY KEY,
    canonical_title TEXT,
    original_title TEXT,
    original_language TEXT,
    description TEXT
);

CREATE TABLE IF NOT EXISTS editions (
    id INTEGER PRIMARY KEY,
    work_id INTEGER,
    isbn10 TEXT,
    isbn13 TEXT,
    publisher TEXT,
    publication_date TEXT,
    language TEXT,
    edition_name TEXT,
    FOREIGN KEY(work_id) REFERENCES works(id)
);
CREATE INDEX IF NOT EXISTS idx_editions_isbn13 ON editions(isbn13);

CREATE TABLE IF NOT EXISTS authors (
    id INTEGER PRIMARY KEY,
    canonical_name TEXT
);

CREATE TABLE IF NOT EXISTS author_aliases (
    id INTEGER PRIMARY KEY,
    author_id INTEGER,
    alias TEXT,
    FOREIGN KEY(author_id) REFERENCES authors(id)
);

CREATE TABLE IF NOT EXISTS work_authors (
    work_id INTEGER,
    author_id INTEGER,
    PRIMARY KEY(work_id, author_id)
);

CREATE TABLE IF NOT EXISTS identifiers (
    id INTEGER PRIMARY KEY,
    edition_id INTEGER,
    type TEXT,
    value TEXT,
    source TEXT,
    UNIQUE(type, value),
    FOREIGN KEY(edition_id) REFERENCES editions(id)
);

CREATE TABLE IF NOT EXISTS metadata_sources (
    id INTEGER PRIMARY KEY,
    edition_id INTEGER,
    provider TEXT,
    provider_id TEXT,
    retrieved_at TEXT,
    raw_json TEXT,
    UNIQUE(provider, provider_id),
    FOREIGN KEY(edition_id) REFERENCES editions(id)
);

CREATE TABLE IF NOT EXISTS matches (
    id INTEGER PRIMARY KEY,
    file_id INTEGER,
    edition_id INTEGER,
    score REAL,
    confidence REAL,
    resolver TEXT,
    evidence_json TEXT,
    status TEXT,
    created_at TEXT,
    FOREIGN KEY(file_id) REFERENCES files(id),
    FOREIGN KEY(edition_id) REFERENCES editions(id)
);
CREATE INDEX IF NOT EXISTS idx_matches_file ON matches(file_id);

CREATE TABLE IF NOT EXISTS scan_runs (
    id INTEGER PRIMARY KEY,
    started_at TEXT,
    completed_at TEXT,
    root_path TEXT,
    files_seen INTEGER,
    files_added INTEGER,
    files_changed INTEGER
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class LockError(RuntimeError):
    pass


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock_path = self.path.parent / ".lock"
        try:
            self._lock_fd = os.open(
                self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY
            )
            os.write(self._lock_fd, str(os.getpid()).encode())
        except FileExistsError:
            raise LockError(
                f"another book-organizer instance holds {self.lock_path} "
                "(delete it if that process crashed)"
            ) from None
        self.conn = sqlite3.connect(self.path, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")

    def init_schema(self) -> None:
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()
        os.close(self._lock_fd)
        self.lock_path.unlink(missing_ok=True)

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
```

Also create empty `src/book_organizer/db/__init__.py`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_database.py -v` — Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add src/book_organizer/db tests/test_database.py
git commit -m "feat: sqlite database with spec schema, WAL mode, exclusive lock file"
```

---

### Task 5: Database file-row helpers

**Files:**
- Modify: `src/book_organizer/db/database.py` (append methods to `Database`)
- Test: `tests/test_database.py` (append)

**Interfaces:**
- Consumes: `Database`, `_now()` from Task 4.
- Produces (methods on `Database`): `upsert_file(path: str, size: int, mtime: int, fmt: str) -> tuple[int, str]` where the str is `"new" | "changed" | "unchanged"` (changed resets sha256 to NULL and status to NEW); `set_hash(file_id: int, sha256: str) -> bool` (True if another file already has this hash; sets status DUPLICATE, else SCANNED); `set_status(file_id: int, status: str) -> None`; `set_raw_metadata(file_id: int, title, author, isbn, language) -> None` (all `str | None`); `files_with_status(status: str) -> list[sqlite3.Row]`; `start_scan_run(root: str) -> int`; `finish_scan_run(run_id: int, seen: int, added: int, changed: int) -> None`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_database.py`:

```python
def _mkdb(tmp_path):
    db = Database(tmp_path / "db" / "books.sqlite3")
    db.init_schema()
    return db


def test_upsert_file_lifecycle(tmp_path):
    with _mkdb(tmp_path) as db:
        fid, state = db.upsert_file("/x/a.epub", 100, 111, "epub")
        assert state == "new"
        assert db.upsert_file("/x/a.epub", 100, 111, "epub") == (fid, "unchanged")
        fid2, state2 = db.upsert_file("/x/a.epub", 200, 222, "epub")
        assert (fid2, state2) == (fid, "changed")
        row = db.conn.execute("SELECT * FROM files WHERE id=?", (fid,)).fetchone()
        assert row["sha256"] is None and row["status"] == "NEW"


def test_set_hash_marks_duplicates(tmp_path):
    with _mkdb(tmp_path) as db:
        a, _ = db.upsert_file("/x/a.epub", 1, 1, "epub")
        b, _ = db.upsert_file("/x/b.epub", 1, 1, "epub")
        assert db.set_hash(a, "abc") is False
        assert db.set_hash(b, "abc") is True
        rows = {r["path"]: r["status"] for r in db.conn.execute("SELECT * FROM files")}
        assert rows["/x/a.epub"] == "SCANNED"
        assert rows["/x/b.epub"] == "DUPLICATE"


def test_raw_metadata_and_status_queries(tmp_path):
    with _mkdb(tmp_path) as db:
        fid, _ = db.upsert_file("/x/a.epub", 1, 1, "epub")
        db.set_hash(fid, "abc")
        db.set_raw_metadata(fid, title="三体", author="刘慈欣", isbn="9780765382030", language="zh")
        db.set_status(fid, "IDENTIFIED")
        rows = db.files_with_status("IDENTIFIED")
        assert len(rows) == 1 and rows[0]["title_raw"] == "三体"


def test_scan_runs(tmp_path):
    with _mkdb(tmp_path) as db:
        rid = db.start_scan_run("/x")
        db.finish_scan_run(rid, seen=3, added=2, changed=1)
        row = db.conn.execute("SELECT * FROM scan_runs WHERE id=?", (rid,)).fetchone()
        assert row["files_seen"] == 3 and row["completed_at"] is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_database.py -v` — Expected: new tests FAIL (AttributeError: upsert_file), Task 4 tests still PASS.

- [ ] **Step 3: Implement**

Append these methods to `class Database` in `src/book_organizer/db/database.py`:

```python
    def upsert_file(self, path: str, size: int, mtime: int, fmt: str) -> tuple[int, str]:
        row = self.conn.execute(
            "SELECT id, size, mtime FROM files WHERE path=?", (path,)
        ).fetchone()
        now = _now()
        if row is None:
            cur = self.conn.execute(
                "INSERT INTO files (path, size, mtime, format, status, created_at, updated_at)"
                " VALUES (?,?,?,?,'NEW',?,?)",
                (path, size, mtime, fmt, now, now),
            )
            return cur.lastrowid, "new"
        if (row["size"], row["mtime"]) == (size, mtime):
            return row["id"], "unchanged"
        self.conn.execute(
            "UPDATE files SET size=?, mtime=?, sha256=NULL, status='NEW', updated_at=?"
            " WHERE id=?",
            (size, mtime, now, row["id"]),
        )
        return row["id"], "changed"

    def set_hash(self, file_id: int, sha256: str) -> bool:
        dup = self.conn.execute(
            "SELECT id FROM files WHERE sha256=? AND id != ?", (sha256, file_id)
        ).fetchone()
        status = "DUPLICATE" if dup else "SCANNED"
        self.conn.execute(
            "UPDATE files SET sha256=?, status=?, updated_at=? WHERE id=?",
            (sha256, status, _now(), file_id),
        )
        return dup is not None

    def set_status(self, file_id: int, status: str) -> None:
        self.conn.execute(
            "UPDATE files SET status=?, updated_at=? WHERE id=?",
            (status, _now(), file_id),
        )

    def set_raw_metadata(
        self,
        file_id: int,
        title: str | None,
        author: str | None,
        isbn: str | None,
        language: str | None,
    ) -> None:
        self.conn.execute(
            "UPDATE files SET title_raw=?, author_raw=?, isbn_raw=?, language_raw=?,"
            " updated_at=? WHERE id=?",
            (title, author, isbn, language, _now(), file_id),
        )

    def files_with_status(self, status: str) -> list:
        return self.conn.execute(
            "SELECT * FROM files WHERE status=? ORDER BY path", (status,)
        ).fetchall()

    def start_scan_run(self, root: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO scan_runs (started_at, root_path) VALUES (?,?)",
            (_now(), root),
        )
        return cur.lastrowid

    def finish_scan_run(self, run_id: int, seen: int, added: int, changed: int) -> None:
        self.conn.execute(
            "UPDATE scan_runs SET completed_at=?, files_seen=?, files_added=?,"
            " files_changed=? WHERE id=?",
            (_now(), seen, added, changed, run_id),
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_database.py -v` — Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add src/book_organizer/db/database.py tests/test_database.py
git commit -m "feat: file row helpers - upsert, hashing/duplicates, status, scan runs"
```

---

### Task 6: Config module

**Files:**
- Create: `src/book_organizer/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: pydantic models `LibraryConfig(root, incoming, quarantine: Path)`, `DatabaseConfig(path: Path)`, `ScanConfig(recursive: bool = True, formats: list[str] = ["epub","pdf"])`, `MatchingConfig(auto_accept=0.98, review_below=0.90, ai_resolve_below=0.75, unresolved_below=0.50)`, `ProviderConfig(enabled: bool = True)`, `ProvidersConfig(openlibrary: ProviderConfig)`, `CacheConfig(ttl_days: int = 30)`, `Config(library, database, scan, matching, providers, cache)`. Functions: `default_config(root: Path) -> Config`, `save_config(cfg: Config, root: Path) -> None` (writes `root/config.yaml`), `load_config(root: Path) -> Config`.

- [ ] **Step 1: Write the failing tests**

`tests/test_config.py`:

```python
from book_organizer.config import default_config, load_config, save_config


def test_default_roundtrip(tmp_path):
    cfg = default_config(tmp_path)
    save_config(cfg, tmp_path)
    loaded = load_config(tmp_path)
    assert loaded == cfg
    assert loaded.library.incoming == tmp_path.resolve() / "incoming"
    assert loaded.database.path == tmp_path.resolve() / "db" / "books.sqlite3"
    assert loaded.matching.auto_accept == 0.98
    assert loaded.matching.review_below == 0.90
    assert loaded.matching.ai_resolve_below == 0.75
    assert loaded.matching.unresolved_below == 0.50
    assert loaded.scan.formats == ["epub", "pdf"]
    assert loaded.providers.openlibrary.enabled is True
    assert loaded.cache.ttl_days == 30
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_config.py -v` — Expected: FAIL (ModuleNotFoundError).

- [ ] **Step 3: Implement**

`src/book_organizer/config.py`:

```python
from pathlib import Path

import yaml
from pydantic import BaseModel


class LibraryConfig(BaseModel):
    root: Path
    incoming: Path
    quarantine: Path


class DatabaseConfig(BaseModel):
    path: Path


class ScanConfig(BaseModel):
    recursive: bool = True
    formats: list[str] = ["epub", "pdf"]


class MatchingConfig(BaseModel):
    auto_accept: float = 0.98
    review_below: float = 0.90
    ai_resolve_below: float = 0.75
    unresolved_below: float = 0.50


class ProviderConfig(BaseModel):
    enabled: bool = True


class ProvidersConfig(BaseModel):
    openlibrary: ProviderConfig = ProviderConfig()


class CacheConfig(BaseModel):
    ttl_days: int = 30


class Config(BaseModel):
    library: LibraryConfig
    database: DatabaseConfig
    scan: ScanConfig = ScanConfig()
    matching: MatchingConfig = MatchingConfig()
    providers: ProvidersConfig = ProvidersConfig()
    cache: CacheConfig = CacheConfig()


def default_config(root: Path) -> Config:
    root = Path(root).resolve()
    return Config(
        library=LibraryConfig(
            root=root,
            incoming=root / "incoming",
            quarantine=root / "quarantine",
        ),
        database=DatabaseConfig(path=root / "db" / "books.sqlite3"),
    )


def save_config(cfg: Config, root: Path) -> None:
    (Path(root) / "config.yaml").write_text(
        yaml.safe_dump(cfg.model_dump(mode="json"), sort_keys=False, allow_unicode=True)
    )


def load_config(root: Path) -> Config:
    data = yaml.safe_load((Path(root) / "config.yaml").read_text())
    return Config.model_validate(data)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_config.py -v` — Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/book_organizer/config.py tests/test_config.py
git commit -m "feat: yaml config with spec-aligned matching thresholds"
```

---

### Task 7: init command

**Files:**
- Modify: `src/book_organizer/cli.py`
- Test: `tests/test_cli.py` (append)

**Interfaces:**
- Consumes: `default_config`, `save_config` (Task 6); `Database.init_schema` (Task 4).
- Produces: CLI command `book-organizer init ROOT` creating `incoming/ library/ quarantine/ duplicates/ covers/ cache/openlibrary/ reports/ db/`, `config.yaml`, and the schema-initialized SQLite DB.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cli.py`:

```python
def test_init_creates_layout(tmp_path):
    result = runner.invoke(app, ["init", str(tmp_path)])
    assert result.exit_code == 0, result.output
    for sub in [
        "incoming", "library", "quarantine", "duplicates", "covers",
        "cache/openlibrary", "reports", "db",
    ]:
        assert (tmp_path / sub).is_dir(), sub
    assert (tmp_path / "config.yaml").is_file()
    assert (tmp_path / "db" / "books.sqlite3").is_file()
    # idempotent
    assert runner.invoke(app, ["init", str(tmp_path)]).exit_code == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_cli.py -v` — Expected: FAIL (No such command 'init').

- [ ] **Step 3: Implement**

In `src/book_organizer/cli.py` add imports and the command:

```python
from pathlib import Path

from book_organizer.config import default_config, save_config
from book_organizer.db.database import Database

SUBDIRS = [
    "incoming", "library", "quarantine", "duplicates", "covers",
    "cache/openlibrary", "reports", "db",
]


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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_cli.py -v` — Expected: PASS (both tests).

- [ ] **Step 5: Commit**

```bash
git add src/book_organizer/cli.py tests/test_cli.py
git commit -m "feat: init command creates layout, config, and database"
```

---

### Task 8: Scanner and hashing modules

**Files:**
- Create: `src/book_organizer/scanner/__init__.py` (empty), `src/book_organizer/scanner/scanner.py`, `src/book_organizer/scanner/hashing.py`
- Test: `tests/test_scanner.py`

**Interfaces:**
- Produces: `FileInfo` dataclass `(path: str, size: int, mtime: int, extension: str)`; `iter_files(root: Path, formats: list[str], recursive: bool = True) -> Iterator[FileInfo]` (sorted paths, skips dotfiles and dot-directories, case-insensitive extension match); `sha256_file(path: Path) -> str`.

- [ ] **Step 1: Write the failing tests**

`tests/test_scanner.py`:

```python
import hashlib

from book_organizer.scanner.hashing import sha256_file
from book_organizer.scanner.scanner import iter_files


def _touch(p, content=b"x"):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(content)


def test_iter_files_filters_and_recurses(tmp_path):
    _touch(tmp_path / "a.epub")
    _touch(tmp_path / "B.PDF")
    _touch(tmp_path / "notes.txt")
    _touch(tmp_path / ".hidden.epub")
    _touch(tmp_path / ".git" / "c.epub")
    _touch(tmp_path / "sub" / "c.epub")
    found = [f.path for f in iter_files(tmp_path, ["epub", "pdf"])]
    assert found == sorted(
        [str(tmp_path / "B.PDF"), str(tmp_path / "a.epub"), str(tmp_path / "sub" / "c.epub")]
    )
    flat = [f.path for f in iter_files(tmp_path, ["epub", "pdf"], recursive=False)]
    assert str(tmp_path / "sub" / "c.epub") not in flat


def test_fileinfo_fields(tmp_path):
    _touch(tmp_path / "a.epub", b"hello")
    fi = next(iter_files(tmp_path, ["epub"]))
    assert fi.size == 5 and fi.extension == "epub" and fi.mtime > 0


def test_sha256_file(tmp_path):
    _touch(tmp_path / "a.epub", b"hello")
    assert sha256_file(tmp_path / "a.epub") == hashlib.sha256(b"hello").hexdigest()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_scanner.py -v` — Expected: FAIL (ModuleNotFoundError).

- [ ] **Step 3: Implement**

`src/book_organizer/scanner/scanner.py`:

```python
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


@dataclass
class FileInfo:
    path: str
    size: int
    mtime: int
    extension: str


def iter_files(
    root: Path, formats: list[str], recursive: bool = True
) -> Iterator[FileInfo]:
    root = Path(root)
    paths = root.rglob("*") if recursive else root.glob("*")
    fmts = {f.lower() for f in formats}
    for p in sorted(paths):
        if any(part.startswith(".") for part in p.relative_to(root).parts):
            continue
        ext = p.suffix.lower().lstrip(".")
        if p.is_file() and ext in fmts:
            st = p.stat()
            yield FileInfo(str(p), st.st_size, int(st.st_mtime), ext)
```

`src/book_organizer/scanner/hashing.py`:

```python
import hashlib
from pathlib import Path


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()
```

Also create empty `src/book_organizer/scanner/__init__.py`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_scanner.py -v` — Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add src/book_organizer/scanner tests/test_scanner.py
git commit -m "feat: recursive file scanner and chunked sha256 hashing"
```

---

### Task 9: scan command

**Files:**
- Modify: `src/book_organizer/cli.py`
- Test: `tests/test_cli.py` (append)

**Interfaces:**
- Consumes: `load_config` (Task 6), `Database` helpers (Task 5), `iter_files`/`sha256_file` (Task 8).
- Produces: `book-organizer scan [PATH] --root ROOT` (PATH defaults to `config.library.incoming`). New/changed files are hashed (binary duplicates → status DUPLICATE); unchanged files are skipped without hashing (incremental, resumable — spec §7.1 fast identity = path+size+mtime). Records a `scan_runs` row. Prints `seen=N added=N changed=N duplicates=N`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cli.py`:

```python
def _init_root(tmp_path):
    runner.invoke(app, ["init", str(tmp_path)])
    return tmp_path


def test_scan_incremental_and_duplicates(tmp_path):
    root = _init_root(tmp_path)
    (root / "incoming" / "a.epub").write_bytes(b"AAA")
    (root / "incoming" / "copy-of-a.epub").write_bytes(b"AAA")
    (root / "incoming" / "b.pdf").write_bytes(b"BBB")

    r1 = runner.invoke(app, ["scan", "--root", str(root)])
    assert r1.exit_code == 0, r1.output
    assert "seen=3 added=3" in r1.output and "duplicates=1" in r1.output

    r2 = runner.invoke(app, ["scan", "--root", str(root)])
    assert "added=0 changed=0" in r2.output
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_cli.py::test_scan_incremental_and_duplicates -v` — Expected: FAIL (No such command 'scan').

- [ ] **Step 3: Implement**

Add to `src/book_organizer/cli.py`:

```python
from typing import Optional

from book_organizer.config import load_config
from book_organizer.scanner.hashing import sha256_file
from book_organizer.scanner.scanner import iter_files

ROOT_OPTION = typer.Option(Path("."), "--root", help="Library root (with config.yaml)")


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
```

(The per-file commit makes interrupted scans resumable — spec §40.)

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_cli.py -v` — Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add src/book_organizer/cli.py tests/test_cli.py
git commit -m "feat: incremental scan command with sha256 duplicate detection"
```

---

### Task 10: EPUB extractor

**Files:**
- Create: `src/book_organizer/extractors/__init__.py` (empty), `src/book_organizer/extractors/base.py`, `src/book_organizer/extractors/epub.py`, `tests/helpers.py`
- Test: `tests/test_epub.py`

**Interfaces:**
- Produces: `ExtractedMetadata` pydantic model `(title: str | None, authors: list[str], isbns: list[str] (normalized ISBN-13), language: str | None (ISO 639-1), publisher: str | None, date: str | None, description: str | None)`; `ExtractionError(Exception)`; `extract_epub(path: Path) -> ExtractedMetadata`. Test helper `make_epub(path, title, author, isbn=None, language="en", publisher=None)` used by Tasks 12 and 20.

- [ ] **Step 1: Write the fixture helper and failing tests**

`tests/helpers.py`:

```python
import zipfile

CONTAINER = """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>"""


def make_epub(path, title, author, isbn=None, language="en", publisher=None):
    ident = f"<dc:identifier>urn:isbn:{isbn}</dc:identifier>" if isbn else ""
    pub = f"<dc:publisher>{publisher}</dc:publisher>" if publisher else ""
    opf = f"""<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>{title}</dc:title>
    <dc:creator>{author}</dc:creator>
    <dc:language>{language}</dc:language>
    {ident}{pub}
  </metadata>
  <manifest/><spine/>
</package>"""
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr("META-INF/container.xml", CONTAINER)
        z.writestr("content.opf", opf)
    return path
```

`tests/test_epub.py`:

```python
import pytest

from book_organizer.extractors.base import ExtractionError
from book_organizer.extractors.epub import extract_epub
from tests.helpers import make_epub


def test_extracts_full_metadata(tmp_path):
    p = make_epub(
        tmp_path / "t.epub",
        title="The Three-Body Problem",
        author="Liu Cixin",
        isbn="9780765382030",
        language="eng",
        publisher="Tor Books",
    )
    meta = extract_epub(p)
    assert meta.title == "The Three-Body Problem"
    assert meta.authors == ["Liu Cixin"]
    assert meta.isbns == ["9780765382030"]
    assert meta.language == "en"
    assert meta.publisher == "Tor Books"


def test_missing_optional_fields(tmp_path):
    p = make_epub(tmp_path / "t.epub", title="三体", author="刘慈欣", language="zh")
    meta = extract_epub(p)
    assert meta.title == "三体" and meta.isbns == []


def test_broken_zip_raises(tmp_path):
    p = tmp_path / "bad.epub"
    p.write_bytes(b"not a zip")
    with pytest.raises(ExtractionError):
        extract_epub(p)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_epub.py -v` — Expected: FAIL (ModuleNotFoundError).

- [ ] **Step 3: Implement**

`src/book_organizer/extractors/base.py`:

```python
from pydantic import BaseModel


class ExtractionError(Exception):
    pass


class ExtractedMetadata(BaseModel):
    title: str | None = None
    authors: list[str] = []
    isbns: list[str] = []
    language: str | None = None
    publisher: str | None = None
    date: str | None = None
    description: str | None = None
```

`src/book_organizer/extractors/epub.py`:

```python
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from book_organizer.extractors.base import ExtractedMetadata, ExtractionError
from book_organizer.metadata.isbn import find_isbns
from book_organizer.metadata.normalization import normalize_language

_CONTAINER_NS = "{urn:oasis:names:tc:opendocument:xmlns:container}"
_OPF_NS = "{http://www.idpf.org/2007/opf}"


def extract_epub(path: Path) -> ExtractedMetadata:
    try:
        with zipfile.ZipFile(path) as z:
            container = ET.fromstring(z.read("META-INF/container.xml"))
            rootfile = container.find(f".//{_CONTAINER_NS}rootfile")
            if rootfile is None:
                raise ExtractionError("no rootfile in container.xml")
            opf = ET.fromstring(z.read(rootfile.attrib["full-path"]))
    except (zipfile.BadZipFile, KeyError, ET.ParseError, OSError) as e:
        raise ExtractionError(str(e)) from e

    md = opf.find(f"{_OPF_NS}metadata")
    if md is None:
        raise ExtractionError("no metadata element in OPF")

    def dc(name: str) -> list[str]:
        return [
            el.text.strip()
            for el in md
            if el.tag.endswith("}" + name) and el.text and el.text.strip()
        ]

    isbns: list[str] = []
    for ident in dc("identifier") + dc("source"):
        for isbn in find_isbns(ident):
            if isbn not in isbns:
                isbns.append(isbn)

    titles, creators = dc("title"), dc("creator")
    langs, pubs, dates, descs = dc("language"), dc("publisher"), dc("date"), dc("description")
    return ExtractedMetadata(
        title=titles[0] if titles else None,
        authors=creators,
        isbns=isbns,
        language=normalize_language(langs[0]) if langs else None,
        publisher=pubs[0] if pubs else None,
        date=dates[0] if dates else None,
        description=descs[0] if descs else None,
    )
```

Also create empty `src/book_organizer/extractors/__init__.py`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_epub.py -v` — Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add src/book_organizer/extractors tests/helpers.py tests/test_epub.py
git commit -m "feat: EPUB OPF metadata extractor with ISBN and language normalization"
```

---

### Task 11: PDF extractor

**Files:**
- Create: `src/book_organizer/extractors/pdf.py`
- Modify: `tests/helpers.py` (append `make_pdf`)
- Test: `tests/test_pdf.py`

**Interfaces:**
- Consumes: `ExtractedMetadata`, `ExtractionError` (Task 10); `find_isbns` (Task 2).
- Produces: `extract_pdf(path: Path) -> ExtractedMetadata`. ISBN candidates come from metadata fields plus text of the first 5 pages (spec §8: no OCR). Test helper `make_pdf(path, title, author, text=None)`.

- [ ] **Step 1: Write the fixture helper and failing tests**

Append to `tests/helpers.py`:

```python
def make_pdf(path, title, author, text=None):
    import fitz

    doc = fitz.open()
    page = doc.new_page()
    if text:
        page.insert_text((72, 72), text)
    doc.set_metadata({"title": title, "author": author})
    doc.save(str(path))
    doc.close()
    return path
```

`tests/test_pdf.py`:

```python
import pytest

from book_organizer.extractors.base import ExtractionError
from book_organizer.extractors.pdf import extract_pdf
from tests.helpers import make_pdf


def test_extracts_metadata_and_page_isbn(tmp_path):
    p = make_pdf(
        tmp_path / "t.pdf",
        title="The Three-Body Problem",
        author="Liu Cixin",
        text="ISBN 978-0-765-38203-0",
    )
    meta = extract_pdf(p)
    assert meta.title == "The Three-Body Problem"
    assert meta.authors == ["Liu Cixin"]
    assert meta.isbns == ["9780765382030"]


def test_empty_metadata(tmp_path):
    p = make_pdf(tmp_path / "t.pdf", title="", author="")
    meta = extract_pdf(p)
    assert meta.title is None and meta.authors == []


def test_broken_pdf_raises(tmp_path):
    p = tmp_path / "bad.pdf"
    p.write_bytes(b"not a pdf")
    with pytest.raises(ExtractionError):
        extract_pdf(p)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_pdf.py -v` — Expected: FAIL (ModuleNotFoundError).

- [ ] **Step 3: Implement**

`src/book_organizer/extractors/pdf.py`:

```python
from pathlib import Path

import fitz

from book_organizer.extractors.base import ExtractedMetadata, ExtractionError
from book_organizer.metadata.isbn import find_isbns

_ISBN_SCAN_PAGES = 5


def extract_pdf(path: Path) -> ExtractedMetadata:
    try:
        doc = fitz.open(path)
    except Exception as e:  # fitz raises several undocumented types
        raise ExtractionError(str(e)) from e
    try:
        meta = doc.metadata or {}
        text_parts = [
            meta.get("title") or "",
            meta.get("subject") or "",
            meta.get("keywords") or "",
        ]
        for page in doc.pages(0, min(doc.page_count, _ISBN_SCAN_PAGES)):
            text_parts.append(page.get_text())
        author = (meta.get("author") or "").strip()
        return ExtractedMetadata(
            title=(meta.get("title") or "").strip() or None,
            authors=[author] if author else [],
            isbns=find_isbns(" ".join(text_parts)),
            date=(meta.get("creationDate") or "").strip() or None,
        )
    finally:
        doc.close()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_pdf.py -v` — Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add src/book_organizer/extractors/pdf.py tests/helpers.py tests/test_pdf.py
git commit -m "feat: PDF metadata extractor with first-pages ISBN scan"
```

---

### Task 12: extract command

**Files:**
- Modify: `src/book_organizer/cli.py`
- Test: `tests/test_cli.py` (append)

**Interfaces:**
- Consumes: `extract_epub` (Task 10), `extract_pdf` (Task 11), `find_isbns` (Task 2), `Database` helpers (Task 5), `make_epub` helper.
- Produces: `book-organizer extract --root ROOT [--force]` — processes files with status SCANNED (plus ERROR and IDENTIFIED when `--force`). Stores `title_raw`, `author_raw` ("; "-joined), `isbn_raw` (first normalized ISBN, falling back to `find_isbns(filename)`), `language_raw`; sets status IDENTIFIED, or ERROR on `ExtractionError`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cli.py`:

```python
from tests.helpers import make_epub


def test_extract_sets_metadata_and_states(tmp_path):
    root = _init_root(tmp_path)
    make_epub(
        root / "incoming" / "tbp.epub",
        title="The Three-Body Problem",
        author="Liu Cixin",
        isbn="9780765382030",
        language="en",
    )
    (root / "incoming" / "bad.epub").write_bytes(b"not a zip")
    runner.invoke(app, ["scan", "--root", str(root)])

    r = runner.invoke(app, ["extract", "--root", str(root)])
    assert r.exit_code == 0, r.output

    from book_organizer.config import load_config
    from book_organizer.db.database import Database

    cfg = load_config(root)
    with Database(cfg.database.path) as db:
        rows = {r["path"].split("/")[-1]: r for r in db.conn.execute("SELECT * FROM files")}
    assert rows["tbp.epub"]["status"] == "IDENTIFIED"
    assert rows["tbp.epub"]["title_raw"] == "The Three-Body Problem"
    assert rows["tbp.epub"]["author_raw"] == "Liu Cixin"
    assert rows["tbp.epub"]["isbn_raw"] == "9780765382030"
    assert rows["tbp.epub"]["language_raw"] == "en"
    assert rows["bad.epub"]["status"] == "ERROR"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_cli.py::test_extract_sets_metadata_and_states -v` — Expected: FAIL (No such command 'extract').

- [ ] **Step 3: Implement**

Add to `src/book_organizer/cli.py`:

```python
from book_organizer.extractors.base import ExtractionError
from book_organizer.extractors.epub import extract_epub
from book_organizer.extractors.pdf import extract_pdf
from book_organizer.metadata.isbn import find_isbns

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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_cli.py -v` — Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add src/book_organizer/cli.py tests/test_cli.py
git commit -m "feat: extract command stores raw metadata with ERROR handling"
```

---

### Task 13: Data models, provider base, and file cache

**Files:**
- Create: `src/book_organizer/metadata/models.py`, `src/book_organizer/providers/__init__.py` (empty), `src/book_organizer/providers/base.py`, `src/book_organizer/providers/cache.py`
- Test: `tests/test_cache.py`

**Interfaces:**
- Produces (`metadata/models.py`, per spec §28): `Author(name: str, aliases: list[str] = [])`, `Work(title: str, original_title: str | None, authors: list[Author], original_language: str | None)`, `Edition(work: Work, isbn10, isbn13, publisher, publication_date, language, edition_name: all str | None)`, `Candidate(provider: str, provider_id: str, edition: Edition, score: float = 0, confidence: float = 0, evidence: list[str] = [])`.
- Produces (`providers/base.py`): abstract `MetadataProvider` with `name: str`, `lookup_isbn(isbn: str) -> list[Candidate]`, `search(title: str, author: str | None = None, language: str | None = None) -> list[Candidate]`.
- Produces (`providers/cache.py`): `FileCache(directory: Path, ttl_days: int = 30)` with `get(key: str) -> dict | list | None` (None on miss/expired) and `put(key: str, response) -> None`. Records store `query`, `retrieved_at` (UTC ISO), `response` per spec §29.

- [ ] **Step 1: Write the failing tests**

`tests/test_cache.py`:

```python
import json
from datetime import datetime, timedelta, timezone

from book_organizer.providers.cache import FileCache


def test_roundtrip_and_miss(tmp_path):
    cache = FileCache(tmp_path)
    assert cache.get("isbn:9780765382030") is None
    cache.put("isbn:9780765382030", {"title": "三体"})
    assert cache.get("isbn:9780765382030") == {"title": "三体"}


def test_expiry(tmp_path):
    cache = FileCache(tmp_path, ttl_days=30)
    cache.put("search:x", {"a": 1})
    path = next(tmp_path.glob("*.json"))
    rec = json.loads(path.read_text())
    rec["retrieved_at"] = (
        datetime.now(timezone.utc) - timedelta(days=31)
    ).isoformat()
    path.write_text(json.dumps(rec))
    assert cache.get("search:x") is None


def test_key_sanitization(tmp_path):
    cache = FileCache(tmp_path)
    cache.put("search:three body/problem:liu cixin", {"ok": True})
    assert cache.get("search:three body/problem:liu cixin") == {"ok": True}
    assert all("/" not in p.name for p in tmp_path.glob("*.json"))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_cache.py -v` — Expected: FAIL (ModuleNotFoundError).

- [ ] **Step 3: Implement**

`src/book_organizer/metadata/models.py`:

```python
from pydantic import BaseModel


class Author(BaseModel):
    name: str
    aliases: list[str] = []


class Work(BaseModel):
    title: str
    original_title: str | None = None
    authors: list[Author] = []
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

`src/book_organizer/providers/base.py`:

```python
from abc import ABC, abstractmethod

from book_organizer.metadata.models import Candidate


class MetadataProvider(ABC):
    name: str

    @abstractmethod
    def lookup_isbn(self, isbn: str) -> list[Candidate]: ...

    @abstractmethod
    def search(
        self,
        title: str,
        author: str | None = None,
        language: str | None = None,
    ) -> list[Candidate]: ...
```

`src/book_organizer/providers/cache.py`:

```python
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path


class FileCache:
    def __init__(self, directory: Path, ttl_days: int = 30):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.ttl = timedelta(days=ttl_days)

    def _path(self, key: str) -> Path:
        safe = re.sub(r"[^a-z0-9._-]+", "_", key.lower())[:80]
        digest = hashlib.sha1(key.encode()).hexdigest()[:10]
        return self.directory / f"{safe}-{digest}.json"

    def get(self, key: str):
        p = self._path(key)
        if not p.exists():
            return None
        rec = json.loads(p.read_text())
        age_limit = datetime.now(timezone.utc) - self.ttl
        if datetime.fromisoformat(rec["retrieved_at"]) < age_limit:
            return None
        return rec["response"]

    def put(self, key: str, response) -> None:
        self._path(key).write_text(
            json.dumps(
                {
                    "query": key,
                    "retrieved_at": datetime.now(timezone.utc).isoformat(),
                    "response": response,
                },
                ensure_ascii=False,
            )
        )
```

Also create empty `src/book_organizer/providers/__init__.py`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_cache.py -v` — Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add src/book_organizer/metadata/models.py src/book_organizer/providers tests/test_cache.py
git commit -m "feat: candidate data models, provider interface, TTL file cache"
```

---

### Task 14: Open Library provider

**Files:**
- Create: `src/book_organizer/providers/openlibrary.py`
- Test: `tests/test_openlibrary.py`

**Interfaces:**
- Consumes: `MetadataProvider`, `Candidate`/`Edition`/`Work`/`Author` (Task 13), `FileCache` (Task 13), `normalize_language` (Task 3).
- Produces: `OpenLibraryProvider(client: httpx.Client | None = None, cache: FileCache | None = None)`. `client=None` = offline mode: cache hits still work, cache misses return `[]`. `lookup_isbn` uses `GET /api/books?bibkeys=ISBN:{isbn}&format=json&jscmd=data` (cache key `isbn:{isbn}`); `search` uses `GET /search.json?title=..&author=..&limit=10` (cache key `search:{title}:{author}` lowercased).

- [ ] **Step 1: Write the failing tests**

`tests/test_openlibrary.py`:

```python
import httpx

from book_organizer.providers.cache import FileCache
from book_organizer.providers.openlibrary import OpenLibraryProvider

ISBN_RESPONSE = {
    "ISBN:9780765382030": {
        "key": "/books/OL26831316M",
        "title": "The Three-Body Problem",
        "authors": [{"name": "Liu Cixin"}],
        "publishers": [{"name": "Tor Books"}],
        "publish_date": "2014",
        "identifiers": {"isbn_13": ["9780765382030"], "isbn_10": ["0765382032"]},
    }
}

SEARCH_RESPONSE = {
    "docs": [
        {
            "key": "/works/OL17091839W",
            "title": "The Three-Body Problem",
            "author_name": ["Liu Cixin"],
            "first_publish_year": 2008,
            "isbn": ["0765382032", "9780765382030"],
            "language": ["eng"],
        }
    ]
}


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_lookup_isbn_parses_candidate():
    def handler(request):
        assert request.url.path == "/api/books"
        return httpx.Response(200, json=ISBN_RESPONSE)

    provider = OpenLibraryProvider(client=_client(handler))
    [cand] = provider.lookup_isbn("9780765382030")
    assert cand.provider == "openlibrary"
    assert cand.provider_id == "/books/OL26831316M"
    assert cand.edition.isbn13 == "9780765382030"
    assert cand.edition.isbn10 == "0765382032"
    assert cand.edition.publisher == "Tor Books"
    assert cand.edition.work.title == "The Three-Body Problem"
    assert cand.edition.work.authors[0].name == "Liu Cixin"


def test_search_parses_candidates():
    def handler(request):
        assert request.url.path == "/search.json"
        return httpx.Response(200, json=SEARCH_RESPONSE)

    provider = OpenLibraryProvider(client=_client(handler))
    [cand] = provider.search("The Three-Body Problem", author="Liu Cixin")
    assert cand.edition.isbn13 == "9780765382030"
    assert cand.edition.language == "en"
    assert cand.edition.publication_date == "2008"


def test_cache_hit_avoids_network(tmp_path):
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(200, json=ISBN_RESPONSE)

    cache = FileCache(tmp_path)
    p1 = OpenLibraryProvider(client=_client(handler), cache=cache)
    p1.lookup_isbn("9780765382030")
    p2 = OpenLibraryProvider(client=_client(handler), cache=cache)
    p2.lookup_isbn("9780765382030")
    assert calls == ["/api/books"]  # second call served from cache


def test_offline_mode(tmp_path):
    provider = OpenLibraryProvider(client=None, cache=FileCache(tmp_path))
    assert provider.lookup_isbn("9780765382030") == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_openlibrary.py -v` — Expected: FAIL (ModuleNotFoundError).

- [ ] **Step 3: Implement**

`src/book_organizer/providers/openlibrary.py`:

```python
import httpx

from book_organizer.metadata.models import Author, Candidate, Edition, Work
from book_organizer.metadata.normalization import normalize_language
from book_organizer.providers.base import MetadataProvider
from book_organizer.providers.cache import FileCache

BASE = "https://openlibrary.org"


class OpenLibraryProvider(MetadataProvider):
    name = "openlibrary"

    def __init__(
        self,
        client: httpx.Client | None = None,
        cache: FileCache | None = None,
    ):
        self.client = client
        self.cache = cache

    def _get(self, key: str, url: str, params: dict) -> dict | None:
        if self.cache is not None:
            hit = self.cache.get(key)
            if hit is not None:
                return hit
        if self.client is None:  # offline
            return None
        resp = self.client.get(url, params=params, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        if self.cache is not None:
            self.cache.put(key, data)
        return data

    def lookup_isbn(self, isbn: str) -> list[Candidate]:
        data = self._get(
            f"isbn:{isbn}",
            f"{BASE}/api/books",
            {"bibkeys": f"ISBN:{isbn}", "format": "json", "jscmd": "data"},
        )
        rec = (data or {}).get(f"ISBN:{isbn}")
        if not rec:
            return []
        idents = rec.get("identifiers", {})
        edition = Edition(
            work=Work(
                title=rec.get("title", ""),
                authors=[Author(name=a["name"]) for a in rec.get("authors", [])],
            ),
            isbn13=(idents.get("isbn_13") or [isbn])[0],
            isbn10=(idents.get("isbn_10") or [None])[0],
            publisher=(rec.get("publishers") or [{}])[0].get("name"),
            publication_date=rec.get("publish_date"),
        )
        return [
            Candidate(
                provider=self.name,
                provider_id=rec.get("key", f"ISBN:{isbn}"),
                edition=edition,
            )
        ]

    def search(
        self,
        title: str,
        author: str | None = None,
        language: str | None = None,
    ) -> list[Candidate]:
        params: dict = {"title": title, "limit": 10}
        if author:
            params["author"] = author
        key = f"search:{title}:{author or ''}".lower()
        data = self._get(key, f"{BASE}/search.json", params)
        if not data:
            return []
        out: list[Candidate] = []
        for doc in data.get("docs", [])[:10]:
            isbn13 = next((i for i in doc.get("isbn", []) if len(i) == 13), None)
            langs = doc.get("language") or []
            year = doc.get("first_publish_year")
            edition = Edition(
                work=Work(
                    title=doc.get("title", ""),
                    authors=[Author(name=n) for n in doc.get("author_name", [])],
                ),
                isbn13=isbn13,
                publication_date=str(year) if year else None,
                language=normalize_language(langs[0]) if langs else None,
            )
            out.append(
                Candidate(
                    provider=self.name,
                    provider_id=doc.get("key", ""),
                    edition=edition,
                )
            )
        return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_openlibrary.py -v` — Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add src/book_organizer/providers/openlibrary.py tests/test_openlibrary.py
git commit -m "feat: Open Library provider with cache and offline mode"
```

---

### Task 15: Matching scorer and confidence bands

**Files:**
- Create: `src/book_organizer/matching/__init__.py` (empty), `src/book_organizer/matching/scorer.py`
- Test: `tests/test_matching.py`

**Interfaces:**
- Consumes: `Candidate` (Task 13), `normalize_title`/`normalize_author` (Task 3), `MatchingConfig` (Task 6).
- Produces: `LocalBook` dataclass `(title: str | None, authors: list[str], isbn13s: list[str], language: str | None, publisher: str | None = None, year: str | None = None)`; `score_candidate(local: LocalBook, cand: Candidate) -> tuple[float, list[str]]` (spec §14 weights; evidence strings: `exact_isbn`, `exact_title`, `title_sim>=0.95`, `title_sim>=0.85`, `exact_author`, `author_sim>=0.90`, `language_match`, `publisher_match`, `year_exact`, `year_close`, `conflict:isbn`, `conflict:author`, `conflict:language`, `conflict:year`); `confidence_from_score(score: float, evidence: list[str]) -> float` (spec §14 mapping: clamp(score/150), exact-ISBN-no-conflict → 0.99, ISBN conflict cap 0.40); `band(confidence: float, m: MatchingConfig) -> str` returning `AUTO_ACCEPT | HIGH_CONFIDENCE | REVIEW_RECOMMENDED | AI_RESOLUTION | UNRESOLVED`.

- [ ] **Step 1: Write the failing tests**

`tests/test_matching.py`:

```python
from book_organizer.config import MatchingConfig
from book_organizer.matching.scorer import (
    LocalBook,
    band,
    confidence_from_score,
    score_candidate,
)
from book_organizer.metadata.models import Author, Candidate, Edition, Work


def _cand(title="The Three-Body Problem", author="Liu Cixin", isbn13=None,
          language=None, publisher=None, date=None):
    return Candidate(
        provider="openlibrary",
        provider_id="x",
        edition=Edition(
            work=Work(title=title, authors=[Author(name=author)]),
            isbn13=isbn13,
            language=language,
            publisher=publisher,
            publication_date=date,
        ),
    )


LOCAL = LocalBook(
    title="The Three-Body Problem",
    authors=["Liu Cixin"],
    isbn13s=["9780765382030"],
    language="en",
    year="2014",
)


def test_exact_isbn_gives_099():
    score, ev = score_candidate(LOCAL, _cand(isbn13="9780765382030", language="en", date="2014"))
    assert "exact_isbn" in ev and "exact_title" in ev
    assert confidence_from_score(score, ev) == 0.99


def test_isbn_conflict_caps_confidence():
    score, ev = score_candidate(LOCAL, _cand(isbn13="9780306406157", language="en", date="2014"))
    assert "conflict:isbn" in ev
    assert confidence_from_score(score, ev) <= 0.40


def test_title_author_match_without_isbn():
    local = LocalBook(title="Three-Body Problem", authors=["Liu Cixin"],
                      isbn13s=[], language=None)
    score, ev = score_candidate(local, _cand())
    assert "exact_author" in ev
    assert score >= 65  # exact/near-exact title + exact author


def test_author_conflict_penalized():
    score_same, _ = score_candidate(LOCAL, _cand(author="Liu Cixin"))
    score_diff, ev = score_candidate(LOCAL, _cand(author="Stephen King"))
    assert "conflict:author" in ev and score_diff < score_same


def test_bands():
    m = MatchingConfig()
    assert band(0.99, m) == "AUTO_ACCEPT"
    assert band(0.95, m) == "HIGH_CONFIDENCE"
    assert band(0.80, m) == "REVIEW_RECOMMENDED"
    assert band(0.60, m) == "AI_RESOLUTION"
    assert band(0.30, m) == "UNRESOLVED"


def test_confidence_clamped():
    assert confidence_from_score(-50, []) == 0.0
    assert confidence_from_score(500, []) == 1.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_matching.py -v` — Expected: FAIL (ModuleNotFoundError).

- [ ] **Step 3: Implement**

`src/book_organizer/matching/scorer.py`:

```python
import re
from dataclasses import dataclass, field

from rapidfuzz import fuzz

from book_organizer.config import MatchingConfig
from book_organizer.metadata.models import Candidate
from book_organizer.metadata.normalization import normalize_author, normalize_title


@dataclass
class LocalBook:
    title: str | None = None
    authors: list[str] = field(default_factory=list)
    isbn13s: list[str] = field(default_factory=list)
    language: str | None = None
    publisher: str | None = None
    year: str | None = None


def _year(date: str | None) -> int | None:
    m = re.search(r"\d{4}", date or "")
    return int(m.group()) if m else None


def score_candidate(local: LocalBook, cand: Candidate) -> tuple[float, list[str]]:
    score = 0.0
    ev: list[str] = []
    e = cand.edition

    if e.isbn13 and local.isbn13s:
        if e.isbn13 in local.isbn13s:
            score += 100
            ev.append("exact_isbn")
        else:
            score -= 100
            ev.append("conflict:isbn")

    lt, ct = normalize_title(local.title), normalize_title(e.work.title)
    if lt and ct:
        if lt == ct:
            score += 40
            ev.append("exact_title")
        else:
            sim = fuzz.token_sort_ratio(lt, ct) / 100
            if sim >= 0.95:
                score += 35
                ev.append("title_sim>=0.95")
            elif sim >= 0.85:
                score += 25
                ev.append("title_sim>=0.85")

    la = [normalize_author(a) for a in local.authors]
    ca = [normalize_author(a.name) for a in e.work.authors]
    if la and ca:
        best = max(fuzz.token_sort_ratio(x, y) / 100 for x in la for y in ca)
        if best == 1.0:
            score += 30
            ev.append("exact_author")
        elif best >= 0.90:
            score += 25
            ev.append("author_sim>=0.90")
        elif best < 0.50:
            score -= 40
            ev.append("conflict:author")

    if local.language and e.language:
        if local.language == e.language:
            score += 10
            ev.append("language_match")
        else:
            score -= 15
            ev.append("conflict:language")

    if local.publisher and e.publisher:
        if normalize_title(local.publisher) == normalize_title(e.publisher):
            score += 8
            ev.append("publisher_match")

    ly, cy = _year(local.year), _year(e.publication_date)
    if ly and cy:
        diff = abs(ly - cy)
        if diff == 0:
            score += 7
            ev.append("year_exact")
        elif diff <= 1:
            score += 5
            ev.append("year_close")
        elif diff > 20:
            score -= 10
            ev.append("conflict:year")

    return score, ev


def confidence_from_score(score: float, evidence: list[str]) -> float:
    if "exact_isbn" in evidence and not any(e.startswith("conflict:") for e in evidence):
        return 0.99
    conf = max(0.0, min(score / 150, 1.0))
    if "conflict:isbn" in evidence:
        conf = min(conf, 0.40)
    return conf


def band(confidence: float, m: MatchingConfig) -> str:
    if confidence >= m.auto_accept:
        return "AUTO_ACCEPT"
    if confidence >= m.review_below:
        return "HIGH_CONFIDENCE"
    if confidence >= m.ai_resolve_below:
        return "REVIEW_RECOMMENDED"
    if confidence >= m.unresolved_below:
        return "AI_RESOLUTION"
    return "UNRESOLVED"
```

Also create empty `src/book_organizer/matching/__init__.py`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_matching.py -v` — Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add src/book_organizer/matching tests/test_matching.py
git commit -m "feat: deterministic scoring, confidence mapping, and bands per spec"
```

---

### Task 16: Database match-persistence helpers

**Files:**
- Modify: `src/book_organizer/db/database.py` (append methods)
- Test: `tests/test_database.py` (append)

**Interfaces:**
- Consumes: `Candidate` (Task 13), `Database` (Tasks 4–5).
- Produces (methods on `Database`): `save_candidate(cand: Candidate) -> int` (dedupes work by `canonical_title`, edition by `isbn13`; inserts authors/work_authors/identifiers/metadata_sources rows; returns edition id); `record_match(file_id: int, edition_id: int, score: float, confidence: float, resolver: str, evidence: list[str], band: str) -> None` (stores evidence as JSON array in `evidence_json`, band in `matches.status`); `set_file_match(file_id: int, edition_id: int | None, confidence: float, status: str) -> None`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_database.py`:

```python
from book_organizer.metadata.models import Author, Candidate, Edition, Work


def _tbp_candidate():
    return Candidate(
        provider="openlibrary",
        provider_id="/books/OL26831316M",
        edition=Edition(
            work=Work(title="The Three-Body Problem", authors=[Author(name="Liu Cixin")]),
            isbn13="9780765382030",
            publisher="Tor Books",
            publication_date="2014",
        ),
    )


def test_save_candidate_dedupes(tmp_path):
    with _mkdb(tmp_path) as db:
        e1 = db.save_candidate(_tbp_candidate())
        e2 = db.save_candidate(_tbp_candidate())
        assert e1 == e2
        assert db.conn.execute("SELECT COUNT(*) c FROM works").fetchone()["c"] == 1
        assert db.conn.execute("SELECT COUNT(*) c FROM authors").fetchone()["c"] == 1
        ident = db.conn.execute("SELECT * FROM identifiers").fetchone()
        assert (ident["type"], ident["value"]) == ("isbn13", "9780765382030")
        src = db.conn.execute("SELECT * FROM metadata_sources").fetchone()
        assert src["provider"] == "openlibrary" and src["edition_id"] == e1


def test_record_match_and_file_link(tmp_path):
    with _mkdb(tmp_path) as db:
        fid, _ = db.upsert_file("/x/a.epub", 1, 1, "epub")
        eid = db.save_candidate(_tbp_candidate())
        db.record_match(fid, eid, 140.0, 0.99, "deterministic",
                        ["exact_isbn", "exact_title"], "AUTO_ACCEPT")
        db.set_file_match(fid, eid, 0.99, "MATCHED")
        m = db.conn.execute("SELECT * FROM matches").fetchone()
        assert m["status"] == "AUTO_ACCEPT" and '"exact_isbn"' in m["evidence_json"]
        f = db.conn.execute("SELECT * FROM files WHERE id=?", (fid,)).fetchone()
        assert f["matched_edition_id"] == eid and f["status"] == "MATCHED"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_database.py -v` — Expected: new tests FAIL (AttributeError: save_candidate).

- [ ] **Step 3: Implement**

Add at the top of `src/book_organizer/db/database.py`: `import json` and `from book_organizer.metadata.models import Candidate`. Append methods to `class Database`:

```python
    def save_candidate(self, cand: Candidate) -> int:
        e = cand.edition
        w = e.work
        row = self.conn.execute(
            "SELECT id FROM works WHERE canonical_title=?", (w.title,)
        ).fetchone()
        if row:
            work_id = row["id"]
        else:
            work_id = self.conn.execute(
                "INSERT INTO works (canonical_title, original_title, original_language)"
                " VALUES (?,?,?)",
                (w.title, w.original_title, w.original_language),
            ).lastrowid
        for a in w.authors:
            arow = self.conn.execute(
                "SELECT id FROM authors WHERE canonical_name=?", (a.name,)
            ).fetchone()
            author_id = (
                arow["id"]
                if arow
                else self.conn.execute(
                    "INSERT INTO authors (canonical_name) VALUES (?)", (a.name,)
                ).lastrowid
            )
            self.conn.execute(
                "INSERT OR IGNORE INTO work_authors (work_id, author_id) VALUES (?,?)",
                (work_id, author_id),
            )
        erow = None
        if e.isbn13:
            erow = self.conn.execute(
                "SELECT id FROM editions WHERE isbn13=?", (e.isbn13,)
            ).fetchone()
        if erow:
            edition_id = erow["id"]
        else:
            edition_id = self.conn.execute(
                "INSERT INTO editions (work_id, isbn10, isbn13, publisher,"
                " publication_date, language, edition_name) VALUES (?,?,?,?,?,?,?)",
                (work_id, e.isbn10, e.isbn13, e.publisher, e.publication_date,
                 e.language, e.edition_name),
            ).lastrowid
        if e.isbn13:
            self.conn.execute(
                "INSERT OR IGNORE INTO identifiers (edition_id, type, value, source)"
                " VALUES (?,'isbn13',?,?)",
                (edition_id, e.isbn13, cand.provider),
            )
        self.conn.execute(
            "INSERT OR IGNORE INTO metadata_sources (edition_id, provider,"
            " provider_id, retrieved_at) VALUES (?,?,?,?)",
            (edition_id, cand.provider, cand.provider_id, _now()),
        )
        return edition_id

    def record_match(
        self,
        file_id: int,
        edition_id: int,
        score: float,
        confidence: float,
        resolver: str,
        evidence: list[str],
        band: str,
    ) -> None:
        self.conn.execute(
            "INSERT INTO matches (file_id, edition_id, score, confidence, resolver,"
            " evidence_json, status, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (file_id, edition_id, score, confidence, resolver,
             json.dumps(evidence), band, _now()),
        )

    def set_file_match(
        self,
        file_id: int,
        edition_id: int | None,
        confidence: float,
        status: str,
    ) -> None:
        self.conn.execute(
            "UPDATE files SET matched_edition_id=?, match_confidence=?, status=?,"
            " updated_at=? WHERE id=?",
            (edition_id, confidence, status, _now(), file_id),
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_database.py -v` — Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add src/book_organizer/db/database.py tests/test_database.py
git commit -m "feat: persist candidates, matches, and file match links"
```

---

### Task 17: match command

**Files:**
- Modify: `src/book_organizer/cli.py`
- Test: `tests/test_cli.py` (append)

**Interfaces:**
- Consumes: `OpenLibraryProvider`/`FileCache` (Tasks 13–14), scorer functions (Task 15), DB persistence (Task 16).
- Produces: `book-organizer match --root ROOT [--offline]` — for each IDENTIFIED file: build `LocalBook` (title falls back to filename stem; authors split on ";"), get candidates via `lookup_isbn` then `search` fallback, score all, apply tie-break (top two within 10 points and band would be AUTO_ACCEPT/HIGH_CONFIDENCE → demote to REVIEW_RECOMMENDED with evidence `ambiguous:tie`), persist best candidate + match row, set file status: AUTO_ACCEPT/HIGH_CONFIDENCE → MATCHED, REVIEW_RECOMMENDED/AI_RESOLUTION → REVIEW, else UNRESOLVED. No candidates → UNRESOLVED. Cache dir: `{library.root}/cache/openlibrary`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cli.py` (uses a pre-seeded cache with `--offline` so no network and no mocking of the CLI's client):

```python
OL_ISBN_RESPONSE = {
    "ISBN:9780765382030": {
        "key": "/books/OL26831316M",
        "title": "The Three-Body Problem",
        "authors": [{"name": "Liu Cixin"}],
        "publishers": [{"name": "Tor Books"}],
        "publish_date": "2014",
        "identifiers": {"isbn_13": ["9780765382030"]},
    }
}


def test_match_offline_with_seeded_cache(tmp_path):
    from book_organizer.config import load_config
    from book_organizer.db.database import Database
    from book_organizer.providers.cache import FileCache

    root = _init_root(tmp_path)
    make_epub(
        root / "incoming" / "tbp.epub",
        title="The Three-Body Problem",
        author="Liu Cixin",
        isbn="9780765382030",
        language="en",
    )
    make_epub(root / "incoming" / "mystery.epub", title="zzz no such book qqq",
              author="Nobody")
    runner.invoke(app, ["scan", "--root", str(root)])
    runner.invoke(app, ["extract", "--root", str(root)])

    FileCache(root / "cache" / "openlibrary").put(
        "isbn:9780765382030", OL_ISBN_RESPONSE
    )
    r = runner.invoke(app, ["match", "--root", str(root), "--offline"])
    assert r.exit_code == 0, r.output

    cfg = load_config(root)
    with Database(cfg.database.path) as db:
        rows = {r["path"].split("/")[-1]: r for r in db.conn.execute("SELECT * FROM files")}
        assert rows["tbp.epub"]["status"] == "MATCHED"
        assert rows["tbp.epub"]["match_confidence"] == 0.99
        assert rows["mystery.epub"]["status"] == "UNRESOLVED"
        m = db.conn.execute("SELECT * FROM matches").fetchone()
        assert m["status"] == "AUTO_ACCEPT" and m["resolver"] == "deterministic"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_cli.py::test_match_offline_with_seeded_cache -v` — Expected: FAIL (No such command 'match').

- [ ] **Step 3: Implement**

Add to `src/book_organizer/cli.py`:

```python
import httpx

from book_organizer import __version__
from book_organizer.matching.scorer import (
    LocalBook,
    band,
    confidence_from_score,
    score_candidate,
)
from book_organizer.providers.cache import FileCache
from book_organizer.providers.openlibrary import OpenLibraryProvider

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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_cli.py -v` — Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add src/book_organizer/cli.py tests/test_cli.py
git commit -m "feat: match command with tie-break, band statuses, offline mode"
```

---

### Task 18: report module and command

**Files:**
- Create: `src/book_organizer/reports/__init__.py` (empty), `src/book_organizer/reports/report.py`
- Modify: `src/book_organizer/cli.py`
- Test: `tests/test_report.py`

**Interfaces:**
- Consumes: `Database` (Tasks 4–5, 16).
- Produces: `build_report(db: Database) -> dict` with integer keys `files_scanned, exact_isbn_matches, high_confidence, needs_review, duplicates, unresolved, errors`; CLI `book-organizer report --root ROOT [--json]` (rich table by default, raw JSON with `--json`).

- [ ] **Step 1: Write the failing tests**

`tests/test_report.py`:

```python
import json

from typer.testing import CliRunner

from book_organizer.cli import app
from book_organizer.db.database import Database
from book_organizer.reports.report import build_report

runner = CliRunner()


def _seed(tmp_path):
    db = Database(tmp_path / "db" / "books.sqlite3")
    db.init_schema()
    for i, status in enumerate(
        ["MATCHED", "MATCHED", "REVIEW", "DUPLICATE", "UNRESOLVED", "ERROR"]
    ):
        fid, _ = db.upsert_file(f"/x/{i}.epub", 1, 1, "epub")
        db.set_status(fid, status)
    db.record_match(1, 1, 140, 0.99, "deterministic", ["exact_isbn"], "AUTO_ACCEPT")
    db.record_match(2, 2, 90, 0.93, "deterministic", ["exact_title", "exact_author"],
                    "HIGH_CONFIDENCE")
    db.record_match(3, 3, 60, 0.80, "deterministic", ["title_sim>=0.85"],
                    "REVIEW_RECOMMENDED")
    db.conn.commit()
    return db


def test_build_report_counts(tmp_path):
    with _seed(tmp_path) as db:
        rep = build_report(db)
    assert rep == {
        "files_scanned": 6,
        "exact_isbn_matches": 1,
        "high_confidence": 1,
        "needs_review": 1,
        "duplicates": 1,
        "unresolved": 1,
        "errors": 1,
    }


def test_report_json_command(tmp_path):
    _seed(tmp_path).close()
    from book_organizer.config import default_config, save_config

    save_config(default_config(tmp_path), tmp_path)
    r = runner.invoke(app, ["report", "--root", str(tmp_path), "--json"])
    assert r.exit_code == 0, r.output
    assert json.loads(r.output)["files_scanned"] == 6
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_report.py -v` — Expected: FAIL (ModuleNotFoundError).

- [ ] **Step 3: Implement**

`src/book_organizer/reports/report.py`:

```python
from book_organizer.db.database import Database


def build_report(db: Database) -> dict:
    def one(sql: str) -> int:
        return db.conn.execute(sql).fetchone()[0]

    return {
        "files_scanned": one("SELECT COUNT(*) FROM files"),
        "exact_isbn_matches": one(
            "SELECT COUNT(*) FROM matches WHERE evidence_json LIKE '%\"exact_isbn\"%'"
        ),
        "high_confidence": one(
            "SELECT COUNT(*) FROM matches WHERE status IN"
            " ('AUTO_ACCEPT','HIGH_CONFIDENCE')"
            " AND evidence_json NOT LIKE '%\"exact_isbn\"%'"
        ),
        "needs_review": one("SELECT COUNT(*) FROM files WHERE status='REVIEW'"),
        "duplicates": one("SELECT COUNT(*) FROM files WHERE status='DUPLICATE'"),
        "unresolved": one("SELECT COUNT(*) FROM files WHERE status='UNRESOLVED'"),
        "errors": one("SELECT COUNT(*) FROM files WHERE status='ERROR'"),
    }
```

Add to `src/book_organizer/cli.py`:

```python
import json as _json

from rich.console import Console
from rich.table import Table

from book_organizer.reports.report import build_report


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
```

Also create empty `src/book_organizer/reports/__init__.py`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_report.py -v` — Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add src/book_organizer/reports src/book_organizer/cli.py tests/test_report.py
git commit -m "feat: report module and command with table and JSON output"
```

---

### Task 19: planner module and plan command

**Files:**
- Create: `src/book_organizer/planner/__init__.py` (empty), `src/book_organizer/planner/planner.py`
- Modify: `src/book_organizer/cli.py`
- Test: `tests/test_planner.py`

**Interfaces:**
- Consumes: `Database` (Tasks 4–5, 16).
- Produces: `generate_plan(db: Database, reports_dir: Path) -> Path` writing `reports/plan-<plan_id>.json` with `{"plan_id", "created_at", "actions": [...]}`. Actions per spec §23: MATCHED files → `import` (with `metadata_changes` from the matched edition/work), DUPLICATE → `mark_duplicate`, UNRESOLVED → `quarantine`; every action carries `preconditions: {sha256, size, mtime}`. CLI `book-organizer plan --root ROOT` prints the plan path and action count. Nothing else is written — no filesystem mutation.

- [ ] **Step 1: Write the failing tests**

`tests/test_planner.py`:

```python
import json

from book_organizer.db.database import Database
from book_organizer.metadata.models import Author, Candidate, Edition, Work
from book_organizer.planner.planner import generate_plan


def test_generate_plan_actions(tmp_path):
    with Database(tmp_path / "db" / "books.sqlite3") as db:
        db.init_schema()
        fid, _ = db.upsert_file("/x/tbp.epub", 10, 1, "epub")
        db.set_hash(fid, "aaa")
        eid = db.save_candidate(
            Candidate(
                provider="openlibrary",
                provider_id="/books/OL26831316M",
                edition=Edition(
                    work=Work(title="The Three-Body Problem",
                              authors=[Author(name="Liu Cixin")]),
                    isbn13="9780765382030",
                    publisher="Tor Books",
                    publication_date="2014",
                ),
            )
        )
        db.set_file_match(fid, eid, 0.99, "MATCHED")

        did, _ = db.upsert_file("/x/dup.epub", 10, 1, "epub")
        db.set_hash(did, "aaa")  # duplicate of tbp.epub
        uid, _ = db.upsert_file("/x/unknown.epub", 5, 1, "epub")
        db.set_hash(uid, "bbb")
        db.set_status(uid, "UNRESOLVED")
        db.conn.commit()

        out = generate_plan(db, tmp_path / "reports")

    plan = json.loads(out.read_text())
    assert out.name.startswith("plan-") and plan["plan_id"] in out.name
    actions = {a["file"]: a for a in plan["actions"]}
    assert actions["/x/tbp.epub"]["action"] == "import"
    assert actions["/x/tbp.epub"]["preconditions"] == {
        "sha256": "aaa", "size": 10, "mtime": 1,
    }
    assert actions["/x/tbp.epub"]["metadata_changes"]["isbn13"] == "9780765382030"
    assert actions["/x/tbp.epub"]["metadata_changes"]["title"] == "The Three-Body Problem"
    assert actions["/x/dup.epub"]["action"] == "mark_duplicate"
    assert actions["/x/unknown.epub"]["action"] == "quarantine"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_planner.py -v` — Expected: FAIL (ModuleNotFoundError).

- [ ] **Step 3: Implement**

`src/book_organizer/planner/planner.py`:

```python
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from book_organizer.db.database import Database


def _preconditions(row) -> dict:
    return {"sha256": row["sha256"], "size": row["size"], "mtime": row["mtime"]}


def generate_plan(db: Database, reports_dir: Path) -> Path:
    now = datetime.now(timezone.utc)
    plan_id = now.strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:8]
    actions: list[dict] = []

    matched = db.conn.execute(
        "SELECT f.*, e.isbn13, e.publisher, e.publication_date,"
        " w.canonical_title"
        " FROM files f"
        " JOIN editions e ON e.id = f.matched_edition_id"
        " JOIN works w ON w.id = e.work_id"
        " WHERE f.status = 'MATCHED' ORDER BY f.path"
    ).fetchall()
    for r in matched:
        actions.append(
            {
                "file": r["path"],
                "action": "import",
                "preconditions": _preconditions(r),
                "metadata_changes": {
                    "title": r["canonical_title"],
                    "isbn13": r["isbn13"],
                    "publisher": r["publisher"],
                    "publication_date": r["publication_date"],
                },
            }
        )
    for status, action in [("DUPLICATE", "mark_duplicate"), ("UNRESOLVED", "quarantine")]:
        for r in db.files_with_status(status):
            actions.append(
                {"file": r["path"], "action": action, "preconditions": _preconditions(r)}
            )

    plan = {"plan_id": plan_id, "created_at": now.isoformat(), "actions": actions}
    reports_dir = Path(reports_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)
    out = reports_dir / f"plan-{plan_id}.json"
    out.write_text(json.dumps(plan, ensure_ascii=False, indent=2))
    return out
```

Add to `src/book_organizer/cli.py`:

```python
from book_organizer.planner.planner import generate_plan


@app.command()
def plan(root: Path = ROOT_OPTION) -> None:
    """Generate a reviewable dry-run plan (writes reports/plan-*.json only)."""
    cfg = load_config(root)
    with Database(cfg.database.path) as db:
        out = generate_plan(db, cfg.library.root / "reports")
        n = len(_json.loads(out.read_text())["actions"])
    typer.echo(f"plan written: {out} ({n} actions). No files were modified.")
```

Also create empty `src/book_organizer/planner/__init__.py`.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_planner.py -v` — Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/book_organizer/planner src/book_organizer/cli.py tests/test_planner.py
git commit -m "feat: dry-run plan generation with per-action preconditions"
```

---

### Task 20: End-to-end integration test

**Files:**
- Test: `tests/test_e2e.py`

**Interfaces:**
- Consumes: everything. No new production code — this task validates the full pipeline and the spec §40 definition of done for the MVP slice.

- [ ] **Step 1: Write the test**

`tests/test_e2e.py`:

```python
import json

from typer.testing import CliRunner

from book_organizer.cli import app
from book_organizer.providers.cache import FileCache
from tests.helpers import make_epub, make_pdf
from tests.test_cli import OL_ISBN_RESPONSE

runner = CliRunner()


def test_full_pipeline_offline(tmp_path):
    assert runner.invoke(app, ["init", str(tmp_path)]).exit_code == 0
    incoming = tmp_path / "incoming"
    make_epub(incoming / "tbp.epub", title="The Three-Body Problem",
              author="Liu Cixin", isbn="9780765382030", language="en")
    # different bytes (extra publisher field), same ISBN — an edition-level
    # duplicate, NOT a binary duplicate
    make_epub(incoming / "tbp-copy.epub", title="The Three-Body Problem",
              author="Liu Cixin", isbn="9780765382030", language="en",
              publisher="Tor Books")
    make_pdf(incoming / "unknown.pdf", title="", author="")
    # binary duplicate: identical bytes
    (incoming / "tbp-exact-dup.epub").write_bytes((incoming / "tbp.epub").read_bytes())

    FileCache(tmp_path / "cache" / "openlibrary").put(
        "isbn:9780765382030", OL_ISBN_RESPONSE
    )

    for cmd in (
        ["scan", "--root", str(tmp_path)],
        ["extract", "--root", str(tmp_path)],
        ["match", "--root", str(tmp_path), "--offline"],
    ):
        result = runner.invoke(app, cmd)
        assert result.exit_code == 0, (cmd, result.output)

    # scan is idempotent / resumable
    r = runner.invoke(app, ["scan", "--root", str(tmp_path)])
    assert "added=0 changed=0" in r.output

    r = runner.invoke(app, ["report", "--root", str(tmp_path), "--json"])
    rep = json.loads(r.output)
    assert rep["files_scanned"] == 4
    assert rep["exact_isbn_matches"] == 2  # tbp + tbp-copy
    assert rep["duplicates"] == 1
    assert rep["unresolved"] == 1  # unknown.pdf, offline, no candidates

    r = runner.invoke(app, ["plan", "--root", str(tmp_path)])
    assert r.exit_code == 0 and "No files were modified" in r.output
    plan_file = next((tmp_path / "reports").glob("plan-*.json"))
    plan = json.loads(plan_file.read_text())
    by_action = {}
    for a in plan["actions"]:
        by_action.setdefault(a["action"], []).append(a)
        assert a["preconditions"]["sha256"]
    assert len(by_action["import"]) == 2
    assert len(by_action["mark_duplicate"]) == 1
    assert len(by_action["quarantine"]) == 1

    # originals untouched
    names = sorted(p.name for p in incoming.iterdir())
    assert names == ["tbp-copy.epub", "tbp-exact-dup.epub", "tbp.epub", "unknown.pdf"]
```

- [ ] **Step 2: Run the full suite**

Run: `.venv/bin/pytest -v` — Expected: PASS (all tests, all tasks).

- [ ] **Step 3: Manual smoke check**

```bash
.venv/bin/book-organizer --help
```

Expected: help lists `init scan extract match report plan`.

- [ ] **Step 4: Commit**

```bash
git add tests/test_e2e.py
git commit -m "test: end-to-end pipeline integration test"
```

---

## Self-Review (completed during authoring)

- **Spec §33 MVP coverage:** init→T7, scan→T9, SHA256→T8/T9, EPUB→T10, PDF→T11, ISBN normalization→T2, SQLite→T4/T5/T16, Open Library→T14, fuzzy matching→T15/T17, confidence→T15, report→T18, dry-run plan→T19. NO AI, NO file modification anywhere. ✓
- **Spec §40 slice:** interrupted-scan resume (per-file commits, T9), no duplicate processing (fast identity, T5/T9), API caching (T13/T14), originals preserved (asserted in T20), evidence + confidence on every match (T15/T16), deterministic plan (T19), no AI needed for ISBN matches (T15 override), Calibre-ready schema (spec §18 verbatim, T4). ✓
- **Type consistency check:** `Candidate.provider/provider_id` used consistently (T13/T14/T16); `files_with_status` returns Rows used in T12/T17/T19; `MatchingConfig` field names identical in T6 and T15; `OL_ISBN_RESPONSE` defined in T17's test file and imported by T20. ✓
- **Deferred beyond MVP (spec Phase 2+):** review TUI, resolve command, commit/rollback, Calibre import, Google Books/Crossref/Douban providers, rate limiting wrappers, structured logging, `--csv/--html` report formats.
