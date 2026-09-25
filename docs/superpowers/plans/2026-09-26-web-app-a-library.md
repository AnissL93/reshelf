# Reshelf Web App (Sub-project A) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the `reshelf` CLI into a self-hosted single-user web library app: browse, search, correct metadata, convert Kindle/TXT/DjVu books, and drive the whole pipeline from a browser.

**Architecture:** Per-book JSON sidecar files become the source of truth for book metadata; SQLite is demoted to a deletable, rebuildable index (paths, hashes, provider candidates, FTS5 search, jobs). Pipeline logic moves out of typer command bodies into `reshelf/pipeline.py` functions that take a progress callback, so both the CLI and a single-slot in-process job runner can call them. FastAPI serves a JSON API plus a built React SPA.

**Tech Stack:** Python 3.11+, FastAPI, uvicorn, pydantic v2, SQLite (FTS5), typer, pymupdf, `mobi` (GPL-3.0), `ddjvu` (system binary), React 18 + Vite + TypeScript.

**Spec:** `docs/superpowers/specs/2026-09-26-web-app-a-library.md`
**Parent:** `docs/superpowers/specs/2026-09-26-web-app-decomposition.md`

## Global Constraints

- **Python >= 3.11.** Existing floor in `pyproject.toml`; do not raise it.
- **No Calibre.** No `ebook-convert` and no new `calibredb` usage anywhere in the pipeline, converters, or web app. The pre-existing `reshelf calibre-export` command is an unrelated shipped feature and stays untouched.
- **JSON sidecars are the source of truth.** Any read of book metadata for display, planning, or export reads the sidecar, never the `works`/`editions` tables. Those tables are a provider-candidate cache.
- **`source.resolver == "human"` is sticky.** `match` and `resolve` must skip such books unless explicitly forced.
- **Nothing under `incoming/` is modified** unless `library.commit_mode == "move"` (a move) or `metadata.layout == "sidecar"` (a `.json` beside the original). Book bytes under `incoming/` are never rewritten.
- **Originals are never deleted by conversion.** Derived files are additive.
- **AI is off unless `ai.provider` is set.** With it unset, no code path may contact a model.
- **The index is deletable.** `reshelf reindex` must fully restore the UI from sidecars plus a rescan, with no data loss.
- **Bind to `127.0.0.1` by default.** No auth is implemented, so the default must not be `0.0.0.0`.
- Tests are `pytest`, in `tests/`, matching the existing suite's style. No JS test framework.
- Existing CLI behaviour and the existing test suite must keep passing after every task.

## Known Hazards (read before Task 1)

1. **`Database.__init__` takes an exclusive lock** (`os.open(..., O_CREAT | O_EXCL)`) at `src/reshelf/db/database.py:130` and raises `LockError`. A long-running `reshelf serve` holds that lock for its lifetime. This is correct (one writer), but it means: the server opens **one** `Database` and shares it with the job worker thread, so the connection needs `check_same_thread=False` plus a `threading.Lock` around use. Task 15 does this.
2. **`generate_plan` (`src/reshelf/planner/planner.py:13`) reads metadata from a `files`/`editions`/`works` join.** Under the sidecar model it must read sidecars. Task 9 changes this.
3. **`committer.py:8` imports `reshelf.calibre.convert`** and converts Kindle files during commit. Conversion becomes a per-book action, so that hook is removed in Task 9 and `calibre/convert.py` is deleted in Task 13.
4. **Changing the AI default is a behaviour change.** After Task 1, `reshelf resolve` refuses to run until `ai.provider` is set. Task 1 includes updating the working `config.yaml`.

## File Structure

**New — storage and conversion**

| File | Responsibility |
|---|---|
| `src/reshelf/store/sidecar.py` | Read/write/merge one book's JSON sidecar; atomic writes; path layouts |
| `src/reshelf/store/models.py` | Pydantic models for the sidecar document |
| `src/reshelf/store/index.py` | Sync a sidecar into SQLite + FTS5; query the index |
| `src/reshelf/db/migrations.py` | `PRAGMA user_version` stepping |
| `src/reshelf/covers.py` | Extract cover images from EPUB and PDF |
| `src/reshelf/convert/epub_writer.py` | Build and rewrite minimal EPUB 3 zips |
| `src/reshelf/convert/converters.py` | MOBI/AZW3/TXT to EPUB, DjVu to PDF |
| `src/reshelf/writeback.py` | Tier-2 library rename, tier-3 embed into EPUB/PDF |
| `src/reshelf/pipeline.py` | Pipeline stages as plain functions taking a progress callback |

**New — web**

| File | Responsibility |
|---|---|
| `src/reshelf/web/app.py` | FastAPI app factory; mounts routers and the built SPA |
| `src/reshelf/web/deps.py` | Shared `Database`, config, sidecar store, job runner |
| `src/reshelf/web/jobs.py` | `jobs` table, single worker thread, cancellation, SSE feed |
| `src/reshelf/web/schemas.py` | Request/response models |
| `src/reshelf/web/api/books.py` | List, detail, metadata PATCH, choose, cover, file |
| `src/reshelf/web/api/actions.py` | Convert and rematch |
| `src/reshelf/web/api/jobs.py` | Job CRUD and the SSE stream |
| `src/reshelf/web/api/meta.py` | Capabilities, stats, settings |
| `web/` | React + Vite SPA source |

**Modified**

| File | Change |
|---|---|
| `src/reshelf/config.py` | `metadata`, `convert`, `web`, `write_back` sections; AI provider gating |
| `src/reshelf/cli.py` | Commands become thin wrappers over `pipeline.py`; add `serve`, `reindex`, `migrate-json` |
| `src/reshelf/db/database.py` | `check_same_thread=False` + lock; run migrations on open |
| `src/reshelf/planner/planner.py` | Read metadata from sidecars |
| `src/reshelf/planner/committer.py` | Drop the Calibre conversion hook; honour `commit_mode` |

**Deleted**

| File | Reason |
|---|---|
| `src/reshelf/calibre/convert.py` | Replaced by `convert/converters.py`; no Calibre |

---

# Phase 1 — Storage and pipeline foundations (no web layer)

### Task 1: Config sections and AI provider gating

**Files:**
- Modify: `src/reshelf/config.py`
- Modify: `tests/test_config.py:18` (drop the `convert_to_epub` assertion)
- Modify: `config.yaml` (the working library root)
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `MetadataConfig(layout: Literal["hash","sidecar","library"], dir: Path)`, `ConvertConfig(dir: Path, timeout: int)`, `WebConfig(host: str, port: int)`, `WriteBackConfig(library_file: bool, embed: bool)`, `LibraryConfig.commit_mode: Literal["copy","move"]`, `AIConfig.provider: str | None`, `AIConfig.enabled -> bool`, `AIConfig.resolved_api_key -> str | None`. All reachable as `cfg.metadata`, `cfg.convert`, `cfg.web`, `cfg.write_back`, `cfg.library.commit_mode`, `cfg.ai`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_config.py`:

```python
import pytest
from reshelf.config import AIConfig, Config, default_config


def test_new_sections_have_defaults(tmp_path):
    cfg = default_config(tmp_path)
    assert cfg.metadata.layout == "hash"
    assert cfg.metadata.dir == Path("metadata")
    assert cfg.convert.dir == Path("derived")
    assert cfg.convert.timeout == 300
    assert cfg.web.host == "127.0.0.1"
    assert cfg.web.port == 8080
    assert cfg.write_back.library_file is False
    assert cfg.write_back.embed is False
    assert cfg.library.commit_mode == "copy"


def test_ai_is_off_unless_a_provider_is_configured(tmp_path):
    cfg = default_config(tmp_path)
    assert cfg.ai.provider is None
    assert cfg.ai.enabled is False


def test_ai_enabled_once_a_provider_is_set():
    assert AIConfig(provider="claude-cli").enabled is True


def test_api_key_falls_back_to_the_environment(monkeypatch):
    monkeypatch.delenv("RESHELF_AI_API_KEY", raising=False)
    assert AIConfig(provider="api").resolved_api_key is None
    monkeypatch.setenv("RESHELF_AI_API_KEY", "sk-test")
    assert AIConfig(provider="api").resolved_api_key == "sk-test"
    assert AIConfig(provider="api", api_key="explicit").resolved_api_key == "explicit"


def test_commit_mode_rejects_nonsense(tmp_path):
    cfg = default_config(tmp_path).model_dump(mode="json")
    cfg["library"]["commit_mode"] = "teleport"
    with pytest.raises(ValueError):
        Config.model_validate(cfg)
```

Add `from pathlib import Path` to the imports at the top of the file, and delete the line `assert loaded.library.convert_to_epub is True` from `test_default_roundtrip`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_config.py -v`
Expected: FAIL with `AttributeError: 'Config' object has no attribute 'metadata'`.

- [ ] **Step 3: Write the implementation**

In `src/reshelf/config.py`, add `import os` and `from typing import Literal` at the top, then:

```python
class LibraryConfig(BaseModel):
    root: Path
    incoming: Path
    quarantine: Path
    commit_mode: Literal["copy", "move"] = "copy"


class MetadataConfig(BaseModel):
    """Where per-book JSON sidecars live. Sidecars are the source of truth."""

    layout: Literal["hash", "sidecar", "library"] = "hash"
    dir: Path = Path("metadata")


class ConvertConfig(BaseModel):
    dir: Path = Path("derived")
    timeout: int = 300


class WebConfig(BaseModel):
    # No auth is implemented; do not default to 0.0.0.0.
    host: str = "127.0.0.1"
    port: int = 8080


class WriteBackConfig(BaseModel):
    library_file: bool = False
    embed: bool = False


class AIConfig(BaseModel):
    provider: Literal["claude-cli", "api"] | None = None
    model: str = "haiku"
    api_key: str | None = None
    base_url: str | None = None
    resolver_only: bool = True  # AI selects among candidates, never invents metadata
    timeout_seconds: int = 180

    @property
    def enabled(self) -> bool:
        return self.provider is not None

    @property
    def resolved_api_key(self) -> str | None:
        return self.api_key or os.environ.get("RESHELF_AI_API_KEY")
```

Remove `convert_to_epub` from `LibraryConfig`. Add the new sections to `Config`:

```python
class Config(BaseModel):
    library: LibraryConfig
    database: DatabaseConfig
    metadata: MetadataConfig = MetadataConfig()
    convert: ConvertConfig = ConvertConfig()
    web: WebConfig = WebConfig()
    write_back: WriteBackConfig = WriteBackConfig()
    scan: ScanConfig = ScanConfig()
    matching: MatchingConfig = MatchingConfig()
    providers: ProvidersConfig = ProvidersConfig()
    cache: CacheConfig = CacheConfig()
    ai: AIConfig = AIConfig()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_config.py -v`
Expected: PASS.

- [ ] **Step 5: Fix the callers that used the removed flag**

`src/reshelf/cli.py` passes `cfg.library.convert_to_epub` into `apply_plan`. Grep and remove:

Run: `grep -rn 'convert_to_epub' src/ tests/`
Expected after fixing: no matches. In `cli.py`'s `commit` command, drop the `convert_kindle=cfg.library.convert_to_epub` argument so `apply_plan`'s own default applies; Task 9 removes the parameter entirely.

- [ ] **Step 6: Run the whole suite**

Run: `.venv/bin/pytest -q`
Expected: PASS.

- [ ] **Step 7: Update the working config**

This is a behaviour change: `reshelf resolve` now refuses to run until a provider is set. Preserve today's behaviour in the real library root:

```bash
python3 - <<'PY'
import yaml, pathlib
p = pathlib.Path("config.yaml")
d = yaml.safe_load(p.read_text())
d.setdefault("ai", {})["provider"] = "claude-cli"
d["library"].pop("convert_to_epub", None)
p.write_text(yaml.safe_dump(d, sort_keys=False, allow_unicode=True))
PY
```

- [ ] **Step 8: Commit**

```bash
git add src/reshelf/config.py src/reshelf/cli.py tests/test_config.py config.yaml
git commit -m "feat(config): sidecar/convert/web/write_back sections, AI off unless configured"
```

---

### Task 2: Schema migrations via `PRAGMA user_version`

**Files:**
- Create: `src/reshelf/db/migrations.py`
- Modify: `src/reshelf/db/database.py` (run migrations in `init_schema`)
- Test: `tests/test_migrations.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `MIGRATIONS: list[str]` (ordered SQL scripts, index 0 is version 1) and `migrate(conn: sqlite3.Connection) -> int` returning the resulting `user_version`. Later tasks add scripts by appending to `MIGRATIONS` — never by editing an existing entry.

- [ ] **Step 1: Write the failing test**

Create `tests/test_migrations.py`:

```python
import sqlite3

from reshelf.db.migrations import MIGRATIONS, migrate


def test_migrate_from_empty_reaches_the_latest_version(tmp_path):
    conn = sqlite3.connect(tmp_path / "t.sqlite3")
    assert migrate(conn) == len(MIGRATIONS)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == len(MIGRATIONS)


def test_migrate_is_idempotent(tmp_path):
    conn = sqlite3.connect(tmp_path / "t.sqlite3")
    migrate(conn)
    assert migrate(conn) == len(MIGRATIONS)


def test_migrate_creates_the_jobs_table(tmp_path):
    conn = sqlite3.connect(tmp_path / "t.sqlite3")
    migrate(conn)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(jobs)")}
    assert {"id", "command", "status", "progress", "log"} <= cols


def test_migrate_creates_the_fts_table(tmp_path):
    conn = sqlite3.connect(tmp_path / "t.sqlite3")
    migrate(conn)
    conn.execute(
        "INSERT INTO books_fts (sha256, title, authors) VALUES ('a', 'Dune', 'Herbert')"
    )
    rows = conn.execute(
        "SELECT sha256 FROM books_fts WHERE books_fts MATCH 'Dune'"
    ).fetchall()
    assert rows == [("a",)]


def test_an_existing_v0_database_is_upgraded_in_place(tmp_path):
    """The real database predates migrations; it must not be recreated."""
    path = tmp_path / "t.sqlite3"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE files (id INTEGER PRIMARY KEY, path TEXT)")
    conn.execute("INSERT INTO files (path) VALUES ('keep-me.epub')")
    conn.commit()
    migrate(conn)
    assert conn.execute("SELECT path FROM files").fetchone() == ("keep-me.epub",)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/pytest tests/test_migrations.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'reshelf.db.migrations'`.

- [ ] **Step 3: Write the implementation**

Create `src/reshelf/db/migrations.py`:

```python
"""Schema evolution keyed on PRAGMA user_version.

Append new scripts to MIGRATIONS; never edit an existing entry, because
databases in the wild have already run it. Version N means MIGRATIONS[:N]
have been applied.
"""

import sqlite3

# Version 1: the web layer's derived tables. The pre-existing pipeline
# tables live in database.SCHEMA and are created with IF NOT EXISTS, so a
# database from before migrations existed is at version 0 and simply gains
# these.
_V1 = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY,
    command TEXT NOT NULL,
    args_json TEXT,
    status TEXT NOT NULL,
    progress INTEGER DEFAULT 0,
    total INTEGER,
    message TEXT,
    log TEXT DEFAULT '',
    error TEXT,
    created_at TEXT,
    started_at TEXT,
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);

CREATE VIRTUAL TABLE IF NOT EXISTS books_fts USING fts5(
    sha256 UNINDEXED, title, authors, series, publisher, tags, description
);

CREATE TABLE IF NOT EXISTS book_index (
    sha256 TEXT PRIMARY KEY,
    title TEXT,
    authors TEXT,
    series TEXT,
    series_index REAL,
    publisher TEXT,
    pubdate TEXT,
    language TEXT,
    isbn13 TEXT,
    tags TEXT,
    resolver TEXT,
    confidence REAL,
    primary_path TEXT,
    primary_format TEXT,
    has_cover INTEGER DEFAULT 0,
    updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_book_index_resolver ON book_index(resolver);
"""

MIGRATIONS: list[str] = [_V1]


def migrate(conn: sqlite3.Connection) -> int:
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    for version in range(current, len(MIGRATIONS)):
        conn.executescript(MIGRATIONS[version])
        conn.execute(f"PRAGMA user_version = {version + 1}")
    conn.commit()
    return conn.execute("PRAGMA user_version").fetchone()[0]
```

Note: `PRAGMA user_version` cannot take a bound parameter, hence the f-string. The value is a loop index, never user input.

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/bin/pytest tests/test_migrations.py -v`
Expected: PASS.

- [ ] **Step 5: Call it from `Database.init_schema`**

In `src/reshelf/db/database.py`, add `from reshelf.db.migrations import migrate` and change:

```python
    def init_schema(self) -> None:
        self.conn.executescript(SCHEMA)
        migrate(self.conn)
        self.conn.commit()
```

- [ ] **Step 6: Run the whole suite**

Run: `.venv/bin/pytest -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/reshelf/db/migrations.py src/reshelf/db/database.py tests/test_migrations.py
git commit -m "feat(db): user_version migrations, jobs + FTS5 + book_index tables"
```

---
### Task 3: Sidecar store — the source of truth

**Files:**
- Create: `src/reshelf/store/__init__.py` (empty)
- Create: `src/reshelf/store/models.py`
- Create: `src/reshelf/store/sidecar.py`
- Test: `tests/test_sidecar.py`

**Interfaces:**
- Consumes: `Config` from Task 1.
- Produces:
  - `Book`, `BookMetadata`, `FileEntry`, `Source`, `Reading` (pydantic models). `Book.schema_version` is serialized as `"schema"`. `Book.is_human -> bool`. `Book.primary_file() -> FileEntry | None`.
  - `SidecarStore(cfg: Config)` with `path_for(sha256: str, file_path: str | None = None) -> Path`, `load(sha256, file_path=None) -> Book | None`, `save(book: Book, file_path=None) -> Path`, `update(sha256, mutate: Callable[[Book], None], file_path=None) -> Book`, `iter_all() -> Iterator[Book]`, `delete(sha256, file_path=None) -> None`.
- Note for later tasks: with `metadata.layout` set to `sidecar` or `library`, `path_for` needs `file_path`. Every web caller has it, because `book_index.primary_path` stores it.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_sidecar.py`:

```python
import json

import pytest

from reshelf.config import default_config
from reshelf.store.models import Book, FileEntry
from reshelf.store.sidecar import SidecarStore

SHA = "a" * 64


def store_for(tmp_path, layout="hash"):
    cfg = default_config(tmp_path)
    cfg.metadata.layout = layout
    return SidecarStore(cfg)


def a_book(path="incoming/dune.epub"):
    return Book(
        sha256=SHA,
        files=[FileEntry(path=path, format="epub", size=10, mtime=1)],
    )


def test_round_trip(tmp_path):
    store = store_for(tmp_path)
    book = a_book()
    book.metadata.title = "Dune"
    book.metadata.authors = ["Frank Herbert"]
    store.save(book)
    loaded = store.load(SHA)
    assert loaded.metadata.title == "Dune"
    assert loaded.metadata.authors == ["Frank Herbert"]
    assert loaded.sha256 == SHA


def test_missing_book_loads_as_none(tmp_path):
    assert store_for(tmp_path).load(SHA) is None


def test_schema_key_is_written_as_schema_not_schema_version(tmp_path):
    store = store_for(tmp_path)
    store.save(a_book())
    raw = json.loads(store.path_for(SHA).read_text())
    assert raw["schema"] == 1
    assert "schema_version" not in raw


def test_unknown_keys_survive_a_rewrite(tmp_path):
    """Sub-project B adds keys this code has never heard of."""
    store = store_for(tmp_path)
    store.save(a_book())
    path = store.path_for(SHA)
    raw = json.loads(path.read_text())
    raw["future_feature"] = {"kept": True}
    raw["reading"]["future_locator"] = "x"
    path.write_text(json.dumps(raw))

    store.update(SHA, lambda b: setattr(b.metadata, "title", "Changed"))

    raw = json.loads(path.read_text())
    assert raw["future_feature"] == {"kept": True}
    assert raw["reading"]["future_locator"] == "x"
    assert raw["metadata"]["title"] == "Changed"


def test_write_is_atomic_and_leaves_no_temp_file(tmp_path):
    store = store_for(tmp_path)
    store.save(a_book())
    assert list(store.path_for(SHA).parent.glob("*.tmp")) == []


def test_a_failed_write_leaves_the_old_content_intact(tmp_path, monkeypatch):
    store = store_for(tmp_path)
    book = a_book()
    book.metadata.title = "Original"
    store.save(book)

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr("os.replace", boom)
    book.metadata.title = "Doomed"
    with pytest.raises(OSError):
        store.save(book)
    assert store.load(SHA).metadata.title == "Original"


def test_hash_layout_never_touches_the_book_folder(tmp_path):
    store = store_for(tmp_path)
    assert store.path_for(SHA) == tmp_path.resolve() / "metadata" / f"{SHA}.json"


def test_sidecar_layout_sits_beside_the_file(tmp_path):
    store = store_for(tmp_path, layout="sidecar")
    p = store.path_for(SHA, "incoming/dune.epub")
    assert p == tmp_path.resolve() / "incoming" / "dune.epub.json"


def test_non_hash_layout_requires_a_file_path(tmp_path):
    store = store_for(tmp_path, layout="sidecar")
    with pytest.raises(ValueError, match="file_path"):
        store.path_for(SHA)


def test_iter_all_finds_every_book(tmp_path):
    store = store_for(tmp_path)
    for n in ("a", "b", "c"):
        store.save(Book(sha256=n * 64))
    assert {b.sha256 for b in store.iter_all()} == {"a" * 64, "b" * 64, "c" * 64}


def test_is_human_reflects_the_resolver(tmp_path):
    book = a_book()
    assert book.is_human is False
    book.source.resolver = "human"
    assert book.is_human is True


def test_primary_file_prefers_a_converted_file(tmp_path):
    book = Book(
        sha256=SHA,
        files=[
            FileEntry(path="incoming/x.azw3", format="azw3", role="original"),
            FileEntry(path="derived/x.epub", format="epub", role="converted"),
        ],
    )
    assert book.primary_file().path == "derived/x.epub"


def test_primary_file_prefers_library_over_incoming(tmp_path):
    book = Book(
        sha256=SHA,
        files=[
            FileEntry(path="incoming/x.epub", format="epub"),
            FileEntry(path="library/A/x.epub", format="epub"),
        ],
    )
    assert book.primary_file().path == "library/A/x.epub"


def test_update_applies_the_mutation_and_bumps_updated_at(tmp_path):
    store = store_for(tmp_path)
    store.save(a_book())
    before = store.load(SHA).updated_at
    after = store.update(SHA, lambda b: setattr(b.metadata, "title", "New"))
    assert after.metadata.title == "New"
    assert after.updated_at >= before


def test_update_on_a_missing_book_raises(tmp_path):
    with pytest.raises(KeyError):
        store_for(tmp_path).update(SHA, lambda b: None)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_sidecar.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'reshelf.store'`.

- [ ] **Step 3: Write the models**

Create `src/reshelf/store/__init__.py` (empty) and `src/reshelf/store/models.py`:

```python
"""The sidecar document: one JSON file per book, the source of truth.

Every model allows extra keys so that a sidecar written by a newer
reshelf (sub-project B adds annotation fields) survives a rewrite by an
older one. Load, mutate, dump - unknown keys ride along.
"""

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

_EXTRA = ConfigDict(extra="allow", populate_by_name=True)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class FileEntry(BaseModel):
    model_config = _EXTRA

    path: str
    format: str
    size: int = 0
    mtime: int = 0
    role: Literal["original", "converted"] = "original"
    sha256: str | None = None  # set on derived files; the book keeps the original's


class BookMetadata(BaseModel):
    model_config = _EXTRA

    title: str | None = None
    subtitle: str | None = None
    authors: list[str] = Field(default_factory=list)
    translators: list[str] = Field(default_factory=list)
    series: str | None = None
    series_index: float | None = None
    publisher: str | None = None
    pubdate: str | None = None
    language: str | None = None
    isbn10: str | None = None
    isbn13: str | None = None
    tags: list[str] = Field(default_factory=list)
    description: str | None = None
    cover: str | None = None


class Source(BaseModel):
    model_config = _EXTRA

    resolver: Literal["embedded", "deterministic", "ai", "human"] = "embedded"
    provider: str | None = None
    provider_id: str | None = None
    confidence: float = 0.0
    decided_at: str | None = None


class Reading(BaseModel):
    model_config = _EXTRA

    locator: str | None = None
    percent: float = 0.0
    updated_at: str | None = None


class Book(BaseModel):
    model_config = _EXTRA

    schema_version: int = Field(default=1, alias="schema")
    sha256: str
    files: list[FileEntry] = Field(default_factory=list)
    metadata: BookMetadata = Field(default_factory=BookMetadata)
    source: Source = Field(default_factory=Source)
    reading: Reading = Field(default_factory=Reading)
    annotations: list[dict] = Field(default_factory=list)  # written by sub-project B
    created_at: str = Field(default_factory=now)
    updated_at: str = Field(default_factory=now)

    @property
    def is_human(self) -> bool:
        """A human decision is sticky - match and resolve must not overwrite it."""
        return self.source.resolver == "human"

    def primary_file(self) -> FileEntry | None:
        """Converted first (readable and metadata-writable), then library, then the rest."""
        if not self.files:
            return None

        def rank(f: FileEntry) -> int:
            if f.role == "converted":
                return 0
            return 1 if "library/" in f.path.replace("\\", "/") else 2

        return sorted(self.files, key=rank)[0]
```

- [ ] **Step 4: Write the store**

Create `src/reshelf/store/sidecar.py`:

```python
"""Atomic, locked read/write of sidecar documents."""

import json
import os
import threading
from collections import defaultdict
from collections.abc import Callable, Iterator
from pathlib import Path

from reshelf.config import Config
from reshelf.store.models import Book, now


class SidecarStore:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.root = Path(cfg.library.root).resolve()
        self._locks: defaultdict[str, threading.Lock] = defaultdict(threading.Lock)
        self._locks_guard = threading.Lock()

    def _lock(self, sha256: str) -> threading.Lock:
        with self._locks_guard:
            return self._locks[sha256]

    def path_for(self, sha256: str, file_path: str | None = None) -> Path:
        layout = self.cfg.metadata.layout
        if layout == "hash":
            return self.root / self.cfg.metadata.dir / f"{sha256}.json"
        if file_path is None:
            raise ValueError(f"metadata.layout={layout!r} needs a file_path")
        p = Path(file_path)
        if not p.is_absolute():
            p = self.root / p
        return p.with_name(p.name + ".json")

    def load(self, sha256: str, file_path: str | None = None) -> Book | None:
        path = self.path_for(sha256, file_path)
        if not path.exists():
            return None
        return Book.model_validate(json.loads(path.read_text(encoding="utf-8")))

    def save(self, book: Book, file_path: str | None = None) -> Path:
        path = self.path_for(book.sha256, file_path)
        with self._lock(book.sha256):
            return self._write(path, book)

    def _write(self, path: Path, book: Book) -> Path:
        book.updated_at = now()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            book.model_dump(mode="json", by_alias=True),
            ensure_ascii=False,
            indent=2,
        )
        tmp = path.with_name(path.name + ".tmp")
        try:
            tmp.write_text(payload, encoding="utf-8")
            os.replace(tmp, path)
        except OSError:
            tmp.unlink(missing_ok=True)
            raise
        return path

    def update(
        self,
        sha256: str,
        mutate: Callable[[Book], None],
        file_path: str | None = None,
    ) -> Book:
        """Load, mutate and write back under one lock."""
        path = self.path_for(sha256, file_path)
        with self._lock(sha256):
            if not path.exists():
                raise KeyError(sha256)
            book = Book.model_validate(json.loads(path.read_text(encoding="utf-8")))
            mutate(book)
            self._write(path, book)
            return book

    def iter_all(self) -> Iterator[Book]:
        if self.cfg.metadata.layout == "hash":
            paths = sorted((self.root / self.cfg.metadata.dir).glob("*.json"))
        else:
            paths = sorted(self.root.rglob("*.json"))
        for path in paths:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(data, dict) and "sha256" in data:
                yield Book.model_validate(data)

    def delete(self, sha256: str, file_path: str | None = None) -> None:
        with self._lock(sha256):
            self.path_for(sha256, file_path).unlink(missing_ok=True)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_sidecar.py -v`
Expected: PASS (16 tests).

- [ ] **Step 6: Run the whole suite**

Run: `.venv/bin/pytest -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/reshelf/store tests/test_sidecar.py
git commit -m "feat(store): JSON sidecar as the source of truth for book metadata"
```

---
### Task 4: Index sync and search

Every scanned file that has a hash gets a sidecar — even one with no metadata at all. That keeps the model uniform: `book_index` always has a row, listing and search never need a fallback path, and "every scanned book appears in the UI" falls out for free. Pipeline *status* is not duplicated into the index; it is joined from `files`, which remains its home.

**Files:**
- Create: `src/reshelf/store/index.py`
- Test: `tests/test_index.py`

**Interfaces:**
- Consumes: `Book`, `SidecarStore` (Task 3); the `book_index` and `books_fts` tables (Task 2).
- Produces:
  - `sync(conn: sqlite3.Connection, book: Book) -> None`
  - `remove(conn, sha256: str) -> None`
  - `rebuild(conn, store: SidecarStore) -> int` (returns rows written)
  - `query(conn, *, q=None, status=None, fmt=None, tag=None, resolver=None, sort="title", order="asc", limit=50, offset=0) -> tuple[list[dict], int]` returning `(rows, total)`
  - `SORTS: dict[str, str]` — the sort whitelist.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_index.py`:

```python
import sqlite3

import pytest

from reshelf.config import default_config
from reshelf.db.migrations import migrate
from reshelf.store import index
from reshelf.store.models import Book, BookMetadata, FileEntry
from reshelf.store.sidecar import SidecarStore


@pytest.fixture
def conn(tmp_path):
    c = sqlite3.connect(tmp_path / "i.sqlite3")
    c.row_factory = sqlite3.Row
    c.executescript(
        "CREATE TABLE files (id INTEGER PRIMARY KEY, path TEXT, sha256 TEXT,"
        " format TEXT, status TEXT);"
    )
    migrate(c)
    return c


def book(sha, title="Dune", authors=("Frank Herbert",), fmt="epub", **kw):
    return Book(
        sha256=sha,
        files=[FileEntry(path=f"library/{title}.{fmt}", format=fmt)],
        metadata=BookMetadata(title=title, authors=list(authors), **kw),
    )


def add_file(conn, sha, status="MATCHED", fmt="epub", path=None):
    conn.execute(
        "INSERT INTO files (path, sha256, format, status) VALUES (?,?,?,?)",
        (path or f"library/{sha}.{fmt}", sha, fmt, status),
    )
    conn.commit()


def test_sync_writes_a_queryable_row(conn):
    index.sync(conn, book("a" * 64))
    add_file(conn, "a" * 64)
    rows, total = index.query(conn)
    assert total == 1
    assert rows[0]["title"] == "Dune"
    assert rows[0]["authors"] == "Frank Herbert"
    assert rows[0]["status"] == "MATCHED"


def test_sync_is_an_upsert_not_a_duplicate(conn):
    b = book("a" * 64)
    index.sync(conn, b)
    b.metadata.title = "Dune Messiah"
    index.sync(conn, b)
    rows, total = index.query(conn)
    assert total == 1
    assert rows[0]["title"] == "Dune Messiah"


def test_full_text_search_finds_by_title_and_author(conn):
    index.sync(conn, book("a" * 64, title="Dune", authors=["Frank Herbert"]))
    index.sync(conn, book("b" * 64, title="Neuromancer", authors=["William Gibson"]))
    assert index.query(conn, q="Dune")[1] == 1
    assert index.query(conn, q="Gibson")[1] == 1
    assert index.query(conn, q="Tolkien")[1] == 0


def test_search_reindexes_rather_than_accumulating_fts_rows(conn):
    b = book("a" * 64, title="Dune")
    index.sync(conn, b)
    b.metadata.title = "Neuromancer"
    index.sync(conn, b)
    assert index.query(conn, q="Dune")[1] == 0
    assert index.query(conn, q="Neuromancer")[1] == 1


def test_a_query_with_punctuation_does_not_explode(conn):
    """FTS5 MATCH treats bare punctuation as syntax; it must be escaped."""
    index.sync(conn, book("a" * 64, title="Dune"))
    assert index.query(conn, q='"')[1] == 0
    assert index.query(conn, q="AND OR NOT")[1] == 0


def test_filter_by_status(conn):
    index.sync(conn, book("a" * 64))
    index.sync(conn, book("b" * 64, title="Other"))
    add_file(conn, "a" * 64, status="MATCHED")
    add_file(conn, "b" * 64, status="UNRESOLVED")
    assert index.query(conn, status="UNRESOLVED")[1] == 1


def test_filter_by_format_and_tag(conn):
    index.sync(conn, book("a" * 64, fmt="pdf", tags=["sci-fi"]))
    index.sync(conn, book("b" * 64, title="Other", fmt="epub", tags=["history"]))
    assert index.query(conn, fmt="pdf")[1] == 1
    assert index.query(conn, tag="history")[1] == 1


def test_sort_whitelist_rejects_injection(conn):
    with pytest.raises(ValueError):
        index.query(conn, sort="title; DROP TABLE files")


def test_paging_reports_the_full_total(conn):
    for n in range(5):
        index.sync(conn, book(str(n) * 64, title=f"Book {n}"))
    rows, total = index.query(conn, limit=2, offset=0)
    assert total == 5
    assert len(rows) == 2


def test_duplicate_paths_collapse_to_one_row(conn):
    sha = "a" * 64
    index.sync(conn, book(sha))
    add_file(conn, sha, path="incoming/one.epub")
    add_file(conn, sha, path="incoming/two.epub", status="DUPLICATE")
    rows, total = index.query(conn)
    assert total == 1


def test_remove_clears_both_tables(conn):
    index.sync(conn, book("a" * 64))
    index.remove(conn, "a" * 64)
    assert index.query(conn)[1] == 0
    assert index.query(conn, q="Dune")[1] == 0


def test_rebuild_restores_everything_from_sidecars(tmp_path, conn):
    cfg = default_config(tmp_path)
    store = SidecarStore(cfg)
    for n, title in ((0, "Dune"), (1, "Neuromancer")):
        store.save(book(str(n) * 64, title=title))
    assert index.rebuild(conn, store) == 2
    assert index.query(conn)[1] == 2
    assert index.query(conn, q="Neuromancer")[1] == 1


def test_rebuild_discards_stale_rows(tmp_path, conn):
    index.sync(conn, book("z" * 64, title="Deleted Book"))
    store = SidecarStore(default_config(tmp_path))
    store.save(book("a" * 64))
    index.rebuild(conn, store)
    assert index.query(conn, q="Deleted")[1] == 0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_index.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'reshelf.store.index'`.

- [ ] **Step 3: Write the implementation**

Create `src/reshelf/store/index.py`:

```python
"""Derived search index over the sidecars.

Nothing here is authoritative: drop the database and `reshelf reindex`
rebuilds it from the sidecars plus a rescan. Pipeline status is NOT
copied in; it is joined from `files`, which owns it.
"""

import re
import sqlite3

from reshelf.store.models import Book
from reshelf.store.sidecar import SidecarStore

SORTS = {
    "title": "bi.title",
    "authors": "bi.authors",
    "pubdate": "bi.pubdate",
    "updated_at": "bi.updated_at",
    "added": "f.id",
}

# One canonical files row per hash, so byte-identical duplicates collapse.
_BASE = """
FROM book_index bi
LEFT JOIN files f ON f.id = (
    SELECT MIN(id) FROM files WHERE sha256 = bi.sha256
)
"""

_WORD = re.compile(r"[^\w一-鿿]+")


def _fts_query(q: str) -> str | None:
    """FTS5 MATCH is a query language; user text must not be handed to it raw."""
    terms = [t for t in _WORD.split(q) if t]
    if not terms:
        return None
    return " ".join(f'"{t}"' for t in terms)


def sync(conn: sqlite3.Connection, book: Book) -> None:
    m = book.metadata
    primary = book.primary_file()
    conn.execute(
        """
        INSERT INTO book_index (sha256, title, authors, series, series_index,
            publisher, pubdate, language, isbn13, tags, resolver, confidence,
            primary_path, primary_format, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(sha256) DO UPDATE SET
            title=excluded.title, authors=excluded.authors, series=excluded.series,
            series_index=excluded.series_index, publisher=excluded.publisher,
            pubdate=excluded.pubdate, language=excluded.language,
            isbn13=excluded.isbn13, tags=excluded.tags, resolver=excluded.resolver,
            confidence=excluded.confidence, primary_path=excluded.primary_path,
            primary_format=excluded.primary_format, updated_at=excluded.updated_at
        """,
        (
            book.sha256, m.title, "; ".join(m.authors), m.series, m.series_index,
            m.publisher, m.pubdate, m.language, m.isbn13, "; ".join(m.tags),
            book.source.resolver, book.source.confidence,
            primary.path if primary else None,
            primary.format if primary else None,
            book.updated_at,
        ),
    )
    # FTS5 has no upsert; delete then insert.
    conn.execute("DELETE FROM books_fts WHERE sha256 = ?", (book.sha256,))
    conn.execute(
        "INSERT INTO books_fts (sha256, title, authors, series, publisher, tags,"
        " description) VALUES (?,?,?,?,?,?,?)",
        (
            book.sha256, m.title or "", "; ".join(m.authors), m.series or "",
            m.publisher or "", "; ".join(m.tags), m.description or "",
        ),
    )
    conn.commit()


def remove(conn: sqlite3.Connection, sha256: str) -> None:
    conn.execute("DELETE FROM book_index WHERE sha256 = ?", (sha256,))
    conn.execute("DELETE FROM books_fts WHERE sha256 = ?", (sha256,))
    conn.commit()


def rebuild(conn: sqlite3.Connection, store: SidecarStore) -> int:
    conn.execute("DELETE FROM book_index")
    conn.execute("DELETE FROM books_fts")
    count = 0
    for book in store.iter_all():
        sync(conn, book)
        count += 1
    conn.commit()
    return count


def query(
    conn: sqlite3.Connection,
    *,
    q: str | None = None,
    status: str | None = None,
    fmt: str | None = None,
    tag: str | None = None,
    resolver: str | None = None,
    sort: str = "title",
    order: str = "asc",
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict], int]:
    if sort not in SORTS:
        raise ValueError(f"sort must be one of {sorted(SORTS)}")
    direction = "DESC" if order.lower() == "desc" else "ASC"

    where: list[str] = []
    params: list[object] = []
    if q:
        match = _fts_query(q)
        if match is None:
            return [], 0
        where.append(
            "bi.sha256 IN (SELECT sha256 FROM books_fts WHERE books_fts MATCH ?)"
        )
        params.append(match)
    if status:
        where.append("f.status = ?")
        params.append(status)
    if fmt:
        where.append("bi.primary_format = ?")
        params.append(fmt)
    if tag:
        where.append("(';' || bi.tags || ';') LIKE ?")
        params.append(f"%;{tag};%".replace(";;", ";"))
    if resolver:
        where.append("bi.resolver = ?")
        params.append(resolver)
    clause = (" WHERE " + " AND ".join(where)) if where else ""

    total = conn.execute(
        f"SELECT COUNT(*) {_BASE}{clause}", params
    ).fetchone()[0]
    rows = conn.execute(
        f"SELECT bi.*, f.status, f.path {_BASE}{clause}"
        f" ORDER BY {SORTS[sort]} {direction} LIMIT ? OFFSET ?",
        [*params, limit, offset],
    ).fetchall()
    return [dict(r) for r in rows], total
```

Note the `tag` filter: tags are stored as `"a; b"`, so the `LIKE` pattern needs the same separator. If `test_filter_by_format_and_tag` fails on the separator, normalise storage to `"; "` on both sides rather than loosening the pattern.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_index.py -v`
Expected: PASS (13 tests).

- [ ] **Step 5: Run the whole suite**

Run: `.venv/bin/pytest -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/reshelf/store/index.py tests/test_index.py
git commit -m "feat(store): derived SQLite + FTS5 index over the sidecars"
```

---
### Task 5: `migrate-json` and `reindex` commands

**Files:**
- Create: `src/reshelf/store/bootstrap.py`
- Modify: `src/reshelf/cli.py` (two new commands)
- Modify: `src/reshelf/cli.py:45` (`SUBDIRS`: add `metadata`, `derived`)
- Test: `tests/test_bootstrap.py`

**Interfaces:**
- Consumes: `SidecarStore`, `index`, `Database`.
- Produces: `migrate_json(db, store, progress) -> int` (sidecars written), `reindex(db, store, progress) -> int`. `progress` is `Callable[[int, int | None, str], None]`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_bootstrap.py`:

```python
from pathlib import Path

from reshelf.config import default_config
from reshelf.db.database import Database
from reshelf.store import index
from reshelf.store.bootstrap import migrate_json, reindex
from reshelf.store.sidecar import SidecarStore

NOOP = lambda *a: None  # noqa: E731


def seeded(tmp_path):
    cfg = default_config(tmp_path)
    db = Database(cfg.database.path)
    db.init_schema()
    db.conn.execute(
        "INSERT INTO files (path, sha256, format, size, mtime, status,"
        " title_raw, author_raw, isbn_raw, language_raw)"
        " VALUES ('incoming/dune.epub', ?, 'epub', 10, 1, 'IDENTIFIED',"
        " 'Dune', 'Frank Herbert', '9780441013593', 'en')",
        ("a" * 64,),
    )
    db.conn.execute(
        "INSERT INTO files (path, sha256, format, size, mtime, status)"
        " VALUES ('incoming/unknown.pdf', ?, 'pdf', 20, 2, 'SCANNED')",
        ("b" * 64,),
    )
    db.conn.commit()
    return cfg, db, SidecarStore(cfg)


def test_migrate_json_writes_a_sidecar_for_every_hashed_file(tmp_path):
    cfg, db, store = seeded(tmp_path)
    assert migrate_json(db, store, NOOP) == 2
    dune = store.load("a" * 64)
    assert dune.metadata.title == "Dune"
    assert dune.metadata.authors == ["Frank Herbert"]
    assert dune.metadata.isbn13 == "9780441013593"
    assert dune.source.resolver == "embedded"
    assert dune.files[0].path == "incoming/dune.epub"
    db.close()


def test_a_file_with_no_metadata_still_gets_a_sidecar(tmp_path):
    """Uniformity: every hashed file has a sidecar, so the UI never has a gap."""
    cfg, db, store = seeded(tmp_path)
    migrate_json(db, store, NOOP)
    unknown = store.load("b" * 64)
    assert unknown is not None
    assert unknown.metadata.title is None
    db.close()


def test_migrate_json_never_overwrites_a_human_decision(tmp_path):
    cfg, db, store = seeded(tmp_path)
    migrate_json(db, store, NOOP)
    store.update("a" * 64, lambda b: (
        setattr(b.metadata, "title", "My Correction"),
        setattr(b.source, "resolver", "human"),
    ))
    migrate_json(db, store, NOOP)
    assert store.load("a" * 64).metadata.title == "My Correction"
    db.close()


def test_migrate_json_is_idempotent(tmp_path):
    cfg, db, store = seeded(tmp_path)
    migrate_json(db, store, NOOP)
    migrate_json(db, store, NOOP)
    assert len(list(store.iter_all())) == 2
    db.close()


def test_unhashed_files_are_skipped(tmp_path):
    cfg, db, store = seeded(tmp_path)
    db.conn.execute(
        "INSERT INTO files (path, format, size, mtime, status)"
        " VALUES ('incoming/pending.epub', 'epub', 1, 1, 'NEW')"
    )
    db.conn.commit()
    assert migrate_json(db, store, NOOP) == 2
    db.close()


def test_reindex_restores_the_index_after_the_tables_are_wiped(tmp_path):
    cfg, db, store = seeded(tmp_path)
    migrate_json(db, store, NOOP)
    reindex(db, store, NOOP)
    assert index.query(db.conn, q="Dune")[1] == 1

    db.conn.execute("DELETE FROM book_index")
    db.conn.execute("DELETE FROM books_fts")
    db.conn.commit()
    assert index.query(db.conn, q="Dune")[1] == 0

    assert reindex(db, store, NOOP) == 2
    assert index.query(db.conn, q="Dune")[1] == 1
    db.close()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_bootstrap.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'reshelf.store.bootstrap'`.

- [ ] **Step 3: Write the implementation**

Create `src/reshelf/store/bootstrap.py`:

```python
"""One-shot conversion of the old SQLite-as-record model into sidecars,
and rebuilding the derived index from them."""

from collections.abc import Callable

from reshelf.db.database import Database
from reshelf.store import index
from reshelf.store.models import Book, FileEntry
from reshelf.store.sidecar import SidecarStore

Progress = Callable[[int, int | None, str], None]


def _matched_metadata(db: Database, file_id: int) -> dict:
    """Provider metadata for a MATCHED file, from the candidate cache."""
    row = db.conn.execute(
        "SELECT w.canonical_title AS title, e.isbn13, e.isbn10, e.publisher,"
        " e.publication_date, e.language,"
        " (SELECT a.canonical_name FROM work_authors wa"
        "   JOIN authors a ON a.id = wa.author_id"
        "   WHERE wa.work_id = w.id LIMIT 1) AS author"
        " FROM files f JOIN editions e ON e.id = f.matched_edition_id"
        " JOIN works w ON w.id = e.work_id WHERE f.id = ?",
        (file_id,),
    ).fetchone()
    return dict(row) if row else {}


def migrate_json(db: Database, store: SidecarStore, progress: Progress) -> int:
    rows = db.conn.execute(
        "SELECT * FROM files WHERE sha256 IS NOT NULL ORDER BY id"
    ).fetchall()
    total = len(rows)
    written = 0
    for n, row in enumerate(rows, 1):
        progress(n, total, row["path"])
        sha = row["sha256"]
        existing = store.load(sha, row["path"])
        if existing is not None and existing.is_human:
            continue  # a human decision is sticky

        book = existing or Book(sha256=sha)
        entry = FileEntry(
            path=row["path"],
            format=row["format"] or "",
            size=row["size"] or 0,
            mtime=row["mtime"] or 0,
        )
        if not any(f.path == entry.path for f in book.files):
            book.files.append(entry)

        matched = _matched_metadata(db, row["id"]) if row["matched_edition_id"] else {}
        m = book.metadata
        m.title = matched.get("title") or row["title_raw"] or m.title
        if matched.get("author"):
            m.authors = [matched["author"]]
        elif row["author_raw"] and not m.authors:
            m.authors = [a.strip() for a in row["author_raw"].split(";") if a.strip()]
        m.isbn13 = matched.get("isbn13") or row["isbn_raw"] or m.isbn13
        m.isbn10 = matched.get("isbn10") or m.isbn10
        m.publisher = matched.get("publisher") or m.publisher
        m.pubdate = matched.get("publication_date") or m.pubdate
        m.language = matched.get("language") or row["language_raw"] or m.language

        book.source.resolver = "deterministic" if matched else "embedded"
        book.source.confidence = row["match_confidence"] or 0.0

        store.save(book, row["path"])
        index.sync(db.conn, book)
        written += 1
    return written


def reindex(db: Database, store: SidecarStore, progress: Progress) -> int:
    progress(0, None, "rebuilding index")
    count = index.rebuild(db.conn, store)
    progress(count, count, f"indexed {count} books")
    return count
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_bootstrap.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Wire up the CLI**

In `src/reshelf/cli.py`, extend `SUBDIRS` with `"metadata"` and `"derived"`, then add:

```python
@app.command("migrate-json")
def migrate_json_cmd(root: Path = ROOT_OPTION) -> None:
    """One shot: write a JSON sidecar for every hashed file in the database."""
    from reshelf.store.bootstrap import migrate_json
    from reshelf.store.sidecar import SidecarStore

    cfg = load_config(root)
    with Database(cfg.database.path) as db:
        db.init_schema()
        n = migrate_json(db, SidecarStore(cfg), lambda *a: None)
    typer.echo(f"sidecars={n}")


@app.command()
def reindex(root: Path = ROOT_OPTION) -> None:
    """Rebuild the derived search index from the sidecars."""
    from reshelf.store.bootstrap import reindex as _reindex
    from reshelf.store.sidecar import SidecarStore

    cfg = load_config(root)
    with Database(cfg.database.path) as db:
        db.init_schema()
        n = _reindex(db, SidecarStore(cfg), lambda *a: None)
    typer.echo(f"indexed={n}")
```

- [ ] **Step 6: Verify against the real library**

```bash
cp db/books.sqlite3 /tmp/books-backup.sqlite3
.venv/bin/reshelf migrate-json --root .
.venv/bin/reshelf reindex --root .
ls metadata | head -3 && ls metadata | wc -l
```
Expected: a `metadata/<sha>.json` per hashed file, count matching `SELECT COUNT(*) FROM files WHERE sha256 IS NOT NULL`.

- [ ] **Step 7: Run the whole suite and commit**

```bash
.venv/bin/pytest -q
git add src/reshelf/store/bootstrap.py src/reshelf/cli.py tests/test_bootstrap.py
git commit -m "feat(store): migrate-json and reindex commands"
```

---

### Task 6: Pipeline refactor — `scan` and `extract`

The refactor is mechanical, and the existing CLI tests in `tests/test_cli.py` are the regression net: they must pass unchanged. The behavioural addition is that `extract` now writes sidecars and index rows.

**Files:**
- Create: `src/reshelf/pipeline.py`
- Modify: `src/reshelf/cli.py` (`scan`, `extract` become wrappers)
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `SidecarStore`, `index`, `Database`, the existing `iter_files`, `sha256_file`, `_EXTRACTORS`.
- Produces:
  - `Progress = Callable[[int, int | None, str], None]`
  - `class JobCancelled(Exception)`
  - `scan(cfg, db, store, target: Path, progress: Progress) -> dict` with keys `seen, added, changed, duplicates`
  - `extract(cfg, db, store, force: bool, progress: Progress) -> dict` with keys `extracted, errors`
  - `EXTRACTORS: dict[str, Callable]` moved here from `cli.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_pipeline.py`:

```python
import pytest

from reshelf.config import default_config
from reshelf.db.database import Database
from reshelf.pipeline import JobCancelled, extract, scan
from reshelf.store.sidecar import SidecarStore
from tests.helpers import make_epub

NOOP = lambda *a: None  # noqa: E731


@pytest.fixture
def env(tmp_path):
    cfg = default_config(tmp_path)
    cfg.library.incoming.mkdir(parents=True)
    db = Database(cfg.database.path)
    db.init_schema()
    yield cfg, db, SidecarStore(cfg)
    db.close()


def test_scan_reports_counts(env):
    cfg, db, store = env
    make_epub(cfg.library.incoming / "dune.epub", "Dune", "Frank Herbert")
    result = scan(cfg, db, store, cfg.library.incoming, NOOP)
    assert result["seen"] == 1
    assert result["added"] == 1


def test_scan_calls_progress_for_each_file(env):
    cfg, db, store = env
    for n in range(3):
        make_epub(cfg.library.incoming / f"b{n}.epub", f"B{n}", "A")
    calls = []
    scan(cfg, db, store, cfg.library.incoming, lambda *a: calls.append(a))
    assert len(calls) == 3
    assert all(len(c) == 3 for c in calls)


def test_progress_may_cancel_a_run(env):
    cfg, db, store = env
    for n in range(5):
        make_epub(cfg.library.incoming / f"b{n}.epub", f"B{n}", "A")

    def cancel_after_two(done, total, message):
        if done > 2:
            raise JobCancelled()

    with pytest.raises(JobCancelled):
        scan(cfg, db, store, cfg.library.incoming, cancel_after_two)


def test_extract_writes_a_sidecar_and_an_index_row(env):
    cfg, db, store = env
    make_epub(cfg.library.incoming / "dune.epub", "Dune", "Frank Herbert")
    scan(cfg, db, store, cfg.library.incoming, NOOP)
    result = extract(cfg, db, store, force=False, progress=NOOP)
    assert result["extracted"] == 1

    from reshelf.store import index

    rows, total = index.query(db.conn, q="Dune")
    assert total == 1
    assert rows[0]["title"] == "Dune"

    book = next(store.iter_all())
    assert book.metadata.title == "Dune"
    assert book.source.resolver == "embedded"


def test_extract_does_not_clobber_a_human_edit(env):
    cfg, db, store = env
    make_epub(cfg.library.incoming / "dune.epub", "Dune", "Frank Herbert")
    scan(cfg, db, store, cfg.library.incoming, NOOP)
    extract(cfg, db, store, force=False, progress=NOOP)

    sha = next(store.iter_all()).sha256
    store.update(sha, lambda b: (
        setattr(b.metadata, "title", "My Title"),
        setattr(b.source, "resolver", "human"),
    ))
    extract(cfg, db, store, force=True, progress=NOOP)
    assert store.load(sha).metadata.title == "My Title"


def test_extract_marks_unreadable_files_as_errors(env):
    cfg, db, store = env
    (cfg.library.incoming / "broken.epub").write_bytes(b"not a zip")
    scan(cfg, db, store, cfg.library.incoming, NOOP)
    result = extract(cfg, db, store, force=False, progress=NOOP)
    assert result["errors"] == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_pipeline.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'reshelf.pipeline'`.

- [ ] **Step 3: Write the implementation**

Create `src/reshelf/pipeline.py`:

```python
"""Pipeline stages as plain functions.

Command bodies used to live in cli.py, where nothing else could call
them. Everything here takes an open Database and a progress callback, so
the CLI and the web job runner share one implementation. The callback
raises JobCancelled to stop a run cooperatively.
"""

from collections.abc import Callable
from pathlib import Path

from reshelf.config import Config
from reshelf.db.database import Database
from reshelf.extractors.base import ExtractionError
from reshelf.extractors.epub import extract_epub
from reshelf.extractors.mobi import extract_mobi
from reshelf.extractors.pdf import extract_pdf
from reshelf.metadata.isbn import find_isbns
from reshelf.scanner.hashing import sha256_file
from reshelf.scanner.scanner import iter_files
from reshelf.store import index
from reshelf.store.models import Book, FileEntry
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
        fid, state = db.upsert_file(fi.path, fi.size, fi.mtime, fi.extension)
        if state == "unchanged":
            continue
        added += state == "new"
        changed += state == "changed"
        if db.set_hash(fid, sha256_file(Path(fi.path))):
            duplicates += 1
        db.conn.commit()
    db.finish_scan_run(run_id, seen, added, changed)
    db.conn.commit()
    return {"seen": seen, "added": added, "changed": changed, "duplicates": duplicates}


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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_pipeline.py -v`
Expected: PASS (6 tests).

- [ ] **Step 4a: Carry a sidecar across a changed hash**

Spec 13 requires this: re-downloading a book as a better scan gives it a new
hash, which would otherwise orphan its sidecar (and, after sub-project B, its
annotations). `upsert_file` nulls `sha256` when a file changes, so the old
hash has to be read *before* that call.

Add to `tests/test_pipeline.py`:

```python
def test_a_changed_file_carries_its_sidecar_to_the_new_hash(env):
    cfg, db, store = env
    path = cfg.library.incoming / "dune.epub"
    make_epub(path, "Dune", "Frank Herbert")
    scan(cfg, db, store, cfg.library.incoming, NOOP)
    extract(cfg, db, store, force=False, progress=NOOP)

    old_sha = next(store.iter_all()).sha256
    store.update(old_sha, lambda b: (
        setattr(b.metadata, "title", "My Corrected Title"),
        setattr(b.source, "resolver", "human"),
    ))

    # Same path, different bytes - a better scan of the same book.
    import os
    make_epub(path, "Dune", "Frank Herbert")
    with open(path, "ab") as fh:
        fh.write(b"\x00" * 64)
    os.utime(path, (0, 0))

    scan(cfg, db, store, cfg.library.incoming, NOOP)

    books = {b.sha256: b for b in store.iter_all()}
    new_sha = db.conn.execute(
        "SELECT sha256 FROM files WHERE path = ?", (str(path),)
    ).fetchone()[0]
    assert new_sha != old_sha
    assert books[new_sha].metadata.title == "My Corrected Title"
    assert books[new_sha].is_human
```

Then in `pipeline.scan`, capture the old hash before `upsert_file` and carry
the sidecar after the new one is computed:

```python
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
```

```python
def _carry_sidecar(db, store, old_sha: str, new_sha: str, fi) -> None:
    """A file's content changed at a known path - keep its metadata.

    # ponytail: keyed on the path staying the same. Proper edition linking
    # only if re-downloads with renames turn out to be common.
    """
    old = store.load(old_sha, fi.path)
    if old is None or store.load(new_sha, fi.path) is not None:
        return
    carried = old.model_copy(deep=True)
    carried.sha256 = new_sha
    for entry in carried.files:
        if entry.path == fi.path:
            entry.size, entry.mtime = fi.size, fi.mtime
    carried.files = [f for f in carried.files if f.role != "converted"]
    store.save(carried, fi.path)
    store.delete(old_sha, fi.path)
    index.remove(db.conn, old_sha)
    index.sync(db.conn, carried)
```

Derived files are dropped deliberately: they were converted from the old
bytes and no longer match. Re-converting is one click.

Run: `.venv/bin/pytest tests/test_pipeline.py -v`
Expected: PASS.

- [ ] **Step 5: Make the CLI commands thin wrappers**

Replace the bodies of `scan` and `extract` in `src/reshelf/cli.py` (keeping the same typer signatures and the same `typer.echo` output strings, because `tests/test_cli.py` asserts on them):

```python
@app.command()
def scan(
    path: Optional[Path] = typer.Argument(None),
    root: Path = ROOT_OPTION,
) -> None:
    """Discover ebook files and record them incrementally."""
    cfg = load_config(root)
    target = path or cfg.library.incoming
    with Database(cfg.database.path) as db:
        db.init_schema()
        r = pipeline.scan(cfg, db, SidecarStore(cfg), Path(target), _bar("Scanning"))
    typer.echo(
        f"seen={r['seen']} added={r['added']} changed={r['changed']}"
        f" duplicates={r['duplicates']}"
    )


@app.command()
def extract(
    root: Path = ROOT_OPTION,
    force: bool = typer.Option(False, "--force", help="Re-extract ERROR/IDENTIFIED files"),
) -> None:
    """Extract embedded metadata from scanned files."""
    cfg = load_config(root)
    with Database(cfg.database.path) as db:
        db.init_schema()
        r = pipeline.extract(cfg, db, SidecarStore(cfg), force, _bar("Extracting"))
    typer.echo(f"extracted={r['extracted']} errors={r['errors']}")
```

Add the imports `from reshelf import pipeline` and `from reshelf.store.sidecar import SidecarStore`, and a shared progress helper near `ROOT_OPTION`:

```python
def _bar(label: str):
    """Progress callback that prints to stderr; the job runner uses its own."""
    console = Console(stderr=True)

    def report(done: int, total: int | None, message: str) -> None:
        suffix = f"/{total}" if total else ""
        console.print(f"{label} {done}{suffix}: {message}", end="\r", highlight=False)

    return report
```

Delete the now-unused `_EXTRACTORS` dict from `cli.py`.

- [ ] **Step 6: Run the whole suite**

Run: `.venv/bin/pytest -q`
Expected: PASS, including `tests/test_cli.py` unchanged.

- [ ] **Step 7: Commit**

```bash
git add src/reshelf/pipeline.py src/reshelf/cli.py tests/test_pipeline.py
git commit -m "refactor(pipeline): scan and extract as callable functions writing sidecars"
```

---
### Task 7: Pipeline refactor — `match`, `resolve`, and `match_one`

Moving `_order_providers`, `_gather_candidates`, `_local_book`, `_build_providers`, `_match_file` and `_BAND_TO_STATUS` out of `cli.py` verbatim, plus two behavioural additions: human-resolved books are skipped, and a successful match writes the sidecar. `match_one` is new and backs the web "Fix metadata" button.

**Files:**
- Modify: `src/reshelf/pipeline.py`
- Modify: `src/reshelf/cli.py` (`match`, `resolve` become wrappers; delete the moved helpers)
- Test: `tests/test_pipeline_match.py`

**Interfaces:**
- Consumes: everything from Task 6, plus `score_candidate`, `confidence_from_score`, `band`, `same_work`, `LocalBook` from `reshelf.matching.scorer`, and the provider classes.
- Produces:
  - `build_providers(cfg, client) -> list` (was `_build_providers`)
  - `match(cfg, db, store, offline: bool, progress) -> dict[str, int]` — counts keyed by status
  - `resolve(cfg, db, store, limit: int, include_unresolved: bool, progress) -> dict` with keys `resolved, skipped, errors`
  - `match_one(cfg, db, store, sha256: str, query: dict | None = None, use_ai: bool = False) -> list[Candidate]` — ranked, never writes a decision
  - `choose(cfg, db, store, sha256: str, candidate_id: int) -> Book` — writes `resolver="human"`
  - `AIDisabledError(RuntimeError)`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_pipeline_match.py`:

```python
import pytest

from reshelf.config import default_config
from reshelf.db.database import Database
from reshelf.metadata.models import Candidate
from reshelf.pipeline import AIDisabledError, choose, match_one
from reshelf.store.models import Book, FileEntry
from reshelf.store.sidecar import SidecarStore

NOOP = lambda *a: None  # noqa: E731


class FakeProvider:
    name = "fake"

    def __init__(self, candidates):
        self._candidates = candidates

    def search(self, local):
        return list(self._candidates)

    def by_isbn(self, isbn):
        return list(self._candidates)

    def enrich(self, candidate):
        return candidate


@pytest.fixture
def env(tmp_path):
    cfg = default_config(tmp_path)
    db = Database(cfg.database.path)
    db.init_schema()
    store = SidecarStore(cfg)
    book = Book(
        sha256="a" * 64,
        files=[FileEntry(path="incoming/dune.epub", format="epub")],
    )
    book.metadata.title = "Dune"
    store.save(book)
    db.conn.execute(
        "INSERT INTO files (path, sha256, format, status, title_raw)"
        " VALUES ('incoming/dune.epub', ?, 'epub', 'IDENTIFIED', 'Dune')",
        ("a" * 64,),
    )
    db.conn.commit()
    yield cfg, db, store
    db.close()


def test_match_one_returns_ranked_candidates_without_deciding(env, monkeypatch):
    cfg, db, store = env
    cands = [
        Candidate(provider="fake", provider_id="1", title="Dune",
                  authors=["Frank Herbert"]),
        Candidate(provider="fake", provider_id="2", title="Dune Messiah",
                  authors=["Frank Herbert"]),
    ]
    monkeypatch.setattr(
        "reshelf.pipeline.build_providers", lambda cfg, client: [FakeProvider(cands)]
    )
    result = match_one(cfg, db, store, "a" * 64)
    assert [c.title for c in result] == ["Dune", "Dune Messiah"]
    assert store.load("a" * 64).source.resolver == "embedded"  # unchanged


def test_match_one_honours_a_query_override(env, monkeypatch):
    cfg, db, store = env
    seen = {}

    class Recorder(FakeProvider):
        def search(self, local):
            seen["title"] = local.title
            return []

    monkeypatch.setattr(
        "reshelf.pipeline.build_providers", lambda cfg, client: [Recorder([])]
    )
    match_one(cfg, db, store, "a" * 64, query={"title": "Corrected Title"})
    assert seen["title"] == "Corrected Title"


def test_match_one_refuses_ai_when_no_provider_is_configured(env):
    cfg, db, store = env
    assert cfg.ai.provider is None
    with pytest.raises(AIDisabledError):
        match_one(cfg, db, store, "a" * 64, use_ai=True)


def test_choose_writes_a_sticky_human_decision(env, monkeypatch):
    cfg, db, store = env
    cand = Candidate(
        provider="fake", provider_id="1", title="Dune",
        authors=["Frank Herbert"], isbn13="9780441013593",
    )
    edition_id = db.save_candidate(cand)
    db.conn.commit()

    book = choose(cfg, db, store, "a" * 64, edition_id)
    assert book.metadata.title == "Dune"
    assert book.metadata.isbn13 == "9780441013593"
    assert book.source.resolver == "human"
    assert book.is_human

    from reshelf.store import index

    assert index.query(db.conn, q="Dune")[1] == 1


def test_match_skips_a_human_resolved_book(env, monkeypatch):
    cfg, db, store = env
    store.update("a" * 64, lambda b: setattr(b.source, "resolver", "human"))
    called = []
    monkeypatch.setattr(
        "reshelf.pipeline.build_providers",
        lambda cfg, client: [FakeProvider([]) if called.append(1) else None],
    )
    from reshelf.pipeline import match

    counts = match(cfg, db, store, offline=True, progress=NOOP)
    assert counts.get("SKIPPED_HUMAN") == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_pipeline_match.py -v`
Expected: FAIL with `ImportError: cannot import name 'match_one'`.

- [ ] **Step 3: Move the helpers into `pipeline.py`**

Cut `_HAS_CJK`, `_BAND_TO_STATUS`, `_order_providers`, `_gather_candidates`, `_local_book`, `_build_providers` and `_match_file` from `src/reshelf/cli.py` into `src/reshelf/pipeline.py` **verbatim**, renaming `_build_providers` to `build_providers`. Add the imports they need (`httpx`, `reshelf.__version__`, the scorer functions, `FileCache`, `DoubanProvider`, `OpenLibraryProvider`, `Candidate`).

- [ ] **Step 4: Add the new functions**

Append to `src/reshelf/pipeline.py`:

```python
class AIDisabledError(RuntimeError):
    """Raised when an AI path is requested but ai.provider is unset."""


def _sidecar_from_candidate(book: Book, cand, resolver: str, confidence: float) -> None:
    m = book.metadata
    m.title = cand.title or m.title
    if getattr(cand, "authors", None):
        m.authors = list(cand.authors)
    m.isbn13 = getattr(cand, "isbn13", None) or m.isbn13
    m.isbn10 = getattr(cand, "isbn10", None) or m.isbn10
    m.publisher = getattr(cand, "publisher", None) or m.publisher
    m.pubdate = getattr(cand, "publication_date", None) or m.pubdate
    m.language = getattr(cand, "language", None) or m.language
    book.source.resolver = resolver
    book.source.provider = getattr(cand, "provider", None)
    book.source.provider_id = getattr(cand, "provider_id", None)
    book.source.confidence = confidence
    book.source.decided_at = models_now()


def match(cfg, db, store, offline: bool, progress: Progress) -> dict[str, int]:
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


def _sync_matched_sidecar(db, store, row, book: Book) -> None:
    edition = db.conn.execute(
        "SELECT e.*, w.canonical_title AS title,"
        " (SELECT a.canonical_name FROM work_authors wa"
        "   JOIN authors a ON a.id = wa.author_id"
        "   WHERE wa.work_id = w.id LIMIT 1) AS author"
        " FROM files f JOIN editions e ON e.id = f.matched_edition_id"
        " JOIN works w ON w.id = e.work_id WHERE f.id = ?",
        (row["id"],),
    ).fetchone()
    if edition is None:
        return
    m = book.metadata
    m.title = edition["title"] or m.title
    if edition["author"]:
        m.authors = [edition["author"]]
    m.isbn13 = edition["isbn13"] or m.isbn13
    m.isbn10 = edition["isbn10"] or m.isbn10
    m.publisher = edition["publisher"] or m.publisher
    m.pubdate = edition["publication_date"] or m.pubdate
    m.language = edition["language"] or m.language
    book.source.resolver = "deterministic"
    book.source.decided_at = models_now()
    store.save(book, row["path"])
    index.sync(db.conn, book)


def _row_for(db, sha256: str):
    row = db.conn.execute(
        "SELECT * FROM files WHERE sha256 = ? ORDER BY id LIMIT 1", (sha256,)
    ).fetchone()
    if row is None:
        raise KeyError(sha256)
    return row


def match_one(cfg, db, store, sha256: str, query: dict | None = None,
              use_ai: bool = False) -> list:
    """Rank candidates for one book. Never writes a decision - that is `choose`."""
    if use_ai and not cfg.ai.enabled:
        raise AIDisabledError("ai.provider is not configured")
    row = _row_for(db, sha256)
    local = _local_book(row)
    if query:
        local = LocalBook(
            title=query.get("title") or local.title,
            author=query.get("author") or local.author,
            isbn=query.get("isbn") or local.isbn,
            language=local.language,
            filename=local.filename,
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
        candidates = _ai_rank(cfg, local, candidates)
    return candidates


def _ai_rank(cfg, local, candidates: list) -> list:
    """Ask the configured model to pick among real candidates. Never invents."""
    from reshelf.ai.resolver import AIError, ClaudeCLIResolver

    if cfg.ai.provider != "claude-cli":
        raise AIDisabledError(f"unsupported ai.provider {cfg.ai.provider!r}")
    try:
        pick = ClaudeCLIResolver(cfg.ai).choose(local, candidates)
    except AIError:
        return candidates
    if pick is None:
        return candidates
    chosen = [c for c in candidates if c.provider_id == pick.provider_id]
    rest = [c for c in candidates if c.provider_id != pick.provider_id]
    return chosen + rest


def choose(cfg, db, store, sha256: str, candidate_id: int) -> Book:
    """Commit a candidate as the human's decision. Sticky from here on."""
    row = _row_for(db, sha256)
    edition = db.conn.execute(
        "SELECT e.*, w.canonical_title AS title,"
        " (SELECT a.canonical_name FROM work_authors wa"
        "   JOIN authors a ON a.id = wa.author_id"
        "   WHERE wa.work_id = w.id LIMIT 1) AS author"
        " FROM editions e JOIN works w ON w.id = e.work_id WHERE e.id = ?",
        (candidate_id,),
    ).fetchone()
    if edition is None:
        raise KeyError(candidate_id)
    book = store.load(sha256, row["path"]) or Book(sha256=sha256)
    m = book.metadata
    m.title = edition["title"] or m.title
    if edition["author"]:
        m.authors = [edition["author"]]
    m.isbn13 = edition["isbn13"] or m.isbn13
    m.isbn10 = edition["isbn10"] or m.isbn10
    m.publisher = edition["publisher"] or m.publisher
    m.pubdate = edition["publication_date"] or m.pubdate
    m.language = edition["language"] or m.language
    book.source.resolver = "human"
    book.source.confidence = 1.0
    book.source.decided_at = models_now()
    store.save(book, row["path"])
    db.set_file_match(row["id"], candidate_id, 1.0, "MATCHED")
    db.conn.commit()
    index.sync(db.conn, book)
    return book
```

Add `from reshelf.store.models import now as models_now` to the imports.

`match_one` stashes `cand.edition_id` on each candidate. `Candidate` is a
pydantic model, so check `src/reshelf/metadata/models.py`: if it does not
already declare `edition_id`, add `edition_id: int | None = None` to it
rather than setting an undeclared attribute, which pydantic rejects. The
`rematch` job serialises candidates with `model_dump()`, and the SPA passes
that `edition_id` straight to `POST /choose`, so it must survive the round
trip.

`ClaudeCLIResolver.choose` may not exist under that exact name — check `src/reshelf/ai/resolver.py` and call whatever the existing entry point is, passing the same arguments `resolve` already passes it. Do not add a second AI code path.

- [ ] **Step 5: Add `resolve` to `pipeline.py`**

Move the body of the existing `resolve` typer command into `pipeline.resolve(cfg, db, store, limit, include_unresolved, progress)`, with three changes: raise `AIDisabledError` at the top if `not cfg.ai.enabled`; skip books whose sidecar `is_human`; and call `_sync_matched_sidecar` after a successful resolution. Return `{"resolved": int, "skipped": int, "errors": int}`.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_pipeline_match.py -v`
Expected: PASS (5 tests).

- [ ] **Step 7: Make the CLI commands thin wrappers**

Rewrite `match` and `resolve` in `cli.py` to call `pipeline.match` / `pipeline.resolve`, keeping their existing `typer.echo` output strings so `tests/test_cli.py` passes. Catch `AIDisabledError` and exit 1 with `"ai.provider is not set in config.yaml"`.

- [ ] **Step 8: Run the whole suite and commit**

```bash
.venv/bin/pytest -q
git add src/reshelf/pipeline.py src/reshelf/cli.py tests/test_pipeline_match.py
git commit -m "refactor(pipeline): match/resolve as functions; add match_one and choose"
```

---
### Task 8: Planner and committer read sidecars; `commit_mode`; drop the Calibre hook

Three changes that travel together because they all touch the plan/commit path: `generate_plan` reads metadata from sidecars instead of the `works`/`editions` join, `apply_plan` honours `library.commit_mode`, and the Calibre conversion hook comes out (conversion is now a per-book action, Task 11).

**Files:**
- Modify: `src/reshelf/planner/planner.py`
- Modify: `src/reshelf/planner/committer.py:8` (drop the import), `:62` (drop `convert_kindle`), `:106-135` (drop the conversion branch)
- Modify: `src/reshelf/pipeline.py` (add `plan`, `commit`, `rollback`)
- Modify: `src/reshelf/cli.py`
- Test: `tests/test_planner.py` (extend), `tests/test_committer.py` (extend)

**Interfaces:**
- Consumes: `SidecarStore`, `Book`.
- Produces: `generate_plan(db, store, reports_dir) -> Path` (signature gains `store`), `apply_plan(..., mode: Literal["copy","move"] = "copy")` replacing `convert_kindle`, and `pipeline.plan(cfg, db, store) -> Path`, `pipeline.commit(cfg, db, store, plan_path, progress, **flags) -> dict`, `pipeline.rollback(cfg, db, journal_id, progress) -> dict`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_planner.py`:

```python
def test_plan_metadata_comes_from_the_sidecar_not_the_edition_tables(tmp_path):
    """A human correction in the sidecar must drive the destination path."""
    import json

    from reshelf.config import default_config
    from reshelf.db.database import Database
    from reshelf.planner.planner import generate_plan
    from reshelf.store.models import Book, FileEntry
    from reshelf.store.sidecar import SidecarStore

    cfg = default_config(tmp_path)
    db = Database(cfg.database.path)
    db.init_schema()
    store = SidecarStore(cfg)

    book = Book(
        sha256="a" * 64,
        files=[FileEntry(path="incoming/x.epub", format="epub")],
    )
    book.metadata.title = "Corrected Title"
    book.metadata.authors = ["Real Author"]
    book.source.resolver = "human"
    store.save(book)

    db.conn.execute(
        "INSERT INTO files (path, sha256, format, size, mtime, status)"
        " VALUES ('incoming/x.epub', ?, 'epub', 1, 1, 'MATCHED')",
        ("a" * 64,),
    )
    db.conn.commit()

    plan = json.loads(generate_plan(db, store, tmp_path / "reports").read_text())
    imports = [a for a in plan["actions"] if a["action"] == "import"]
    assert imports[0]["metadata_changes"]["title"] == "Corrected Title"
    assert imports[0]["metadata_changes"]["author"] == "Real Author"
    db.close()
```

Append to `tests/test_committer.py`:

```python
def test_copy_mode_leaves_the_original_in_place(tmp_path):
    env = committed_env(tmp_path, mode="copy")
    assert env["src"].exists()
    assert env["dest"].exists()


def test_move_mode_removes_the_original(tmp_path):
    env = committed_env(tmp_path, mode="move")
    assert not env["src"].exists()
    assert env["dest"].exists()


def test_rollback_restores_a_moved_original(tmp_path):
    env = committed_env(tmp_path, mode="move")
    from reshelf.planner.committer import rollback_journal

    rollback_journal(env["db"], env["journal"], env["reports"])
    assert env["src"].exists()
    assert not env["dest"].exists()


def test_a_kindle_file_is_committed_as_is_not_converted(tmp_path):
    """Conversion is a per-book action now; commit must not silently convert."""
    env = committed_env(tmp_path, mode="copy", fmt="azw3")
    assert env["dest"].suffix == ".azw3"
```

Write a `committed_env(tmp_path, mode="copy", fmt="epub")` helper at the top of `tests/test_committer.py` that builds a config, a database with one MATCHED file, a sidecar, runs `generate_plan` then `apply_plan(..., mode=mode)`, and returns a dict with keys `src`, `dest`, `db`, `journal`, `reports`. Model it on the existing fixtures already in that file.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_planner.py tests/test_committer.py -v`
Expected: FAIL — `generate_plan()` takes 2 positional arguments, and `apply_plan()` has no `mode` keyword.

- [ ] **Step 3: Rewrite the planner's metadata source**

In `src/reshelf/planner/planner.py`, change the signature to `generate_plan(db, store, reports_dir)` and replace the `matched` query and loop with:

```python
    matched = db.conn.execute(
        "SELECT * FROM files WHERE status = 'MATCHED' ORDER BY path"
    ).fetchall()
    for r in matched:
        book = store.load(r["sha256"], r["path"]) if r["sha256"] else None
        if book is None:
            continue  # no sidecar means no metadata to name a destination by
        m = book.metadata
        actions.append(
            {
                "file": r["path"],
                "action": "import",
                "preconditions": _preconditions(r),
                "metadata_changes": {
                    "title": m.title,
                    "author": "; ".join(m.authors) or None,
                    "isbn13": m.isbn13,
                    "publisher": m.publisher,
                    "publication_date": m.pubdate,
                },
            }
        )
```

Add `from reshelf.store.sidecar import SidecarStore` for the type hint.

- [ ] **Step 4: Rewrite the committer's import branch**

In `src/reshelf/planner/committer.py`: delete the `from reshelf.calibre.convert import ...` line, replace the `convert_kindle: bool = True` parameter with `mode: str = "copy"`, and replace the conversion branch with:

```python
                dest, already = _unique_dest(
                    dest_for(action, library_dir),
                    action["preconditions"].get("sha256"),
                )
                if not dry_run:
                    if not already:
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        if mode == "move":
                            shutil.move(action["file"], dest)
                        else:
                            shutil.copy2(action["file"], dest)
                    db.set_status(row["id"], "COMMITTED")
                    db.conn.commit()
                done.append(
                    {
                        "action": "import",
                        "src": action["file"],
                        "dest": str(dest),
                        "moved": mode == "move",
                    }
                )
```

In `rollback_journal`, an entry with `"moved": True` is undone by moving `dest` back to `src`; an entry without it is undone by deleting `dest`, which is the existing behaviour. Find the import-undo branch and add the move case.

- [ ] **Step 5: Add the stages to `pipeline.py`**

```python
def plan(cfg, db, store) -> Path:
    return generate_plan(db, store, Path(cfg.library.root) / "reports")


def commit(cfg, db, store, plan_path: Path, progress: Progress,
           dry_run: bool = False, do_quarantine: bool = False,
           do_duplicates: bool = False) -> dict:
    progress(0, None, f"applying {Path(plan_path).name}")
    root = Path(cfg.library.root)
    result = apply_plan(
        db,
        json.loads(Path(plan_path).read_text()),
        library_dir=root / "library",
        quarantine_dir=cfg.library.quarantine,
        duplicates_dir=root / "duplicates",
        reports_dir=root / "reports",
        dry_run=dry_run,
        do_quarantine=do_quarantine,
        do_duplicates=do_duplicates,
        mode=cfg.library.commit_mode,
    )
    for entry in result.get("done", []):
        _record_committed_path(db, store, entry)
    progress(len(result.get("done", [])), None, "done")
    return result


def _record_committed_path(db, store, entry: dict) -> None:
    """Keep the sidecar's files[] honest after a copy or move."""
    if entry.get("action") != "import":
        return
    row = db.conn.execute(
        "SELECT sha256, format FROM files WHERE path = ?", (entry["src"],)
    ).fetchone()
    if row is None or not row["sha256"]:
        return
    dest = entry["dest"]

    def mutate(book: Book) -> None:
        if entry.get("moved"):
            book.files = [f for f in book.files if f.path != entry["src"]]
        if not any(f.path == dest for f in book.files):
            book.files.append(FileEntry(path=dest, format=row["format"] or ""))

    book = store.update(row["sha256"], mutate, entry["src"])
    index.sync(db.conn, book)


def rollback(cfg, db, journal_id: str, progress: Progress) -> dict:
    progress(0, None, f"rolling back {journal_id}")
    return rollback_journal(db, journal_id, Path(cfg.library.root) / "reports")
```

Add `import json` and the `generate_plan`, `apply_plan`, `rollback_journal` imports to `pipeline.py`.

- [ ] **Step 6: Update the CLI and run the suite**

Point `cli.py`'s `plan`, `commit` and `rollback` commands at the `pipeline` functions, passing a `SidecarStore`. Keep their `typer.echo` output strings.

Run: `.venv/bin/pytest -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/reshelf/planner src/reshelf/pipeline.py src/reshelf/cli.py tests/test_planner.py tests/test_committer.py
git commit -m "feat(planner): plan from sidecars, commit_mode copy/move, no Calibre in commit"
```

---

### Task 9: Cover extraction

Nothing populates `covers/` today, and a library grid without covers is unusable.

**Files:**
- Create: `src/reshelf/covers.py`
- Modify: `src/reshelf/pipeline.py` (`extract` also extracts a cover)
- Test: `tests/test_covers.py`

**Interfaces:**
- Consumes: `pymupdf` (already a dependency), `zipfile`.
- Produces: `extract_cover(src: Path, dest: Path, max_edge: int = 600) -> bool`, `thumb_path(covers_dir: Path, sha256: str) -> Path`, `cover_path(covers_dir: Path, sha256: str) -> Path`, `ensure_cover(covers_dir: Path, sha256: str, src: Path) -> bool`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_covers.py`:

```python
import zipfile

import pytest

from reshelf.covers import cover_path, ensure_cover, extract_cover, thumb_path
from tests.helpers import make_epub, make_pdf

SHA = "a" * 64


def epub_with_cover(path):
    make_epub(path, "Dune", "Frank Herbert")
    import pymupdf as fitz

    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 40, 60))
    pix.clear_with(200)
    with zipfile.ZipFile(path, "a") as z:
        z.writestr("cover.jpg", pix.tobytes("jpeg"))
        # Re-point the OPF at the cover so the extractor can find it.
        opf = z.read("content.opf").decode()
        opf = opf.replace(
            "<manifest>",
            '<manifest><item id="cover" href="cover.jpg" media-type="image/jpeg"'
            ' properties="cover-image"/>',
        )
        z.writestr("content2.opf", opf)
    with zipfile.ZipFile(path, "a") as z:
        z.writestr("content.opf", opf)
    return path


def test_pdf_cover_is_the_first_page(tmp_path):
    src = make_pdf(tmp_path / "b.pdf", "Dune", "Herbert", text="page one")
    dest = tmp_path / "out.jpg"
    assert extract_cover(src, dest) is True
    assert dest.exists() and dest.stat().st_size > 0


def test_epub_cover_comes_from_the_manifest(tmp_path):
    src = epub_with_cover(tmp_path / "b.epub")
    dest = tmp_path / "out.jpg"
    assert extract_cover(src, dest) is True
    assert dest.exists() and dest.stat().st_size > 0


def test_an_epub_with_no_cover_reports_failure_without_raising(tmp_path):
    src = make_epub(tmp_path / "b.epub", "Dune", "Herbert")
    assert extract_cover(src, tmp_path / "out.jpg") is False


def test_an_unsupported_format_reports_failure(tmp_path):
    src = tmp_path / "b.azw3"
    src.write_bytes(b"not really")
    assert extract_cover(src, tmp_path / "out.jpg") is False


def test_a_corrupt_file_reports_failure_without_raising(tmp_path):
    src = tmp_path / "b.pdf"
    src.write_bytes(b"%PDF-1.4 garbage")
    assert extract_cover(src, tmp_path / "out.jpg") is False


def test_cover_is_downscaled_to_max_edge(tmp_path):
    import pymupdf as fitz

    src = make_pdf(tmp_path / "b.pdf", "Dune", "Herbert")
    dest = tmp_path / "out.jpg"
    extract_cover(src, dest, max_edge=100)
    pix = fitz.Pixmap(str(dest))
    assert max(pix.width, pix.height) <= 100


def test_ensure_cover_writes_both_sizes_and_is_idempotent(tmp_path):
    covers = tmp_path / "covers"
    src = make_pdf(tmp_path / "b.pdf", "Dune", "Herbert")
    assert ensure_cover(covers, SHA, src) is True
    assert cover_path(covers, SHA).exists()
    assert thumb_path(covers, SHA).exists()
    mtime = cover_path(covers, SHA).stat().st_mtime_ns
    assert ensure_cover(covers, SHA, src) is True
    assert cover_path(covers, SHA).stat().st_mtime_ns == mtime  # not rewritten
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_covers.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'reshelf.covers'`.

- [ ] **Step 3: Write the implementation**

Create `src/reshelf/covers.py`:

```python
"""Cover images for the library grid.

EPUB: the manifest item marked properties="cover-image", falling back to
the first image in the zip. PDF: the first page, rendered by pymupdf.
MOBI/AZW3 get nothing until they are converted (Task 11), because there
is no Calibre here to read them.
"""

import re
import zipfile
from pathlib import Path

import pymupdf as fitz

_COVER_ITEM = re.compile(
    r'<item[^>]*properties="[^"]*cover-image[^"]*"[^>]*href="([^"]+)"'
    r"|"
    r'<item[^>]*href="([^"]+)"[^>]*properties="[^"]*cover-image[^"]*"',
    re.IGNORECASE,
)
_IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp", ".gif")


def cover_path(covers_dir: Path, sha256: str) -> Path:
    return Path(covers_dir) / f"{sha256}.jpg"


def thumb_path(covers_dir: Path, sha256: str) -> Path:
    return Path(covers_dir) / f"{sha256}.thumb.jpg"


def _save(pix: fitz.Pixmap, dest: Path, max_edge: int) -> None:
    longest = max(pix.width, pix.height)
    if longest > max_edge:
        scale = max_edge / longest
        pix = fitz.Pixmap(
            pix, 0
        ) if pix.alpha else pix
        matrix = fitz.Matrix(scale, scale)
        doc = fitz.open()
        page = doc.new_page(width=pix.width, height=pix.height)
        page.insert_image(page.rect, pixmap=pix)
        pix = page.get_pixmap(matrix=matrix)
        doc.close()
    dest.parent.mkdir(parents=True, exist_ok=True)
    pix.save(str(dest))


def _from_pdf(src: Path, dest: Path, max_edge: int) -> bool:
    with fitz.open(str(src)) as doc:
        if doc.page_count == 0:
            return False
        pix = doc.load_page(0).get_pixmap()
    _save(pix, dest, max_edge)
    return True


def _epub_cover_bytes(src: Path) -> bytes | None:
    with zipfile.ZipFile(src) as z:
        names = z.namelist()
        opfs = [n for n in names if n.lower().endswith(".opf")]
        for opf in opfs:
            match = _COVER_ITEM.search(z.read(opf).decode("utf-8", "replace"))
            if not match:
                continue
            href = match.group(1) or match.group(2)
            base = Path(opf).parent
            for candidate in (str(base / href), href):
                normalized = candidate.replace("\\", "/").lstrip("./")
                if normalized in names:
                    return z.read(normalized)
        images = [n for n in names if n.lower().endswith(_IMAGE_SUFFIXES)]
        if images:
            return z.read(sorted(images)[0])
    return None


def _from_epub(src: Path, dest: Path, max_edge: int) -> bool:
    data = _epub_cover_bytes(src)
    if not data:
        return False
    _save(fitz.Pixmap(data), dest, max_edge)
    return True


def extract_cover(src: Path, dest: Path, max_edge: int = 600) -> bool:
    """True if a cover was written. Never raises on a bad file."""
    src, dest = Path(src), Path(dest)
    suffix = src.suffix.lower()
    try:
        if suffix == ".pdf":
            return _from_pdf(src, dest, max_edge)
        if suffix == ".epub":
            return _from_epub(src, dest, max_edge)
    except Exception:
        return False
    return False


def ensure_cover(covers_dir: Path, sha256: str, src: Path) -> bool:
    """Extract full and thumbnail covers unless they already exist."""
    full, thumb = cover_path(covers_dir, sha256), thumb_path(covers_dir, sha256)
    if full.exists() and thumb.exists():
        return True
    ok = extract_cover(src, full, max_edge=600)
    if not ok:
        return False
    return extract_cover(src, thumb, max_edge=200)
```

If `_save`'s downscale path proves awkward in pymupdf's API, replace its body with `pix.shrink(n)` — pymupdf's integer-factor shrink — and assert `<= max_edge` rather than an exact size. The test only requires the longest edge not to exceed `max_edge`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_covers.py -v`
Expected: PASS (7 tests).

- [ ] **Step 5: Hook it into `extract`**

In `pipeline.extract`, after `_write_extracted_sidecar(...)`, add:

```python
        ensure_cover(Path(cfg.library.root) / "covers", row["sha256"], path)
```

guarded by `if row["sha256"]:`. Add the import.

- [ ] **Step 6: Run the whole suite and commit**

```bash
.venv/bin/pytest -q
git add src/reshelf/covers.py src/reshelf/pipeline.py tests/test_covers.py
git commit -m "feat(covers): extract EPUB and PDF cover images during extract"
```

---
### Task 10: Minimal EPUB writer and OPF rewriter

One module, two jobs: build a valid EPUB 3 from scratch (needed by the TXT and MOBI6 converters) and rewrite the metadata inside an existing one (needed by tier-3 embed). Both are zip surgery, so they share a module and no EPUB library is added.

**Files:**
- Create: `src/reshelf/convert/__init__.py` (empty)
- Create: `src/reshelf/convert/epub_writer.py`
- Test: `tests/test_epub_writer.py`

**Interfaces:**
- Consumes: `zipfile`, `BookMetadata` (Task 3).
- Produces:
  - `write_epub(dest: Path, *, title: str, authors: list[str], language: str = "en", chapters: list[tuple[str, str]], identifier: str | None = None, resources: dict[str, bytes] | None = None) -> Path` — `chapters` is `[(filename, xhtml_body), ...]`
  - `rewrite_metadata(path: Path, metadata: BookMetadata) -> None` — in place, atomically
  - `find_opf(z: zipfile.ZipFile) -> str` — the OPF's name inside the zip
  - `UnsupportedEpub(Exception)`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_epub_writer.py`:

```python
import zipfile

import pytest

from reshelf.convert.epub_writer import UnsupportedEpub, rewrite_metadata, write_epub
from reshelf.store.models import BookMetadata
from tests.helpers import make_epub


def test_mimetype_is_first_and_stored_uncompressed(tmp_path):
    """The EPUB spec requires this exactly; readers reject it otherwise."""
    dest = write_epub(
        tmp_path / "out.epub",
        title="T",
        authors=["A"],
        chapters=[("c1.xhtml", "<p>hello</p>")],
    )
    with zipfile.ZipFile(dest) as z:
        first = z.infolist()[0]
        assert first.filename == "mimetype"
        assert first.compress_type == zipfile.ZIP_STORED
        assert z.read("mimetype") == b"application/epub+zip"


def test_written_epub_has_the_required_parts(tmp_path):
    dest = write_epub(
        tmp_path / "out.epub",
        title="Dune",
        authors=["Frank Herbert"],
        chapters=[("c1.xhtml", "<p>hello</p>")],
    )
    with zipfile.ZipFile(dest) as z:
        names = z.namelist()
        assert "META-INF/container.xml" in names
        assert "content.opf" in names
        assert "nav.xhtml" in names
        assert "c1.xhtml" in names
        opf = z.read("content.opf").decode()
        assert "<dc:title>Dune</dc:title>" in opf
        assert "Frank Herbert" in opf


def test_the_written_epub_round_trips_through_the_existing_extractor(tmp_path):
    from reshelf.extractors.epub import extract_epub

    dest = write_epub(
        tmp_path / "out.epub",
        title="Dune",
        authors=["Frank Herbert"],
        language="en",
        chapters=[("c1.xhtml", "<p>hello</p>")],
    )
    meta = extract_epub(dest)
    assert meta.title == "Dune"
    assert meta.authors == ["Frank Herbert"]


def test_titles_with_xml_special_characters_are_escaped(tmp_path):
    dest = write_epub(
        tmp_path / "out.epub",
        title="Tom & Jerry <the> \"book\"",
        authors=["A & B"],
        chapters=[("c1.xhtml", "<p>x</p>")],
    )
    from reshelf.extractors.epub import extract_epub

    assert extract_epub(dest).title == 'Tom & Jerry <the> "book"'


def test_resources_are_included(tmp_path):
    dest = write_epub(
        tmp_path / "out.epub",
        title="T",
        authors=["A"],
        chapters=[("c1.xhtml", '<img src="img/a.png"/>')],
        resources={"img/a.png": b"\x89PNG\r\n\x1a\n"},
    )
    with zipfile.ZipFile(dest) as z:
        assert z.read("img/a.png").startswith(b"\x89PNG")


def test_rewrite_metadata_replaces_title_and_author(tmp_path):
    src = make_epub(tmp_path / "b.epub", "Old Title", "Old Author")
    rewrite_metadata(src, BookMetadata(title="New Title", authors=["New Author"]))
    from reshelf.extractors.epub import extract_epub

    meta = extract_epub(src)
    assert meta.title == "New Title"
    assert meta.authors == ["New Author"]


def test_rewrite_metadata_preserves_the_content_files(tmp_path):
    src = make_epub(tmp_path / "b.epub", "Old", "Old")
    with zipfile.ZipFile(src) as z:
        before = z.read("chapter1.xhtml")
    rewrite_metadata(src, BookMetadata(title="New", authors=["New"]))
    with zipfile.ZipFile(src) as z:
        assert z.read("chapter1.xhtml") == before
        assert z.infolist()[0].filename == "mimetype"


def test_rewrite_metadata_on_a_non_epub_raises(tmp_path):
    bad = tmp_path / "b.epub"
    bad.write_bytes(b"not a zip")
    with pytest.raises(UnsupportedEpub):
        rewrite_metadata(bad, BookMetadata(title="x"))


def test_a_failed_rewrite_leaves_the_original_intact(tmp_path, monkeypatch):
    src = make_epub(tmp_path / "b.epub", "Old Title", "Old Author")
    monkeypatch.setattr("os.replace", lambda *a, **k: (_ for _ in ()).throw(OSError()))
    with pytest.raises(OSError):
        rewrite_metadata(src, BookMetadata(title="New"))
    from reshelf.extractors.epub import extract_epub

    assert extract_epub(src).title == "Old Title"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_epub_writer.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'reshelf.convert'`.

- [ ] **Step 3: Write the implementation**

Create `src/reshelf/convert/__init__.py` (empty) and `src/reshelf/convert/epub_writer.py`:

```python
"""Build and rewrite EPUB 3 containers.

A zip cannot be edited in place, so rewriting rebuilds into a temp file
and os.replace()s it over the original. `mimetype` must be the first
entry and stored uncompressed or readers reject the file.
"""

import os
import re
import uuid
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

from reshelf.store.models import BookMetadata

MIMETYPE = "application/epub+zip"

CONTAINER = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>"""

_MEDIA_TYPES = {
    ".xhtml": "application/xhtml+xml",
    ".html": "application/xhtml+xml",
    ".css": "text/css",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
    ".webp": "image/webp",
}

XHTML = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>{title}</title></head>
<body>{body}</body></html>"""


class UnsupportedEpub(Exception):
    pass


def _media_type(name: str) -> str:
    return _MEDIA_TYPES.get(Path(name).suffix.lower(), "application/octet-stream")


def _opf(title, authors, language, identifier, chapters, resources) -> str:
    items = []
    for i, (name, _) in enumerate(chapters):
        items.append(
            f'<item id="c{i}" href={quoteattr(name)}'
            f' media-type="application/xhtml+xml"/>'
        )
    for i, name in enumerate(sorted(resources or {})):
        items.append(
            f'<item id="r{i}" href={quoteattr(name)}'
            f' media-type={quoteattr(_media_type(name))}/>'
        )
    items.append('<item id="nav" href="nav.xhtml"'
                 ' media-type="application/xhtml+xml" properties="nav"/>')
    spine = "".join(f'<itemref idref="c{i}"/>' for i in range(len(chapters)))
    creators = "".join(f"<dc:creator>{escape(a)}</dc:creator>" for a in authors)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="pub-id">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="pub-id">{escape(identifier)}</dc:identifier>
    <dc:title>{escape(title or "Untitled")}</dc:title>
    {creators}
    <dc:language>{escape(language or "en")}</dc:language>
  </metadata>
  <manifest>{"".join(items)}</manifest>
  <spine>{spine}</spine>
</package>"""


def _write_zip(dest: Path, entries: list[tuple[str, bytes]]) -> Path:
    """mimetype first and stored; everything else deflated. Atomic."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".tmp")
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr(
                zipfile.ZipInfo("mimetype"), MIMETYPE, compress_type=zipfile.ZIP_STORED
            )
            for name, data in entries:
                if name == "mimetype":
                    continue
                z.writestr(name, data)
        os.replace(tmp, dest)
    except OSError:
        Path(tmp).unlink(missing_ok=True)
        raise
    return dest


def write_epub(
    dest: Path,
    *,
    title: str,
    authors: list[str],
    language: str = "en",
    chapters: list[tuple[str, str]],
    identifier: str | None = None,
    resources: dict[str, bytes] | None = None,
) -> Path:
    identifier = identifier or f"urn:uuid:{uuid.uuid4()}"
    entries: list[tuple[str, bytes]] = [
        ("META-INF/container.xml", CONTAINER.encode()),
        (
            "content.opf",
            _opf(title, authors, language, identifier, chapters, resources).encode(),
        ),
        (
            "nav.xhtml",
            XHTML.format(
                title=escape(title or "Untitled"),
                body='<nav epub:type="toc" xmlns:epub="http://www.idpf.org/2007/ops">'
                "<ol>"
                + "".join(
                    f"<li><a href={quoteattr(n)}>{escape(n)}</a></li>"
                    for n, _ in chapters
                )
                + "</ol></nav>",
            ).encode(),
        ),
    ]
    for name, body in chapters:
        entries.append(
            (name, XHTML.format(title=escape(title or ""), body=body).encode())
        )
    for name, data in (resources or {}).items():
        entries.append((name, data))
    return _write_zip(dest, entries)


def find_opf(z: zipfile.ZipFile) -> str:
    try:
        container = z.read("META-INF/container.xml").decode("utf-8", "replace")
    except KeyError:
        container = ""
    match = re.search(r'full-path="([^"]+)"', container)
    if match:
        return match.group(1)
    opfs = [n for n in z.namelist() if n.lower().endswith(".opf")]
    if not opfs:
        raise UnsupportedEpub("no OPF found")
    return opfs[0]


def _replace_tag(opf: str, tag: str, value: str | None) -> str:
    pattern = re.compile(rf"<dc:{tag}\b[^>]*>.*?</dc:{tag}>", re.DOTALL)
    if value is None:
        return pattern.sub("", opf)
    replacement = f"<dc:{tag}>{escape(value)}</dc:{tag}>"
    if pattern.search(opf):
        return pattern.sub(replacement, opf, count=1)
    return opf.replace("<metadata", "<metadata", 1).replace(
        ">", ">" + replacement, 1
    ) if "<metadata" not in opf else re.sub(
        r"(<metadata[^>]*>)", rf"\1{replacement}", opf, count=1
    )


def rewrite_metadata(path: Path, metadata: BookMetadata) -> None:
    path = Path(path)
    try:
        with zipfile.ZipFile(path) as z:
            opf_name = find_opf(z)
            entries = [(n, z.read(n)) for n in z.namelist()]
            opf = z.read(opf_name).decode("utf-8", "replace")
    except zipfile.BadZipFile as e:
        raise UnsupportedEpub(str(e)) from e

    opf = _replace_tag(opf, "title", metadata.title)
    opf = re.sub(r"<dc:creator\b[^>]*>.*?</dc:creator>", "", opf, flags=re.DOTALL)
    creators = "".join(f"<dc:creator>{escape(a)}</dc:creator>" for a in metadata.authors)
    opf = re.sub(r"(<metadata[^>]*>)", rf"\1{creators}", opf, count=1)
    if metadata.language:
        opf = _replace_tag(opf, "language", metadata.language)
    if metadata.publisher:
        opf = _replace_tag(opf, "publisher", metadata.publisher)

    entries = [(n, opf.encode() if n == opf_name else d) for n, d in entries]
    _write_zip(path, entries)
```

`_replace_tag`'s fallback branch is convoluted; simplify it to the `re.sub(r"(<metadata[^>]*>)", ...)` form used below it and delete the ternary. The tests define the required behaviour.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_epub_writer.py -v`
Expected: PASS (9 tests).

- [ ] **Step 5: Commit**

```bash
git add src/reshelf/convert tests/test_epub_writer.py
git commit -m "feat(convert): minimal EPUB 3 writer and in-place OPF metadata rewriter"
```

---
### Task 11: Converters — MOBI/AZW3/TXT to EPUB, DjVu to PDF

**Files:**
- Create: `src/reshelf/convert/converters.py`
- Delete: `src/reshelf/calibre/convert.py`
- Modify: `pyproject.toml` (add `mobi>=0.3`, `fastapi`, `uvicorn[standard]`, `sse-starlette`)
- Modify: `src/reshelf/pipeline.py` (add `convert_book`)
- Test: `tests/test_convert.py`

**Interfaces:**
- Consumes: `write_epub` (Task 10), `SidecarStore`, `index`.
- Produces:
  - `ConversionError(Exception)`
  - `TARGETS: dict[str, str]` — `{"mobi": "epub", "azw": "epub", "azw3": "epub", "txt": "epub", "djvu": "pdf"}`
  - `target_format(fmt: str) -> str | None`
  - `available() -> dict[str, bool]` — `{"mobi": bool, "txt": True, "djvu": bool}`
  - `convert(src: Path, dest: Path, timeout: int = 300) -> Path`
  - `pipeline.convert_book(cfg, db, store, sha256: str, progress) -> str` returning the derived path

- [ ] **Step 1: Write the failing tests**

Create `tests/test_convert.py`:

```python
import shutil
import zipfile

import pytest

from reshelf.convert.converters import (
    ConversionError,
    available,
    convert,
    target_format,
)

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


def test_target_formats():
    assert target_format("azw3") == "epub"
    assert target_format("mobi") == "epub"
    assert target_format("txt") == "epub"
    assert target_format("djvu") == "pdf"
    assert target_format("epub") is None
    assert target_format("pdf") is None


def test_availability_reports_the_missing_ddjvu_binary():
    caps = available()
    assert caps["txt"] is True
    assert caps["djvu"] is (shutil.which("ddjvu") is not None)


def test_txt_becomes_a_readable_epub(tmp_path):
    src = tmp_path / "book.txt"
    src.write_text("Chapter one.\n\nChapter two.\n", encoding="utf-8")
    dest = convert(src, tmp_path / "out.epub")
    assert dest.exists()
    with zipfile.ZipFile(dest) as z:
        assert z.infolist()[0].filename == "mimetype"
    from reshelf.extractors.epub import extract_epub

    assert extract_epub(dest).title == "book"


def test_txt_in_gbk_is_decoded_not_mangled(tmp_path):
    """A Chinese library is full of GBK text files."""
    src = tmp_path / "zh.txt"
    src.write_bytes("第一章 起点\n\n正文内容\n".encode("gbk"))
    dest = convert(src, tmp_path / "out.epub")
    with zipfile.ZipFile(dest) as z:
        body = "".join(
            z.read(n).decode("utf-8") for n in z.namelist() if n.endswith(".xhtml")
        )
    assert "第一章 起点" in body


def test_txt_content_is_html_escaped(tmp_path):
    src = tmp_path / "book.txt"
    src.write_text("a < b & c > d", encoding="utf-8")
    dest = convert(src, tmp_path / "out.epub")
    with zipfile.ZipFile(dest) as z:
        body = "".join(
            z.read(n).decode("utf-8") for n in z.namelist() if n.endswith(".xhtml")
        )
    assert "&lt;" in body and "&amp;" in body


def test_an_unconvertible_format_raises(tmp_path):
    src = tmp_path / "book.epub"
    src.write_bytes(b"x")
    with pytest.raises(ConversionError, match="nothing to convert"):
        convert(src, tmp_path / "out.epub")


def test_a_corrupt_mobi_raises_conversion_error_not_a_crash(tmp_path):
    src = tmp_path / "book.mobi"
    src.write_bytes(b"definitely not a mobi")
    with pytest.raises(ConversionError):
        convert(src, tmp_path / "out.epub")


@pytest.mark.skipif(shutil.which("ddjvu") is None, reason="djvulibre not installed")
def test_djvu_becomes_a_pdf(tmp_path):
    pytest.skip("needs a sample .djvu fixture; see Step 6")


def test_djvu_without_ddjvu_raises_a_clear_error(tmp_path, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: None)
    src = tmp_path / "book.djvu"
    src.write_bytes(b"AT&TFORM")
    with pytest.raises(ConversionError, match="ddjvu"):
        convert(src, tmp_path / "out.pdf")
```

Create `tests/test_convert_pipeline.py`:

```python
import pytest

from reshelf.config import default_config
from reshelf.db.database import Database
from reshelf.pipeline import convert_book
from reshelf.store.models import Book, FileEntry
from reshelf.store.sidecar import SidecarStore

NOOP = lambda *a: None  # noqa: E731
SHA = "a" * 64


@pytest.fixture
def env(tmp_path):
    cfg = default_config(tmp_path)
    cfg.library.incoming.mkdir(parents=True)
    src = cfg.library.incoming / "book.txt"
    src.write_text("hello\n\nworld\n", encoding="utf-8")

    db = Database(cfg.database.path)
    db.init_schema()
    db.conn.execute(
        "INSERT INTO files (path, sha256, format, status) VALUES (?,?, 'txt','IDENTIFIED')",
        (str(src), SHA),
    )
    db.conn.commit()

    store = SidecarStore(cfg)
    store.save(Book(sha256=SHA, files=[FileEntry(path=str(src), format="txt")]))
    yield cfg, db, store
    db.close()


def test_convert_book_appends_a_derived_entry_and_keeps_the_key(env):
    cfg, db, store = env
    dest = convert_book(cfg, db, store, SHA, NOOP)
    book = store.load(SHA)
    assert book.sha256 == SHA  # the book is still keyed by the original
    derived = [f for f in book.files if f.role == "converted"]
    assert len(derived) == 1
    assert derived[0].path == dest
    assert derived[0].format == "epub"
    assert derived[0].sha256 and derived[0].sha256 != SHA


def test_the_original_file_survives(env):
    cfg, db, store = env
    original = store.load(SHA).files[0].path
    convert_book(cfg, db, store, SHA, NOOP)
    from pathlib import Path

    assert Path(original).exists()


def test_reconverting_replaces_rather_than_duplicates(env):
    cfg, db, store = env
    convert_book(cfg, db, store, SHA, NOOP)
    convert_book(cfg, db, store, SHA, NOOP)
    derived = [f for f in store.load(SHA).files if f.role == "converted"]
    assert len(derived) == 1


def test_the_derived_file_becomes_the_primary(env):
    cfg, db, store = env
    convert_book(cfg, db, store, SHA, NOOP)
    assert store.load(SHA).primary_file().format == "epub"


def test_converting_an_already_epub_book_raises(env):
    cfg, db, store = env
    db.conn.execute("UPDATE files SET format='epub' WHERE sha256=?", (SHA,))
    db.conn.commit()
    store.update(SHA, lambda b: setattr(b.files[0], "format", "epub"))
    from reshelf.convert.converters import ConversionError

    with pytest.raises(ConversionError):
        convert_book(cfg, db, store, SHA, NOOP)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_convert.py tests/test_convert_pipeline.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'reshelf.convert.converters'`.

- [ ] **Step 3: Add the dependency**

In `pyproject.toml`, add to `dependencies`:

```toml
    "mobi>=0.3",            # GPL-3.0; self-hosted only, see the spec
    "fastapi>=0.115",
    "uvicorn[standard]>=0.30",
    "sse-starlette>=2.1",
```

Run: `.venv/bin/pip install -e '.[dev]'`

- [ ] **Step 4: Write the converters**

Create `src/reshelf/convert/converters.py`:

```python
"""Format conversion without Calibre.

MOBI/AZW3 go through the `mobi` package (a KindleUnpack fork): AZW3/KF8
comes out as an EPUB directly, older MOBI6 as HTML plus resources, which
we assemble with epub_writer. TXT is wrapped. DjVu is scanned page
images, so it targets PDF via ddjvu rather than a zip of JPEGs.
"""

import html
import shutil
import subprocess
import tempfile
from pathlib import Path

from reshelf.convert.epub_writer import write_epub

TARGETS = {
    "mobi": "epub",
    "azw": "epub",
    "azw3": "epub",
    "txt": "epub",
    "djvu": "pdf",
}

# Tried in order; a Chinese library is full of GBK text files.
_ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "big5", "latin-1")
_RESOURCE_SUFFIXES = (".jpg", ".jpeg", ".png", ".gif", ".svg", ".css", ".webp")


class ConversionError(Exception):
    pass


def target_format(fmt: str) -> str | None:
    return TARGETS.get((fmt or "").lower().lstrip("."))


def available() -> dict[str, bool]:
    try:
        import mobi  # noqa: F401

        has_mobi = True
    except ImportError:
        has_mobi = False
    return {
        "mobi": has_mobi,
        "txt": True,
        "djvu": shutil.which("ddjvu") is not None,
    }


def _decode(data: bytes) -> str:
    for encoding in _ENCODINGS:
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace")


def _txt_to_epub(src: Path, dest: Path) -> Path:
    text = _decode(src.read_bytes())
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    body = "".join(f"<p>{html.escape(p)}</p>" for p in paragraphs) or "<p></p>"
    return write_epub(
        dest,
        title=src.stem,
        authors=[],
        chapters=[("text.xhtml", body)],
    )


def _mobi_to_epub(src: Path, dest: Path) -> Path:
    try:
        import mobi
    except ImportError as e:
        raise ConversionError("the `mobi` package is not installed") from e
    try:
        tempdir, produced = mobi.extract(str(src))
    except Exception as e:  # KindleUnpack raises a zoo of exceptions
        raise ConversionError(f"could not unpack {src.name}: {e}") from e
    try:
        produced = Path(produced)
        if produced.suffix.lower() == ".epub":
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(produced, dest)
            return dest
        if produced.suffix.lower() in (".html", ".htm"):
            return _html_tree_to_epub(produced, dest, src.stem)
        raise ConversionError(f"unpacked to an unusable {produced.suffix} file")
    finally:
        shutil.rmtree(tempdir, ignore_errors=True)


def _html_tree_to_epub(html_file: Path, dest: Path, title: str) -> Path:
    """MOBI6 unpacks to one HTML file plus a sibling resource tree."""
    root = html_file.parent
    body = _decode(html_file.read_bytes())
    resources: dict[str, bytes] = {}
    for path in root.rglob("*"):
        if path.is_file() and path.suffix.lower() in _RESOURCE_SUFFIXES:
            resources[path.relative_to(root).as_posix()] = path.read_bytes()
    return write_epub(
        dest,
        title=title,
        authors=[],
        chapters=[("text.xhtml", body)],
        resources=resources,
    )


def _djvu_to_pdf(src: Path, dest: Path, timeout: int) -> Path:
    if shutil.which("ddjvu") is None:
        raise ConversionError("ddjvu not found (install djvulibre-bin)")
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        proc = subprocess.run(
            ["ddjvu", "-format=pdf", "-quality=85", str(src), str(tmp_path)],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if proc.returncode != 0 or tmp_path.stat().st_size == 0:
            tail = (proc.stderr or proc.stdout).strip().splitlines()[-1:] or ["failed"]
            raise ConversionError(tail[0][:200])
        shutil.move(str(tmp_path), dest)
        return dest
    except subprocess.TimeoutExpired as e:
        raise ConversionError(f"ddjvu timed out after {timeout}s") from e
    finally:
        tmp_path.unlink(missing_ok=True)


def convert(src: Path, dest: Path, timeout: int = 300) -> Path:
    src, dest = Path(src), Path(dest)
    fmt = src.suffix.lower().lstrip(".")
    target = target_format(fmt)
    if target is None:
        raise ConversionError(f"nothing to convert: {fmt or 'unknown'} is already usable")
    if fmt == "txt":
        return _txt_to_epub(src, dest)
    if fmt == "djvu":
        return _djvu_to_pdf(src, dest, timeout)
    return _mobi_to_epub(src, dest)
```

- [ ] **Step 5: Add `convert_book` to `pipeline.py`**

```python
def convert_book(cfg, db, store, sha256: str, progress: Progress) -> str:
    """Derive a readable EPUB (or PDF for DjVu). Additive - the original stays."""
    row = _row_for(db, sha256)
    book = store.load(sha256, row["path"]) or Book(sha256=sha256)
    source = next((f for f in book.files if f.role == "original"), None)
    source_path = Path(source.path if source else row["path"])
    fmt = (source.format if source else row["format"]) or source_path.suffix.lstrip(".")

    target = converters.target_format(fmt)
    if target is None:
        raise converters.ConversionError(f"nothing to convert: {fmt} is already usable")

    progress(0, 1, f"converting {source_path.name}")
    dest = Path(cfg.library.root) / cfg.convert.dir / f"{sha256}.{target}"
    converters.convert(source_path, dest, timeout=cfg.convert.timeout)

    derived = FileEntry(
        path=str(dest),
        format=target,
        size=dest.stat().st_size,
        mtime=int(dest.stat().st_mtime),
        role="converted",
        sha256=sha256_file(dest),
    )

    def mutate(b: Book) -> None:
        b.files = [f for f in b.files if f.role != "converted"]
        b.files.append(derived)

    book = store.update(sha256, mutate, row["path"])
    index.sync(db.conn, book)
    ensure_cover(Path(cfg.library.root) / "covers", sha256, dest)
    progress(1, 1, str(dest))
    return str(dest)
```

Add `from reshelf.convert import converters` to `pipeline.py`.

- [ ] **Step 6: Remove the Calibre converter**

```bash
git rm src/reshelf/calibre/convert.py
grep -rn 'calibre.convert\|convert_to_epub\|KINDLE_FORMATS' src/ tests/
```
Expected: no matches. Remove any leftover assertions in `tests/test_calibre.py` that covered `convert.py`; the `calibre-export` tests stay.

For `test_djvu_becomes_a_pdf`, either drop in a small `.djvu` fixture under `tests/fixtures/` and replace the `pytest.skip` with a real assertion, or leave the skip in place — the format is 3 files and `ddjvu` is exercised by `test_djvu_without_ddjvu_raises_a_clear_error`.

- [ ] **Step 7: Run the whole suite and commit**

```bash
.venv/bin/pytest -q
git add -A src/reshelf tests pyproject.toml
git commit -m "feat(convert): MOBI/AZW3/TXT to EPUB and DjVu to PDF, replacing Calibre"
```

---

### Task 12: Write-back tiers 2 and 3

**Files:**
- Create: `src/reshelf/writeback.py`
- Test: `tests/test_writeback.py`

**Interfaces:**
- Consumes: `rewrite_metadata` (Task 10), `dest_for` from `reshelf.planner.committer`, `SidecarStore`.
- Produces:
  - `EMBEDDABLE: frozenset[str]` — `{"epub", "pdf"}`
  - `UnsupportedWriteBack(Exception)` with `.reason: str` (`"convert_first"` or `"unwritable"`)
  - `embed_metadata(path: Path, metadata: BookMetadata) -> None`
  - `rename_library_copy(cfg, db, store, sha256: str) -> str | None` — the new path, or `None` if the book is not committed

- [ ] **Step 1: Write the failing tests**

Create `tests/test_writeback.py`:

```python
import pytest

from reshelf.store.models import BookMetadata
from reshelf.writeback import UnsupportedWriteBack, embed_metadata
from tests.helpers import make_epub, make_pdf


def test_embed_into_epub(tmp_path):
    src = make_epub(tmp_path / "b.epub", "Old", "Old Author")
    embed_metadata(src, BookMetadata(title="New", authors=["New Author"]))
    from reshelf.extractors.epub import extract_epub

    meta = extract_epub(src)
    assert meta.title == "New"
    assert meta.authors == ["New Author"]


def test_embed_into_pdf(tmp_path):
    src = make_pdf(tmp_path / "b.pdf", "Old", "Old Author")
    embed_metadata(src, BookMetadata(title="New", authors=["New Author"]))
    import pymupdf as fitz

    with fitz.open(str(src)) as doc:
        assert doc.metadata["title"] == "New"
        assert doc.metadata["author"] == "New Author"


@pytest.mark.parametrize("suffix", [".mobi", ".azw3", ".djvu", ".txt"])
def test_unconverted_formats_report_convert_first(tmp_path, suffix):
    src = tmp_path / f"b{suffix}"
    src.write_bytes(b"x")
    with pytest.raises(UnsupportedWriteBack) as exc:
        embed_metadata(src, BookMetadata(title="New"))
    assert exc.value.reason == "convert_first"


def test_embedding_leaves_the_pdf_readable(tmp_path):
    src = make_pdf(tmp_path / "b.pdf", "Old", "Old Author", text="body text")
    embed_metadata(src, BookMetadata(title="New", authors=["A"]))
    import pymupdf as fitz

    with fitz.open(str(src)) as doc:
        assert "body text" in doc.load_page(0).get_text()
```

Add a test for tier 2 in the same file:

```python
def test_rename_library_copy_moves_the_file_and_updates_the_sidecar(tmp_path):
    from reshelf.config import default_config
    from reshelf.db.database import Database
    from reshelf.store.models import Book, FileEntry
    from reshelf.store.sidecar import SidecarStore
    from reshelf.writeback import rename_library_copy

    cfg = default_config(tmp_path)
    library = tmp_path / "library" / "Old Author"
    library.mkdir(parents=True)
    old = library / "Old Title.epub"
    make_epub(old, "Old Title", "Old Author")

    db = Database(cfg.database.path)
    db.init_schema()
    db.conn.execute(
        "INSERT INTO files (path, sha256, format, status)"
        " VALUES (?, ?, 'epub', 'COMMITTED')",
        (str(old), "a" * 64),
    )
    db.conn.commit()

    store = SidecarStore(cfg)
    book = Book(sha256="a" * 64, files=[FileEntry(path=str(old), format="epub")])
    book.metadata.title = "New Title"
    book.metadata.authors = ["New Author"]
    store.save(book)

    new_path = rename_library_copy(cfg, db, store, "a" * 64)
    assert new_path is not None
    assert "New Title" in new_path
    from pathlib import Path

    assert Path(new_path).exists()
    assert not old.exists()
    assert any(f.path == new_path for f in store.load("a" * 64).files)
    db.close()


def test_rename_is_a_no_op_for_an_uncommitted_book(tmp_path):
    from reshelf.config import default_config
    from reshelf.db.database import Database
    from reshelf.store.models import Book, FileEntry
    from reshelf.store.sidecar import SidecarStore
    from reshelf.writeback import rename_library_copy

    cfg = default_config(tmp_path)
    cfg.library.incoming.mkdir(parents=True)
    src = cfg.library.incoming / "x.epub"
    make_epub(src, "T", "A")
    db = Database(cfg.database.path)
    db.init_schema()
    db.conn.execute(
        "INSERT INTO files (path, sha256, format, status)"
        " VALUES (?, ?, 'epub', 'MATCHED')",
        (str(src), "a" * 64),
    )
    db.conn.commit()
    store = SidecarStore(cfg)
    store.save(Book(sha256="a" * 64, files=[FileEntry(path=str(src), format="epub")]))

    assert rename_library_copy(cfg, db, store, "a" * 64) is None
    assert src.exists()
    db.close()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_writeback.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'reshelf.writeback'`.

- [ ] **Step 3: Write the implementation**

Create `src/reshelf/writeback.py`:

```python
"""Optional write-back beyond the sidecar.

Tier 2 renames the committed copy under library/ to follow corrected
metadata, journaled so `rollback` undoes it. Tier 3 writes metadata into
the file itself - only ever a library copy or a derived file, never an
original under incoming/.
"""

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pymupdf as fitz

from reshelf.convert.epub_writer import UnsupportedEpub, rewrite_metadata
from reshelf.planner.committer import dest_for
from reshelf.store import index
from reshelf.store.models import Book, BookMetadata

EMBEDDABLE = frozenset({"epub", "pdf"})


class UnsupportedWriteBack(Exception):
    def __init__(self, message: str, reason: str):
        super().__init__(message)
        self.reason = reason


def embed_metadata(path: Path, metadata: BookMetadata) -> None:
    path = Path(path)
    fmt = path.suffix.lower().lstrip(".")
    if fmt not in EMBEDDABLE:
        raise UnsupportedWriteBack(
            f"cannot write metadata into a {fmt} file; convert it to EPUB first",
            reason="convert_first",
        )
    if fmt == "epub":
        try:
            rewrite_metadata(path, metadata)
        except UnsupportedEpub as e:
            raise UnsupportedWriteBack(str(e), reason="unwritable") from e
        return
    with fitz.open(str(path)) as doc:
        info = dict(doc.metadata or {})
        info["title"] = metadata.title or ""
        info["author"] = "; ".join(metadata.authors)
        if metadata.publisher:
            info["producer"] = metadata.publisher
        doc.set_metadata(info)
        doc.saveIncr() if doc.can_save_incrementally() else doc.save(
            str(path), incremental=False, deflate=True
        )


def _journal(cfg, entries: list[dict]) -> str:
    """Same shape apply_plan writes, so rollback_journal can undo it."""
    now = datetime.now(timezone.utc)
    journal_id = now.strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:8]
    reports = Path(cfg.library.root) / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / f"journal-{journal_id}.json").write_text(
        json.dumps(
            {"commit_id": journal_id, "created_at": now.isoformat(), "done": entries},
            ensure_ascii=False,
            indent=2,
        )
    )
    return journal_id


def rename_library_copy(cfg, db, store, sha256: str) -> str | None:
    """Re-derive the library path from current metadata and move the copy there."""
    row = db.conn.execute(
        "SELECT * FROM files WHERE sha256 = ? AND status = 'COMMITTED' ORDER BY id"
        " LIMIT 1",
        (sha256,),
    ).fetchone()
    if row is None:
        return None
    book = store.load(sha256, row["path"])
    if book is None:
        return None

    old = Path(row["path"])
    if not old.exists():
        return None
    m = book.metadata
    action = {
        "file": str(old),
        "metadata_changes": {
            "title": m.title,
            "author": "; ".join(m.authors) or None,
            "isbn13": m.isbn13,
        },
    }
    new = dest_for(action, Path(cfg.library.root) / "library").with_suffix(old.suffix)
    if new == old:
        return str(old)
    new.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(old), str(new))

    db.conn.execute(
        "UPDATE files SET path = ? WHERE id = ?", (str(new), row["id"])
    )
    db.conn.commit()

    def mutate(b: Book) -> None:
        for entry in b.files:
            if entry.path == str(old):
                entry.path = str(new)

    book = store.update(sha256, mutate, str(new))
    index.sync(db.conn, book)
    _journal(cfg, [{"action": "import", "src": str(old), "dest": str(new),
                    "moved": True}])
    return str(new)
```

`dest_for`'s exact signature is in `src/reshelf/planner/committer.py` — read it and match the call. If it already applies the source suffix, drop the `.with_suffix(...)`.

`rollback_journal` must already handle `{"action": "import", "moved": True}` from Task 8 Step 4; verify with a manual rollback of a journal this function writes.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_writeback.py -v`
Expected: PASS (8 tests).

- [ ] **Step 5: Run the whole suite and commit**

```bash
.venv/bin/pytest -q
git add src/reshelf/writeback.py tests/test_writeback.py
git commit -m "feat(writeback): journaled library rename and EPUB/PDF metadata embedding"
```

---
# Phase 2 — Web backend

### Task 13: Single-slot job runner

One worker thread is the lock — there is no other concurrency control, and none is wanted for a single-user box. This task also makes `Database` usable from more than one thread, because the FastAPI request threads and the worker share one connection (the exclusive lock file means there can only be one).

**Files:**
- Create: `src/reshelf/web/__init__.py` (empty)
- Create: `src/reshelf/web/jobs.py`
- Modify: `src/reshelf/db/database.py:139` (`check_same_thread=False` + a write lock)
- Test: `tests/test_jobs.py`

**Interfaces:**
- Consumes: the `jobs` table (Task 2), `Progress` and `JobCancelled` (Task 6).
- Produces:
  - `COMMANDS: dict[str, Callable[[Config, Database, SidecarStore, dict, Progress], object]]`
  - `JobRunner(cfg, db, store)` with `start()`, `stop()`, `enqueue(command: str, args: dict) -> int`, `get(job_id: int) -> dict | None`, `recent(limit: int = 50) -> list[dict]`, `cancel(job_id: int) -> bool`, `events(job_id: int) -> Iterator[dict]`
  - `UnknownCommand(ValueError)`
  - `Database.lock: threading.RLock`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_jobs.py`:

```python
import time

import pytest

from reshelf.config import default_config
from reshelf.db.database import Database
from reshelf.pipeline import JobCancelled
from reshelf.store.sidecar import SidecarStore
from reshelf.web.jobs import COMMANDS, JobRunner, UnknownCommand


@pytest.fixture
def runner(tmp_path):
    cfg = default_config(tmp_path)
    db = Database(cfg.database.path)
    db.init_schema()
    r = JobRunner(cfg, db, SidecarStore(cfg))
    r.start()
    yield r
    r.stop()
    db.close()


def wait_for(runner, job_id, status, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = runner.get(job_id)
        if job and job["status"] == status:
            return job
        time.sleep(0.02)
    raise AssertionError(f"job {job_id} never reached {status}: {runner.get(job_id)}")


def test_a_job_runs_and_reports_done(runner, monkeypatch):
    monkeypatch.setitem(
        COMMANDS, "noop", lambda cfg, db, store, args, progress: {"ok": args["n"]}
    )
    job_id = runner.enqueue("noop", {"n": 7})
    job = wait_for(runner, job_id, "done")
    assert '"ok": 7' in job["message"] or "7" in job["message"]


def test_an_unknown_command_is_refused_at_enqueue_time(runner):
    with pytest.raises(UnknownCommand):
        runner.enqueue("rm -rf /", {})


def test_a_failing_job_records_the_error_and_does_not_kill_the_worker(
    runner, monkeypatch
):
    def boom(cfg, db, store, args, progress):
        raise RuntimeError("kaboom")

    monkeypatch.setitem(COMMANDS, "boom", boom)
    monkeypatch.setitem(COMMANDS, "fine", lambda *a: "ok")

    failed = runner.enqueue("boom", {})
    job = wait_for(runner, failed, "failed")
    assert "kaboom" in job["error"]

    after = runner.enqueue("fine", {})
    wait_for(runner, after, "done")


def test_progress_updates_are_recorded(runner, monkeypatch):
    def counting(cfg, db, store, args, progress):
        for n in range(1, 4):
            progress(n, 3, f"step {n}")
        return "done"

    monkeypatch.setitem(COMMANDS, "counting", counting)
    job_id = runner.enqueue("counting", {})
    job = wait_for(runner, job_id, "done")
    assert job["progress"] == 3
    assert job["total"] == 3
    assert "step 3" in job["log"]


def test_cancel_stops_a_running_job(runner, monkeypatch):
    def slow(cfg, db, store, args, progress):
        for n in range(200):
            progress(n, 200, "working")
            time.sleep(0.01)

    monkeypatch.setitem(COMMANDS, "slow", slow)
    job_id = runner.enqueue("slow", {})
    deadline = time.monotonic() + 3
    while runner.get(job_id)["status"] != "running" and time.monotonic() < deadline:
        time.sleep(0.01)
    assert runner.cancel(job_id) is True
    wait_for(runner, job_id, "cancelled")


def test_only_one_job_runs_at_a_time(runner, monkeypatch):
    concurrent = []
    running = []

    def tracked(cfg, db, store, args, progress):
        running.append(1)
        concurrent.append(len(running))
        time.sleep(0.05)
        running.pop()

    monkeypatch.setitem(COMMANDS, "tracked", tracked)
    ids = [runner.enqueue("tracked", {}) for _ in range(3)]
    for job_id in ids:
        wait_for(runner, job_id, "done")
    assert max(concurrent) == 1


def test_stale_jobs_are_marked_interrupted_on_start(tmp_path):
    cfg = default_config(tmp_path)
    db = Database(cfg.database.path)
    db.init_schema()
    db.conn.execute(
        "INSERT INTO jobs (command, status, created_at) VALUES ('scan','running','x')"
    )
    db.conn.execute(
        "INSERT INTO jobs (command, status, created_at) VALUES ('match','queued','x')"
    )
    db.conn.commit()

    r = JobRunner(cfg, db, SidecarStore(cfg))
    r.start()
    try:
        statuses = {j["command"]: j["status"] for j in r.recent()}
        assert statuses == {"scan": "interrupted", "match": "interrupted"}
    finally:
        r.stop()
        db.close()


def test_events_streams_progress_then_a_terminal_event(runner, monkeypatch):
    def two_steps(cfg, db, store, args, progress):
        progress(1, 2, "half")
        progress(2, 2, "all")

    monkeypatch.setitem(COMMANDS, "two", two_steps)
    job_id = runner.enqueue("two", {})
    seen = []
    for event in runner.events(job_id):
        seen.append(event)
        if event["status"] in ("done", "failed", "cancelled"):
            break
    assert seen[-1]["status"] == "done"


def test_the_real_pipeline_commands_are_registered():
    assert {
        "scan", "extract", "match", "resolve", "plan", "commit", "rollback",
        "reindex", "rematch", "convert",
    } <= set(COMMANDS)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_jobs.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'reshelf.web'`.

- [ ] **Step 3: Make `Database` thread-shareable**

In `src/reshelf/db/database.py`, add `import threading`, then in `__init__`:

```python
        self.conn = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        # One process holds the lock file, but FastAPI request threads and the
        # job worker share this connection. SQLite serialises statements; the
        # lock keeps multi-statement sequences from interleaving.
        self.lock = threading.RLock()
```

- [ ] **Step 4: Write the job runner**

Create `src/reshelf/web/__init__.py` (empty) and `src/reshelf/web/jobs.py`:

```python
"""One worker thread, one job at a time.

# ponytail: the single worker IS the lock. A real queue (RQ/Celery) only
# if concurrent pipeline stages are ever wanted; a single-user box does
# not want them.
"""

import json
import queue
import threading
import traceback
from collections.abc import Callable, Iterator
from datetime import datetime, timezone
from pathlib import Path

from reshelf import pipeline
from reshelf.store.bootstrap import reindex as _reindex

TERMINAL = ("done", "failed", "cancelled", "interrupted")


class UnknownCommand(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _latest_plan(cfg) -> Path:
    reports = Path(cfg.library.root) / "reports"
    plans = sorted(reports.glob("plan-*.json"))
    if not plans:
        raise RuntimeError("no plan found; run plan first")
    return plans[-1]


COMMANDS: dict[str, Callable] = {
    "scan": lambda cfg, db, store, args, progress: pipeline.scan(
        cfg, db, store, Path(args.get("path") or cfg.library.incoming), progress
    ),
    "extract": lambda cfg, db, store, args, progress: pipeline.extract(
        cfg, db, store, bool(args.get("force")), progress
    ),
    "match": lambda cfg, db, store, args, progress: pipeline.match(
        cfg, db, store, bool(args.get("offline")), progress
    ),
    "resolve": lambda cfg, db, store, args, progress: pipeline.resolve(
        cfg, db, store, int(args.get("limit") or 0),
        bool(args.get("include_unresolved")), progress
    ),
    "plan": lambda cfg, db, store, args, progress: str(
        pipeline.plan(cfg, db, store)
    ),
    "commit": lambda cfg, db, store, args, progress: pipeline.commit(
        cfg, db, store, Path(args["plan"]) if args.get("plan") else _latest_plan(cfg),
        progress,
        dry_run=bool(args.get("dry_run")),
        do_quarantine=bool(args.get("quarantine")),
        do_duplicates=bool(args.get("duplicates")),
    ),
    "rollback": lambda cfg, db, store, args, progress: pipeline.rollback(
        cfg, db, args["journal_id"], progress
    ),
    "reindex": lambda cfg, db, store, args, progress: _reindex(db, store, progress),
    "rematch": lambda cfg, db, store, args, progress: [
        c.model_dump() if hasattr(c, "model_dump") else vars(c)
        for c in pipeline.match_one(
            cfg, db, store, args["sha256"],
            query=args.get("query"), use_ai=bool(args.get("ai")),
        )
    ],
    "convert": lambda cfg, db, store, args, progress: pipeline.convert_book(
        cfg, db, store, args["sha256"], progress
    ),
}


class JobRunner:
    def __init__(self, cfg, db, store):
        self.cfg, self.db, self.store = cfg, db, store
        self._queue: queue.Queue[int] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._stopping = threading.Event()
        self._cancels: dict[int, threading.Event] = {}
        self._subscribers: dict[int, list[queue.Queue]] = {}
        self._subs_guard = threading.Lock()

    # -- lifecycle ---------------------------------------------------

    def start(self) -> None:
        with self.db.lock:
            self.db.conn.execute(
                "UPDATE jobs SET status='interrupted', finished_at=?"
                " WHERE status IN ('queued','running')",
                (_now(),),
            )
            self.db.conn.commit()
        self._thread = threading.Thread(target=self._work, daemon=True, name="jobs")
        self._thread.start()

    def stop(self) -> None:
        self._stopping.set()
        self._queue.put(-1)
        if self._thread is not None:
            self._thread.join(timeout=5)

    # -- api ---------------------------------------------------------

    def enqueue(self, command: str, args: dict | None = None) -> int:
        if command not in COMMANDS:
            raise UnknownCommand(command)
        with self.db.lock:
            cur = self.db.conn.execute(
                "INSERT INTO jobs (command, args_json, status, created_at)"
                " VALUES (?,?,'queued',?)",
                (command, json.dumps(args or {}), _now()),
            )
            self.db.conn.commit()
            job_id = cur.lastrowid
        self._queue.put(job_id)
        return job_id

    def get(self, job_id: int) -> dict | None:
        with self.db.lock:
            row = self.db.conn.execute(
                "SELECT * FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
        return dict(row) if row else None

    def recent(self, limit: int = 50) -> list[dict]:
        with self.db.lock:
            rows = self.db.conn.execute(
                "SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def cancel(self, job_id: int) -> bool:
        event = self._cancels.get(job_id)
        if event is None:
            with self.db.lock:
                changed = self.db.conn.execute(
                    "UPDATE jobs SET status='cancelled', finished_at=?"
                    " WHERE id=? AND status='queued'",
                    (_now(), job_id),
                ).rowcount
                self.db.conn.commit()
            if changed:
                self._publish(job_id)
            return bool(changed)
        event.set()
        return True

    def events(self, job_id: int) -> Iterator[dict]:
        q: queue.Queue = queue.Queue()
        with self._subs_guard:
            self._subscribers.setdefault(job_id, []).append(q)
        try:
            snapshot = self.get(job_id)
            if snapshot:
                yield snapshot
                if snapshot["status"] in TERMINAL:
                    return
            while True:
                event = q.get()
                yield event
                if event["status"] in TERMINAL:
                    return
        finally:
            with self._subs_guard:
                subs = self._subscribers.get(job_id, [])
                if q in subs:
                    subs.remove(q)

    # -- internals ---------------------------------------------------

    def _publish(self, job_id: int) -> None:
        snapshot = self.get(job_id)
        if snapshot is None:
            return
        with self._subs_guard:
            for q in list(self._subscribers.get(job_id, [])):
                q.put(snapshot)

    def _progress_for(self, job_id: int, cancel: threading.Event):
        def progress(done: int, total: int | None, message: str) -> None:
            if cancel.is_set():
                raise pipeline.JobCancelled()
            with self.db.lock:
                self.db.conn.execute(
                    "UPDATE jobs SET progress=?, total=?, message=?,"
                    " log = substr(COALESCE(log,'') || ? , -20000)"
                    " WHERE id=?",
                    (done, total, message, f"{message}\n", job_id),
                )
                self.db.conn.commit()
            self._publish(job_id)

        return progress

    def _finish(self, job_id: int, status: str, message: str = "", error: str = ""):
        with self.db.lock:
            self.db.conn.execute(
                "UPDATE jobs SET status=?, message=?, error=?, finished_at=?"
                " WHERE id=?",
                (status, message, error, _now(), job_id),
            )
            self.db.conn.commit()
        self._publish(job_id)

    def _work(self) -> None:
        while not self._stopping.is_set():
            job_id = self._queue.get()
            if job_id == -1:
                return
            job = self.get(job_id)
            if job is None or job["status"] != "queued":
                continue

            cancel = threading.Event()
            self._cancels[job_id] = cancel
            with self.db.lock:
                self.db.conn.execute(
                    "UPDATE jobs SET status='running', started_at=? WHERE id=?",
                    (_now(), job_id),
                )
                self.db.conn.commit()
            self._publish(job_id)

            try:
                result = COMMANDS[job["command"]](
                    self.cfg,
                    self.db,
                    self.store,
                    json.loads(job["args_json"] or "{}"),
                    self._progress_for(job_id, cancel),
                )
                self._finish(job_id, "done", json.dumps(result, default=str)[:4000])
            except pipeline.JobCancelled:
                self._finish(job_id, "cancelled")
            except Exception as e:
                self._finish(
                    job_id, "failed", error=f"{e}\n{traceback.format_exc()[-2000:]}"
                )
            finally:
                self._cancels.pop(job_id, None)
```

- [ ] **Step 4a: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_jobs.py -v`
Expected: PASS (9 tests).

- [ ] **Step 5: Run the whole suite and commit**

```bash
.venv/bin/pytest -q
git add src/reshelf/web src/reshelf/db/database.py tests/test_jobs.py
git commit -m "feat(web): single-slot job runner over the pipeline stages"
```

---

### Task 14: FastAPI app, shared state, `serve`, and the meta routes

**Files:**
- Create: `src/reshelf/web/deps.py`
- Create: `src/reshelf/web/app.py`
- Create: `src/reshelf/web/schemas.py`
- Create: `src/reshelf/web/api/__init__.py` (empty)
- Create: `src/reshelf/web/api/meta.py`
- Modify: `src/reshelf/cli.py` (add `serve`)
- Test: `tests/test_api_meta.py`

**Interfaces:**
- Consumes: `Config`, `Database`, `SidecarStore`, `JobRunner`, `converters.available`.
- Produces:
  - `AppState(cfg, db, store, runner)` and `create_app(root: Path) -> FastAPI`
  - `get_state(request) -> AppState` (FastAPI dependency)
  - Routes: `GET /api/capabilities`, `GET /api/stats`, `GET /api/settings`, `PUT /api/settings`
  - `SETTABLE: dict[str, type]` — the config keys the UI may change

- [ ] **Step 1: Write the failing tests**

Create `tests/test_api_meta.py`:

```python
import pytest
from fastapi.testclient import TestClient

from reshelf.config import default_config, save_config
from reshelf.web.app import create_app


@pytest.fixture
def client(tmp_path):
    cfg = default_config(tmp_path)
    for sub in ("incoming", "library", "db", "metadata", "derived", "reports", "covers"):
        (tmp_path / sub).mkdir(parents=True, exist_ok=True)
    save_config(cfg, tmp_path)
    app = create_app(tmp_path)
    with TestClient(app) as c:
        yield c


def test_capabilities_reports_ai_off_by_default(client):
    body = client.get("/api/capabilities").json()
    assert body["ai"] is False
    assert body["ai_provider"] is None


def test_capabilities_lists_embeddable_and_convertible_formats(client):
    body = client.get("/api/capabilities").json()
    assert set(body["embeddable"]) == {"epub", "pdf"}
    assert body["convert"]["txt"] == "epub"
    assert body["convert"]["azw3"] == "epub"
    assert body["convert"]["djvu"] == "pdf"
    assert "djvu" in body["converters_available"]


def test_capabilities_reports_commit_mode(client):
    assert client.get("/api/capabilities").json()["commit_mode"] == "copy"


def test_stats_returns_status_counts(client):
    body = client.get("/api/stats").json()
    assert body["total"] == 0
    assert isinstance(body["by_status"], dict)


def test_settings_round_trip(client):
    client.put("/api/settings", json={"library.commit_mode": "move"})
    assert client.get("/api/settings").json()["library.commit_mode"] == "move"
    assert client.get("/api/capabilities").json()["commit_mode"] == "move"


def test_settings_rejects_a_key_that_is_not_settable(client):
    r = client.put("/api/settings", json={"database.path": "/etc/passwd"})
    assert r.status_code == 422


def test_settings_rejects_an_invalid_value(client):
    r = client.put("/api/settings", json={"library.commit_mode": "teleport"})
    assert r.status_code == 422


def test_settings_persist_to_config_yaml(client, tmp_path):
    import yaml

    client.put("/api/settings", json={"web.port": 9999})
    saved = yaml.safe_load((tmp_path / "config.yaml").read_text())
    assert saved["web"]["port"] == 9999


def test_setting_an_ai_provider_turns_the_capability_on(client):
    client.put("/api/settings", json={"ai.provider": "claude-cli"})
    assert client.get("/api/capabilities").json()["ai"] is True
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_api_meta.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'reshelf.web.app'`.

- [ ] **Step 3: Write the shared state**

Create `src/reshelf/web/deps.py`:

```python
"""One Database, one SidecarStore, one JobRunner for the process.

Database takes an exclusive lock file, so the server owns exactly one and
shares it with the worker thread. A second reshelf process (including a
CLI command) will refuse to start while the server runs - that is the
intended one-writer guarantee.
"""

from dataclasses import dataclass
from pathlib import Path

from fastapi import Request

from reshelf.config import Config, load_config, save_config
from reshelf.db.database import Database
from reshelf.store.sidecar import SidecarStore
from reshelf.web.jobs import JobRunner


@dataclass
class AppState:
    root: Path
    cfg: Config
    db: Database
    store: SidecarStore
    runner: JobRunner

    def reload_config(self, cfg: Config) -> None:
        self.cfg = cfg
        self.store.cfg = cfg
        self.runner.cfg = cfg
        save_config(cfg, self.root)


def build_state(root: Path) -> AppState:
    root = Path(root).resolve()
    cfg = load_config(root)
    db = Database(cfg.database.path)
    db.init_schema()
    store = SidecarStore(cfg)
    runner = JobRunner(cfg, db, store)
    return AppState(root=root, cfg=cfg, db=db, store=store, runner=runner)


def get_state(request: Request) -> AppState:
    return request.app.state.reshelf
```

- [ ] **Step 4: Write the app factory**

Create `src/reshelf/web/app.py`:

```python
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from reshelf.web.api import meta
from reshelf.web.deps import build_state

SPA_DIR = Path(__file__).parent / "static"


def create_app(root: Path) -> FastAPI:
    state = build_state(root)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        state.runner.start()
        yield
        state.runner.stop()
        state.db.close()

    app = FastAPI(title="reshelf", lifespan=lifespan)
    app.state.reshelf = state
    app.include_router(meta.router, prefix="/api")
    if SPA_DIR.exists():
        # html=True so client-side routes fall back to index.html.
        app.mount("/", StaticFiles(directory=SPA_DIR, html=True), name="spa")
    return app
```

- [ ] **Step 5: Write the meta routes**

Create `src/reshelf/web/api/__init__.py` (empty) and `src/reshelf/web/api/meta.py`:

```python
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import ValidationError

from reshelf.config import Config
from reshelf.convert import converters
from reshelf.web.deps import AppState, get_state
from reshelf.writeback import EMBEDDABLE

router = APIRouter(tags=["meta"])

# Only these may be changed from the UI. Everything else needs config.yaml.
SETTABLE = (
    "library.commit_mode",
    "metadata.layout",
    "web.host",
    "web.port",
    "write_back.library_file",
    "write_back.embed",
    "ai.provider",
    "ai.model",
    "ai.api_key",
    "ai.base_url",
    "matching.auto_accept",
    "matching.review_below",
    "convert.timeout",
)


@router.get("/capabilities")
def capabilities(state: AppState = Depends(get_state)) -> dict:
    return {
        "ai": state.cfg.ai.enabled,
        "ai_provider": state.cfg.ai.provider,
        "commit_mode": state.cfg.library.commit_mode,
        "embeddable": sorted(EMBEDDABLE),
        "convert": dict(converters.TARGETS),
        "converters_available": converters.available(),
        "metadata_layout": state.cfg.metadata.layout,
    }


@router.get("/stats")
def stats(state: AppState = Depends(get_state)) -> dict:
    with state.db.lock:
        rows = state.db.conn.execute(
            "SELECT status, COUNT(*) AS n FROM files GROUP BY status"
        ).fetchall()
        total = state.db.conn.execute(
            "SELECT COUNT(DISTINCT sha256) FROM files WHERE sha256 IS NOT NULL"
        ).fetchone()[0]
    return {"total": total, "by_status": {r["status"]: r["n"] for r in rows}}


def _get(obj: Any, dotted: str) -> Any:
    for part in dotted.split("."):
        obj = getattr(obj, part)
    return obj


@router.get("/settings")
def get_settings(state: AppState = Depends(get_state)) -> dict:
    out = {}
    for key in SETTABLE:
        value = _get(state.cfg, key)
        out[key] = str(value) if hasattr(value, "__fspath__") else value
    return out


@router.put("/settings")
def put_settings(
    payload: dict[str, Any], state: AppState = Depends(get_state)
) -> dict:
    unknown = set(payload) - set(SETTABLE)
    if unknown:
        raise HTTPException(422, f"not settable: {sorted(unknown)}")
    data = state.cfg.model_dump(mode="json")
    for key, value in payload.items():
        section, field = key.split(".", 1)
        data[section][field] = value
    try:
        new_cfg = Config.model_validate(data)
    except ValidationError as e:
        raise HTTPException(422, e.errors(include_url=False)) from e
    state.reload_config(new_cfg)
    return get_settings(state)
```

- [ ] **Step 6: Add the `serve` command**

In `src/reshelf/cli.py`:

```python
@app.command()
def serve(
    root: Path = ROOT_OPTION,
    host: Optional[str] = typer.Option(None, "--host"),
    port: Optional[int] = typer.Option(None, "--port"),
) -> None:
    """Run the web app."""
    import uvicorn

    from reshelf.web.app import create_app

    cfg = load_config(root)
    uvicorn.run(
        create_app(root),
        host=host or cfg.web.host,
        port=port or cfg.web.port,
    )
```

- [ ] **Step 7: Run the tests and commit**

```bash
.venv/bin/pytest tests/test_api_meta.py -v
.venv/bin/pytest -q
git add src/reshelf/web src/reshelf/cli.py tests/test_api_meta.py
git commit -m "feat(web): FastAPI app, shared state, serve command, capabilities/stats/settings"
```

---
### Task 15: Books list and detail

**Files:**
- Create: `src/reshelf/web/api/books.py`
- Modify: `src/reshelf/web/schemas.py`
- Modify: `src/reshelf/web/app.py` (include the router)
- Test: `tests/test_api_books.py`

**Interfaces:**
- Consumes: `index.query` (Task 4), `SidecarStore`.
- Produces: `GET /api/books`, `GET /api/books/{sha}`, `GET /api/tags`; schemas `BookListItem`, `BookList`, `BookDetail`.
- `BookDetail` carries `sidecar` (the whole document), `status`, `paths` (every `files` row for that hash), `candidates` (from the last match) and `has_cover`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_api_books.py`:

```python
import pytest
from fastapi.testclient import TestClient

from reshelf.config import default_config, save_config
from reshelf.db.database import Database
from reshelf.store import index
from reshelf.store.models import Book, FileEntry
from reshelf.store.sidecar import SidecarStore
from reshelf.web.app import create_app

SHA = "a" * 64


@pytest.fixture
def client(tmp_path):
    cfg = default_config(tmp_path)
    for sub in ("incoming", "library", "db", "metadata", "derived", "reports", "covers"):
        (tmp_path / sub).mkdir(parents=True, exist_ok=True)
    save_config(cfg, tmp_path)

    db = Database(cfg.database.path)
    db.init_schema()
    store = SidecarStore(cfg)
    for n, (sha, title, status) in enumerate(
        [(SHA, "Dune", "MATCHED"), ("b" * 64, "Neuromancer", "UNRESOLVED")]
    ):
        book = Book(
            sha256=sha,
            files=[FileEntry(path=f"incoming/{title}.epub", format="epub")],
        )
        book.metadata.title = title
        book.metadata.authors = ["Frank Herbert" if n == 0 else "William Gibson"]
        book.metadata.tags = ["sci-fi"]
        store.save(book)
        db.conn.execute(
            "INSERT INTO files (path, sha256, format, status)"
            " VALUES (?,?,'epub',?)",
            (f"incoming/{title}.epub", sha, status),
        )
        index.sync(db.conn, book)
    db.conn.commit()
    db.close()

    with TestClient(create_app(tmp_path)) as c:
        yield c


def test_list_returns_both_books(client):
    body = client.get("/api/books").json()
    assert body["total"] == 2
    assert {b["title"] for b in body["items"]} == {"Dune", "Neuromancer"}


def test_list_includes_pipeline_status(client):
    body = client.get("/api/books").json()
    statuses = {b["title"]: b["status"] for b in body["items"]}
    assert statuses["Neuromancer"] == "UNRESOLVED"


def test_search(client):
    assert client.get("/api/books", params={"q": "Gibson"}).json()["total"] == 1


def test_filter_by_status(client):
    body = client.get("/api/books", params={"status": "MATCHED"}).json()
    assert body["total"] == 1
    assert body["items"][0]["title"] == "Dune"


def test_paging(client):
    body = client.get("/api/books", params={"page_size": 1, "page": 2}).json()
    assert body["total"] == 2
    assert len(body["items"]) == 1


def test_an_invalid_sort_is_rejected(client):
    assert client.get("/api/books", params={"sort": "nope"}).status_code == 422


def test_detail_returns_the_whole_sidecar(client):
    body = client.get(f"/api/books/{SHA}").json()
    assert body["sidecar"]["metadata"]["title"] == "Dune"
    assert body["sidecar"]["schema"] == 1
    assert body["status"] == "MATCHED"
    assert body["paths"] == ["incoming/Dune.epub"]


def test_detail_for_an_unknown_book_is_404(client):
    assert client.get("/api/books/" + "f" * 64).status_code == 404


def test_tags_endpoint(client):
    assert client.get("/api/tags").json() == ["sci-fi"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_api_books.py -v`
Expected: FAIL with 404 on `/api/books`.

- [ ] **Step 3: Write the schemas**

Create `src/reshelf/web/schemas.py`:

```python
from typing import Any, Literal

from pydantic import BaseModel

from reshelf.store.models import BookMetadata


class BookListItem(BaseModel):
    sha256: str
    title: str | None = None
    authors: str | None = None
    series: str | None = None
    pubdate: str | None = None
    primary_format: str | None = None
    status: str | None = None
    resolver: str | None = None
    confidence: float | None = None
    has_cover: bool = False


class BookList(BaseModel):
    items: list[BookListItem]
    total: int
    page: int
    page_size: int


class BookDetail(BaseModel):
    sha256: str
    sidecar: dict[str, Any]
    status: str | None = None
    paths: list[str] = []
    candidates: list[dict[str, Any]] = []
    has_cover: bool = False


class WriteBack(BaseModel):
    library_file: bool = False
    embed: bool = False


class MetadataPatch(BaseModel):
    metadata: BookMetadata
    write_back: WriteBack = WriteBack()


class WriteBackResult(BaseModel):
    sidecar: dict[str, Any]
    library_file: str | None = None
    embedded: bool = False
    warnings: list[str] = []


class ChoosePayload(BaseModel):
    candidate_id: int


class RematchPayload(BaseModel):
    query: dict[str, str] | None = None
    ai: bool = False


class JobCreate(BaseModel):
    command: str
    args: dict[str, Any] = {}


class Sort(BaseModel):
    sort: Literal["title", "authors", "pubdate", "updated_at", "added"] = "title"
```

- [ ] **Step 4: Write the router**

Create `src/reshelf/web/api/books.py`:

```python
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from reshelf.covers import cover_path
from reshelf.store import index
from reshelf.web.deps import AppState, get_state
from reshelf.web.schemas import BookDetail, BookList, BookListItem

router = APIRouter(tags=["books"])


@router.get("/books", response_model=BookList)
def list_books(
    q: str | None = None,
    status: str | None = None,
    fmt: str | None = Query(None, alias="format"),
    tag: str | None = None,
    resolver: str | None = None,
    sort: Literal["title", "authors", "pubdate", "updated_at", "added"] = "title",
    order: Literal["asc", "desc"] = "asc",
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    state: AppState = Depends(get_state),
) -> BookList:
    covers = state.root / "covers"
    with state.db.lock:
        rows, total = index.query(
            state.db.conn,
            q=q, status=status, fmt=fmt, tag=tag, resolver=resolver,
            sort=sort, order=order,
            limit=page_size, offset=(page - 1) * page_size,
        )
    items = [
        BookListItem(
            **{k: row.get(k) for k in BookListItem.model_fields if k != "has_cover"},
            has_cover=cover_path(covers, row["sha256"]).exists(),
        )
        for row in rows
    ]
    return BookList(items=items, total=total, page=page, page_size=page_size)


@router.get("/books/{sha256}", response_model=BookDetail)
def get_book(sha256: str, state: AppState = Depends(get_state)) -> BookDetail:
    with state.db.lock:
        rows = state.db.conn.execute(
            "SELECT * FROM files WHERE sha256 = ? ORDER BY id", (sha256,)
        ).fetchall()
    book = state.store.load(sha256, rows[0]["path"] if rows else None)
    if book is None:
        raise HTTPException(404, "no such book")
    with state.db.lock:
        candidates = state.db.conn.execute(
            "SELECT m.edition_id, m.score, m.confidence, m.resolver, m.status,"
            " m.evidence_json, w.canonical_title AS title, e.isbn13, e.publisher,"
            " e.publication_date"
            " FROM matches m JOIN editions e ON e.id = m.edition_id"
            " JOIN works w ON w.id = e.work_id"
            " WHERE m.file_id IN (SELECT id FROM files WHERE sha256 = ?)"
            " ORDER BY m.score DESC LIMIT 25",
            (sha256,),
        ).fetchall()
    return BookDetail(
        sha256=sha256,
        sidecar=book.model_dump(mode="json", by_alias=True),
        status=rows[0]["status"] if rows else None,
        paths=[r["path"] for r in rows],
        candidates=[dict(c) for c in candidates],
        has_cover=cover_path(state.root / "covers", sha256).exists(),
    )


@router.get("/books/{sha256}/candidates")
def get_candidates(
    sha256: str, state: AppState = Depends(get_state)
) -> list[dict]:
    """The same list the detail response carries, for clients that only want it."""
    return get_book(sha256, state).candidates


@router.get("/tags")
def list_tags(state: AppState = Depends(get_state)) -> list[str]:
    with state.db.lock:
        rows = state.db.conn.execute(
            "SELECT tags FROM book_index WHERE tags IS NOT NULL AND tags != ''"
        ).fetchall()
    tags = {t.strip() for r in rows for t in r["tags"].split(";") if t.strip()}
    return sorted(tags)
```

Register it in `app.py`: `app.include_router(books.router, prefix="/api")`.

- [ ] **Step 5: Run the tests and commit**

```bash
.venv/bin/pytest tests/test_api_books.py -v && .venv/bin/pytest -q
git add src/reshelf/web tests/test_api_books.py
git commit -m "feat(web): books list, search, filters and detail"
```

---

### Task 16: Metadata PATCH with the write-back tiers

**Files:**
- Modify: `src/reshelf/web/api/books.py`
- Test: `tests/test_api_metadata.py`

**Interfaces:**
- Consumes: `MetadataPatch`, `WriteBackResult` (Task 15), `embed_metadata`, `rename_library_copy` (Task 12).
- Produces: `PATCH /api/books/{sha256}/metadata`.
- Contract: tier 1 always runs and sets `resolver="human"`. Tier 2 returns `library_file=null` plus a warning when the book is not committed. Tier 3 returns **422** with `{"reason": "convert_first"}` for a non-embeddable primary file.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_api_metadata.py` reusing the `client` fixture shape from `tests/test_api_books.py`, but with a real EPUB on disk:

```python
import pytest
from fastapi.testclient import TestClient

from reshelf.config import default_config, save_config
from reshelf.db.database import Database
from reshelf.store import index
from reshelf.store.models import Book, FileEntry
from reshelf.store.sidecar import SidecarStore
from reshelf.web.app import create_app
from tests.helpers import make_epub

SHA = "a" * 64


def build(tmp_path, fmt="epub", status="COMMITTED"):
    cfg = default_config(tmp_path)
    for sub in ("incoming", "library", "db", "metadata", "derived", "reports", "covers"):
        (tmp_path / sub).mkdir(parents=True, exist_ok=True)
    save_config(cfg, tmp_path)

    path = tmp_path / "library" / f"Old Title.{fmt}"
    if fmt == "epub":
        make_epub(path, "Old Title", "Old Author")
    else:
        path.write_bytes(b"x")

    db = Database(cfg.database.path)
    db.init_schema()
    store = SidecarStore(cfg)
    book = Book(sha256=SHA, files=[FileEntry(path=str(path), format=fmt)])
    book.metadata.title = "Old Title"
    book.metadata.authors = ["Old Author"]
    store.save(book)
    db.conn.execute(
        "INSERT INTO files (path, sha256, format, status) VALUES (?,?,?,?)",
        (str(path), SHA, fmt, status),
    )
    index.sync(db.conn, book)
    db.conn.commit()
    db.close()
    return cfg, path


@pytest.fixture
def client(tmp_path):
    build(tmp_path)
    with TestClient(create_app(tmp_path)) as c:
        yield c


PATCH = {"metadata": {"title": "New Title", "authors": ["New Author"]}}


def test_patch_writes_the_sidecar_and_marks_it_human(client):
    body = client.patch(f"/api/books/{SHA}/metadata", json=PATCH).json()
    assert body["sidecar"]["metadata"]["title"] == "New Title"
    assert body["sidecar"]["source"]["resolver"] == "human"


def test_patch_updates_the_search_index(client):
    client.patch(f"/api/books/{SHA}/metadata", json=PATCH)
    assert client.get("/api/books", params={"q": "New Title"}).json()["total"] == 1


def test_patch_alone_does_not_touch_the_file(client, tmp_path):
    from reshelf.extractors.epub import extract_epub

    client.patch(f"/api/books/{SHA}/metadata", json=PATCH)
    path = tmp_path / "library" / "Old Title.epub"
    assert path.exists()
    assert extract_epub(path).title == "Old Title"


def test_embed_writes_into_the_file(client, tmp_path):
    from reshelf.extractors.epub import extract_epub

    payload = {**PATCH, "write_back": {"embed": True}}
    body = client.patch(f"/api/books/{SHA}/metadata", json=payload).json()
    assert body["embedded"] is True
    assert extract_epub(tmp_path / "library" / "Old Title.epub").title == "New Title"


def test_library_rename_moves_the_file(client, tmp_path):
    payload = {**PATCH, "write_back": {"library_file": True}}
    body = client.patch(f"/api/books/{SHA}/metadata", json=payload).json()
    assert body["library_file"] is not None
    assert "New Title" in body["library_file"]
    assert not (tmp_path / "library" / "Old Title.epub").exists()


def test_embed_on_an_unconverted_kindle_file_is_422_with_a_reason(tmp_path):
    build(tmp_path, fmt="azw3")
    with TestClient(create_app(tmp_path)) as c:
        r = c.patch(
            f"/api/books/{SHA}/metadata",
            json={**PATCH, "write_back": {"embed": True}},
        )
        assert r.status_code == 422
        assert r.json()["detail"]["reason"] == "convert_first"


def test_a_failed_embed_still_leaves_the_sidecar_written(tmp_path):
    """Tier 1 is not rolled back by a tier-3 failure - it is the source of truth."""
    build(tmp_path, fmt="azw3")
    with TestClient(create_app(tmp_path)) as c:
        c.patch(
            f"/api/books/{SHA}/metadata",
            json={**PATCH, "write_back": {"embed": True}},
        )
        detail = c.get(f"/api/books/{SHA}").json()
        assert detail["sidecar"]["metadata"]["title"] == "New Title"


def test_rename_on_an_uncommitted_book_warns_instead_of_failing(tmp_path):
    build(tmp_path, status="MATCHED")
    with TestClient(create_app(tmp_path)) as c:
        body = c.patch(
            f"/api/books/{SHA}/metadata",
            json={**PATCH, "write_back": {"library_file": True}},
        ).json()
        assert body["library_file"] is None
        assert body["warnings"]


def test_patch_on_an_unknown_book_is_404(client):
    r = client.patch("/api/books/" + "f" * 64 + "/metadata", json=PATCH)
    assert r.status_code == 404
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_api_metadata.py -v`
Expected: FAIL with 405 or 404 on the PATCH route.

- [ ] **Step 3: Write the route**

Append to `src/reshelf/web/api/books.py`:

```python
@router.patch("/books/{sha256}/metadata", response_model=WriteBackResult)
def patch_metadata(
    sha256: str,
    payload: MetadataPatch,
    state: AppState = Depends(get_state),
) -> WriteBackResult:
    with state.db.lock:
        row = state.db.conn.execute(
            "SELECT * FROM files WHERE sha256 = ? ORDER BY id LIMIT 1", (sha256,)
        ).fetchone()
    if state.store.load(sha256, row["path"] if row else None) is None:
        raise HTTPException(404, "no such book")

    # Tier 1: always, and it is the source of truth - never rolled back
    # because a later tier failed.
    def mutate(book):
        book.metadata = payload.metadata
        book.source.resolver = "human"
        book.source.confidence = 1.0
        book.source.decided_at = models_now()

    book = state.store.update(sha256, mutate, row["path"] if row else None)
    with state.db.lock:
        index.sync(state.db.conn, book)

    warnings: list[str] = []
    library_file = None
    embedded = False

    # Tier 3 before tier 2, so embedding targets a stable path.
    if payload.write_back.embed:
        primary = book.primary_file()
        if primary is None:
            warnings.append("no file on disk to embed into")
        else:
            try:
                embed_metadata(Path(primary.path), book.metadata)
                embedded = True
            except UnsupportedWriteBack as e:
                raise HTTPException(
                    422, {"message": str(e), "reason": e.reason}
                ) from e

    if payload.write_back.library_file:
        library_file = rename_library_copy(state.cfg, state.db, state.store, sha256)
        if library_file is None:
            warnings.append("not committed to library/ yet; nothing to rename")
        else:
            book = state.store.load(sha256, library_file)

    return WriteBackResult(
        sidecar=book.model_dump(mode="json", by_alias=True),
        library_file=library_file,
        embedded=embedded,
        warnings=warnings,
    )
```

Add the imports: `from pathlib import Path`, `from reshelf.store.models import now as models_now`, `from reshelf.web.schemas import MetadataPatch, WriteBackResult`, `from reshelf.writeback import UnsupportedWriteBack, embed_metadata, rename_library_copy`.

- [ ] **Step 4: Run the tests and commit**

```bash
.venv/bin/pytest tests/test_api_metadata.py -v && .venv/bin/pytest -q
git add src/reshelf/web tests/test_api_metadata.py
git commit -m "feat(web): metadata PATCH with sidecar, library-rename and embed tiers"
```

---

### Task 17: Convert, rematch and choose

`rematch` and `convert` both go through the job runner so they inherit rate limiting, cancellation and logging — one code path, not two. The SPA renders them inline by subscribing to the returned job's event stream.

**Files:**
- Create: `src/reshelf/web/api/actions.py`
- Modify: `src/reshelf/web/app.py`
- Test: `tests/test_api_actions.py`

**Interfaces:**
- Consumes: `JobRunner`, `pipeline.choose`, `AIDisabledError`, `converters.target_format`.
- Produces: `POST /api/books/{sha}/convert` → `202 {"job_id": int}`; `POST /api/books/{sha}/rematch` → `202 {"job_id": int}`; `POST /api/books/{sha}/choose` → the updated `BookDetail`; `GET /api/books/{sha}/candidates`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_api_actions.py` (reuse the `build` helper from Task 16 by importing it):

```python
import time

import pytest
from fastapi.testclient import TestClient

from reshelf.web.app import create_app
from tests.test_api_metadata import SHA, build


def wait(client, job_id, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("done", "failed", "cancelled"):
            return job
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_convert_enqueues_a_job_and_produces_a_derived_file(tmp_path):
    cfg, path = build(tmp_path, fmt="txt")
    path.write_text("hello\n\nworld\n", encoding="utf-8")
    with TestClient(create_app(tmp_path)) as c:
        r = c.post(f"/api/books/{SHA}/convert")
        assert r.status_code == 202
        job = wait(c, r.json()["job_id"])
        assert job["status"] == "done"
        detail = c.get(f"/api/books/{SHA}").json()
        derived = [f for f in detail["sidecar"]["files"] if f["role"] == "converted"]
        assert len(derived) == 1
        assert derived[0]["format"] == "epub"


def test_convert_on_an_already_usable_format_is_409(tmp_path):
    build(tmp_path, fmt="epub")
    with TestClient(create_app(tmp_path)) as c:
        assert c.post(f"/api/books/{SHA}/convert").status_code == 409


def test_convert_on_an_unknown_book_is_404(tmp_path):
    build(tmp_path)
    with TestClient(create_app(tmp_path)) as c:
        assert c.post("/api/books/" + "f" * 64 + "/convert").status_code == 404


def test_rematch_with_ai_is_refused_when_ai_is_off(tmp_path):
    build(tmp_path)
    with TestClient(create_app(tmp_path)) as c:
        r = c.post(f"/api/books/{SHA}/rematch", json={"ai": True})
        assert r.status_code == 409
        assert "ai.provider" in r.json()["detail"]


def test_rematch_without_ai_enqueues_a_job(tmp_path):
    build(tmp_path)
    with TestClient(create_app(tmp_path)) as c:
        r = c.post(f"/api/books/{SHA}/rematch", json={})
        assert r.status_code == 202
        assert "job_id" in r.json()


def test_choose_makes_the_decision_human_and_sticky(tmp_path):
    from reshelf.config import load_config
    from reshelf.db.database import Database
    from reshelf.metadata.models import Candidate

    build(tmp_path)
    cfg = load_config(tmp_path)
    db = Database(cfg.database.path)
    edition_id = db.save_candidate(
        Candidate(
            provider="openlibrary", provider_id="OL1M", title="Chosen Title",
            authors=["Chosen Author"], isbn13="9780441013593",
        )
    )
    db.conn.commit()
    db.close()

    with TestClient(create_app(tmp_path)) as c:
        body = c.post(
            f"/api/books/{SHA}/choose", json={"candidate_id": edition_id}
        ).json()
        assert body["sidecar"]["metadata"]["title"] == "Chosen Title"
        assert body["sidecar"]["source"]["resolver"] == "human"


def test_choose_with_an_unknown_candidate_is_404(tmp_path):
    build(tmp_path)
    with TestClient(create_app(tmp_path)) as c:
        assert c.post(
            f"/api/books/{SHA}/choose", json={"candidate_id": 9999}
        ).status_code == 404
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_api_actions.py -v`
Expected: FAIL with 404 on `/api/books/{sha}/convert`.

- [ ] **Step 3: Write the router**

Create `src/reshelf/web/api/actions.py`:

```python
from fastapi import APIRouter, Depends, HTTPException, Response

from reshelf import pipeline
from reshelf.convert import converters
from reshelf.web.api.books import get_book
from reshelf.web.deps import AppState, get_state
from reshelf.web.schemas import ChoosePayload, RematchPayload

router = APIRouter(tags=["actions"])


def _require_book(state: AppState, sha256: str):
    with state.db.lock:
        row = state.db.conn.execute(
            "SELECT * FROM files WHERE sha256 = ? ORDER BY id LIMIT 1", (sha256,)
        ).fetchone()
    if row is None:
        raise HTTPException(404, "no such book")
    return row


@router.post("/books/{sha256}/convert", status_code=202)
def convert(sha256: str, response: Response, state: AppState = Depends(get_state)):
    row = _require_book(state, sha256)
    book = state.store.load(sha256, row["path"])
    source = next((f for f in (book.files if book else []) if f.role == "original"), None)
    fmt = (source.format if source else row["format"]) or ""
    if converters.target_format(fmt) is None:
        raise HTTPException(409, f"{fmt or 'this format'} needs no conversion")
    return {"job_id": state.runner.enqueue("convert", {"sha256": sha256})}


@router.post("/books/{sha256}/rematch", status_code=202)
def rematch(
    sha256: str, payload: RematchPayload, state: AppState = Depends(get_state)
):
    _require_book(state, sha256)
    if payload.ai and not state.cfg.ai.enabled:
        raise HTTPException(409, "ai.provider is not configured")
    return {
        "job_id": state.runner.enqueue(
            "rematch",
            {"sha256": sha256, "query": payload.query, "ai": payload.ai},
        )
    }


@router.post("/books/{sha256}/choose")
def choose(
    sha256: str, payload: ChoosePayload, state: AppState = Depends(get_state)
):
    _require_book(state, sha256)
    try:
        pipeline.choose(
            state.cfg, state.db, state.store, sha256, payload.candidate_id
        )
    except KeyError as e:
        raise HTTPException(404, f"no such candidate: {payload.candidate_id}") from e
    return get_book(sha256, state)
```

Register it in `app.py` with `prefix="/api"`.

- [ ] **Step 4: Run the tests and commit**

Task 18 adds `GET /api/jobs/{id}`, which these tests poll. Run Task 18 first if `wait()` 404s, or run both tasks' tests together at the end of Task 18.

```bash
.venv/bin/pytest tests/test_api_actions.py -v && .venv/bin/pytest -q
git add src/reshelf/web tests/test_api_actions.py
git commit -m "feat(web): per-book convert, rematch and choose"
```

---
### Task 18: Jobs API and the SSE progress stream

**Pairs with Task 17** — `tests/test_api_actions.py` polls `GET /api/jobs/{id}`, so those two tasks are verified together. Implement 17 then 18, and run both test files at the end of this one.

**Files:**
- Create: `src/reshelf/web/api/jobs.py`
- Modify: `src/reshelf/web/app.py`
- Test: `tests/test_api_jobs.py`

**Interfaces:**
- Consumes: `JobRunner` (Task 13), `JobCreate` (Task 15).
- Produces: `POST /api/jobs` → `202 {"job_id": int}`; `GET /api/jobs`; `GET /api/jobs/{id}`; `DELETE /api/jobs/{id}`; `GET /api/jobs/{id}/events` (SSE, `text/event-stream`, one JSON job snapshot per `data:` line).
- Guard: `commit` and `rollback` are refused with **409** unless the request carries `confirmed: true` in `args`, so the UI must show a plan preview first.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_api_jobs.py`:

```python
import json
import time

import pytest
from fastapi.testclient import TestClient

from reshelf.config import default_config, save_config
from reshelf.web.app import create_app


@pytest.fixture
def client(tmp_path):
    cfg = default_config(tmp_path)
    for sub in ("incoming", "library", "db", "metadata", "derived", "reports", "covers"):
        (tmp_path / sub).mkdir(parents=True, exist_ok=True)
    save_config(cfg, tmp_path)
    with TestClient(create_app(tmp_path)) as c:
        yield c


def wait(client, job_id, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("done", "failed", "cancelled"):
            return job
        time.sleep(0.05)
    raise AssertionError(f"job did not finish: {client.get(f'/api/jobs/{job_id}').json()}")


def test_enqueue_and_complete_a_scan(client):
    r = client.post("/api/jobs", json={"command": "scan", "args": {}})
    assert r.status_code == 202
    assert wait(client, r.json()["job_id"])["status"] == "done"


def test_an_unknown_command_is_422(client):
    r = client.post("/api/jobs", json={"command": "rm", "args": {}})
    assert r.status_code == 422


def test_commit_without_confirmation_is_409(client):
    r = client.post("/api/jobs", json={"command": "commit", "args": {}})
    assert r.status_code == 409
    assert "confirm" in r.json()["detail"].lower()


def test_rollback_without_confirmation_is_409(client):
    r = client.post(
        "/api/jobs", json={"command": "rollback", "args": {"journal_id": "x"}}
    )
    assert r.status_code == 409


def test_commit_with_confirmation_is_accepted(client):
    r = client.post(
        "/api/jobs", json={"command": "commit", "args": {"confirmed": True}}
    )
    assert r.status_code == 202


def test_job_list_is_newest_first(client):
    first = client.post("/api/jobs", json={"command": "scan"}).json()["job_id"]
    wait(client, first)
    second = client.post("/api/jobs", json={"command": "scan"}).json()["job_id"]
    wait(client, second)
    assert client.get("/api/jobs").json()[0]["id"] == second


def test_get_an_unknown_job_is_404(client):
    assert client.get("/api/jobs/9999").status_code == 404


def test_delete_cancels(client):
    job_id = client.post("/api/jobs", json={"command": "scan"}).json()["job_id"]
    client.delete(f"/api/jobs/{job_id}")
    assert client.get(f"/api/jobs/{job_id}").json()["status"] in (
        "cancelled", "done"  # a scan of an empty folder may beat the cancel
    )


def test_events_stream_ends_with_a_terminal_status(client):
    job_id = client.post("/api/jobs", json={"command": "scan"}).json()["job_id"]
    with client.stream("GET", f"/api/jobs/{job_id}/events") as response:
        assert response.headers["content-type"].startswith("text/event-stream")
        last = None
        for line in response.iter_lines():
            if line.startswith("data:"):
                last = json.loads(line[5:])
                if last["status"] in ("done", "failed", "cancelled"):
                    break
    assert last["status"] == "done"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_api_jobs.py -v`
Expected: FAIL with 404 on `/api/jobs`.

- [ ] **Step 3: Write the router**

Create `src/reshelf/web/api/jobs.py`:

```python
import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException
from sse_starlette.sse import EventSourceResponse

from reshelf.web.deps import AppState, get_state
from reshelf.web.jobs import UnknownCommand
from reshelf.web.schemas import JobCreate

router = APIRouter(tags=["jobs"])

# These move or delete files. The UI must run `plan`, show the diff and
# confirm before it may enqueue them.
NEEDS_CONFIRMATION = {"commit", "rollback"}


@router.post("/jobs", status_code=202)
def create_job(payload: JobCreate, state: AppState = Depends(get_state)) -> dict:
    if payload.command in NEEDS_CONFIRMATION and not payload.args.get("confirmed"):
        raise HTTPException(
            409,
            f"{payload.command} needs an explicit confirmation:"
            " run plan, show the diff, then resend with args.confirmed = true",
        )
    try:
        job_id = state.runner.enqueue(payload.command, payload.args)
    except UnknownCommand as e:
        raise HTTPException(422, f"unknown command: {e}") from e
    return {"job_id": job_id}


@router.get("/jobs")
def list_jobs(limit: int = 50, state: AppState = Depends(get_state)) -> list[dict]:
    return state.runner.recent(limit)


@router.get("/jobs/{job_id}")
def get_job(job_id: int, state: AppState = Depends(get_state)) -> dict:
    job = state.runner.get(job_id)
    if job is None:
        raise HTTPException(404, "no such job")
    return job


@router.delete("/jobs/{job_id}")
def cancel_job(job_id: int, state: AppState = Depends(get_state)) -> dict:
    if state.runner.get(job_id) is None:
        raise HTTPException(404, "no such job")
    return {"cancelled": state.runner.cancel(job_id)}


@router.get("/jobs/{job_id}/events")
async def job_events(job_id: int, state: AppState = Depends(get_state)):
    if state.runner.get(job_id) is None:
        raise HTTPException(404, "no such job")

    async def stream():
        # runner.events() blocks on a queue, so it runs off the event loop.
        iterator = state.runner.events(job_id)
        loop = asyncio.get_running_loop()
        while True:
            event = await loop.run_in_executor(None, lambda: next(iterator, None))
            if event is None:
                return
            yield {"data": json.dumps(event, default=str)}
            if event["status"] in ("done", "failed", "cancelled", "interrupted"):
                return

    return EventSourceResponse(stream())
```

Register it in `app.py` with `prefix="/api"`.

- [ ] **Step 4: Run both task's tests and commit**

```bash
.venv/bin/pytest tests/test_api_jobs.py tests/test_api_actions.py -v
.venv/bin/pytest -q
git add src/reshelf/web tests/test_api_jobs.py
git commit -m "feat(web): jobs API with SSE progress and a confirmation gate on commit"
```

---

### Task 19: Serving book files and covers

Range support is what makes pdf.js work on a 200MB scan — it fetches byte ranges rather than the whole file. Sub-project B's reader depends on this handler, so it is built correctly now rather than as a plain download.

**Files:**
- Modify: `src/reshelf/web/api/books.py`
- Test: `tests/test_api_files.py`

**Interfaces:**
- Consumes: `Book.primary_file()`, `cover_path`, `thumb_path`.
- Produces: `GET /api/books/{sha}/file` (`?original=1` to force the original) and `GET /api/books/{sha}/cover` (`?size=thumb|full`).
- Contract: `Accept-Ranges: bytes` always; a `Range` request returns **206** with `Content-Range`; an unsatisfiable range returns **416**; paths are confined to the library root.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_api_files.py`:

```python
import pytest
from fastapi.testclient import TestClient

from reshelf.web.app import create_app
from tests.test_api_metadata import SHA, build


@pytest.fixture
def client(tmp_path):
    build(tmp_path)
    with TestClient(create_app(tmp_path)) as c:
        yield c


def test_whole_file_download(client):
    r = client.get(f"/api/books/{SHA}/file")
    assert r.status_code == 200
    assert r.content[:2] == b"PK"
    assert r.headers["accept-ranges"] == "bytes"


def test_range_request_returns_206_and_the_right_bytes(client):
    whole = client.get(f"/api/books/{SHA}/file").content
    r = client.get(f"/api/books/{SHA}/file", headers={"Range": "bytes=0-9"})
    assert r.status_code == 206
    assert r.content == whole[:10]
    assert r.headers["content-range"] == f"bytes 0-9/{len(whole)}"


def test_open_ended_range(client):
    whole = client.get(f"/api/books/{SHA}/file").content
    r = client.get(f"/api/books/{SHA}/file", headers={"Range": "bytes=5-"})
    assert r.status_code == 206
    assert r.content == whole[5:]


def test_suffix_range(client):
    whole = client.get(f"/api/books/{SHA}/file").content
    r = client.get(f"/api/books/{SHA}/file", headers={"Range": "bytes=-10"})
    assert r.status_code == 206
    assert r.content == whole[-10:]


def test_an_unsatisfiable_range_is_416(client):
    r = client.get(f"/api/books/{SHA}/file", headers={"Range": "bytes=999999999-"})
    assert r.status_code == 416


def test_a_malformed_range_falls_back_to_the_whole_file(client):
    r = client.get(f"/api/books/{SHA}/file", headers={"Range": "pages=1-2"})
    assert r.status_code == 200


def test_file_for_an_unknown_book_is_404(client):
    assert client.get("/api/books/" + "f" * 64 + "/file").status_code == 404


def test_a_sidecar_pointing_outside_the_library_root_is_refused(tmp_path):
    """Defence in depth: the served path is always inside the root."""
    build(tmp_path)
    from reshelf.config import load_config
    from reshelf.store.sidecar import SidecarStore

    store = SidecarStore(load_config(tmp_path))
    store.update(SHA, lambda b: setattr(b.files[0], "path", "/etc/passwd"))
    with TestClient(create_app(tmp_path)) as c:
        assert c.get(f"/api/books/{SHA}/file").status_code == 404


def test_cover_is_404_before_extraction_and_200_after(tmp_path):
    cfg, path = build(tmp_path)
    with TestClient(create_app(tmp_path)) as c:
        assert c.get(f"/api/books/{SHA}/cover").status_code == 404

    from reshelf.covers import cover_path, thumb_path

    for p in (cover_path(tmp_path / "covers", SHA), thumb_path(tmp_path / "covers", SHA)):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"\xff\xd8\xff\xdb fake jpeg")

    with TestClient(create_app(tmp_path)) as c:
        assert c.get(f"/api/books/{SHA}/cover").status_code == 200
        assert c.get(f"/api/books/{SHA}/cover", params={"size": "thumb"}).status_code == 200
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_api_files.py -v`
Expected: FAIL with 404 on `/api/books/{sha}/file`.

- [ ] **Step 3: Write the handlers**

Append to `src/reshelf/web/api/books.py`:

```python
import mimetypes
import re

from fastapi.responses import FileResponse, Response, StreamingResponse

_RANGE = re.compile(r"^bytes=(\d*)-(\d*)$")
_CHUNK = 1 << 18  # 256 KiB


def _resolve_inside_root(root: Path, candidate: str) -> Path | None:
    """Never serve a path outside the library root, whatever the sidecar says."""
    path = Path(candidate)
    if not path.is_absolute():
        path = root / path
    try:
        path = path.resolve()
        path.relative_to(root.resolve())
    except (OSError, ValueError):
        return None
    return path if path.is_file() else None


def _parse_range(header: str, size: int) -> tuple[int, int] | None | Literal[False]:
    """(start, end) inclusive, None for 'serve the whole thing', False for 416."""
    match = _RANGE.match(header.strip())
    if not match:
        return None
    first, last = match.group(1), match.group(2)
    if not first and not last:
        return None
    if not first:  # bytes=-N, the final N bytes
        length = int(last)
        if length <= 0:
            return False
        return max(0, size - length), size - 1
    start = int(first)
    end = int(last) if last else size - 1
    if start >= size or start > end:
        return False
    return start, min(end, size - 1)


@router.get("/books/{sha256}/file")
def get_file(
    sha256: str,
    request: Request,
    original: bool = False,
    state: AppState = Depends(get_state),
):
    with state.db.lock:
        row = state.db.conn.execute(
            "SELECT path FROM files WHERE sha256 = ? ORDER BY id LIMIT 1", (sha256,)
        ).fetchone()
    book = state.store.load(sha256, row["path"] if row else None)
    if book is None:
        raise HTTPException(404, "no such book")
    entry = (
        next((f for f in book.files if f.role == "original"), None)
        if original
        else book.primary_file()
    )
    if entry is None:
        raise HTTPException(404, "no file recorded for this book")
    path = _resolve_inside_root(state.root, entry.path)
    if path is None:
        raise HTTPException(404, "file missing or outside the library root")

    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    size = path.stat().st_size
    headers = {"Accept-Ranges": "bytes"}

    range_header = request.headers.get("range")
    if not range_header:
        return FileResponse(path, media_type=media_type, headers=headers)

    parsed = _parse_range(range_header, size)
    if parsed is False:
        return Response(
            status_code=416, headers={**headers, "Content-Range": f"bytes */{size}"}
        )
    if parsed is None:
        return FileResponse(path, media_type=media_type, headers=headers)

    start, end = parsed
    length = end - start + 1

    def chunks():
        with path.open("rb") as fh:
            fh.seek(start)
            remaining = length
            while remaining > 0:
                data = fh.read(min(_CHUNK, remaining))
                if not data:
                    return
                remaining -= len(data)
                yield data

    return StreamingResponse(
        chunks(),
        status_code=206,
        media_type=media_type,
        headers={
            **headers,
            "Content-Range": f"bytes {start}-{end}/{size}",
            "Content-Length": str(length),
        },
    )


@router.get("/books/{sha256}/cover")
def get_cover(
    sha256: str,
    size: Literal["thumb", "full"] = "full",
    state: AppState = Depends(get_state),
):
    covers = state.root / "covers"
    path = thumb_path(covers, sha256) if size == "thumb" else cover_path(covers, sha256)
    if not path.is_file():
        raise HTTPException(404, "no cover")
    return FileResponse(
        path, media_type="image/jpeg", headers={"Cache-Control": "max-age=86400"}
    )
```

Add `from fastapi import Request` and `from reshelf.covers import cover_path, thumb_path` to the imports.

- [ ] **Step 4: Run the tests and commit**

```bash
.venv/bin/pytest tests/test_api_files.py -v && .venv/bin/pytest -q
git add src/reshelf/web tests/test_api_files.py
git commit -m "feat(web): Range-capable file serving and cover endpoints"
```

- [ ] **Step 5: Smoke-test against the real library**

```bash
.venv/bin/reshelf serve --root . &
sleep 2
curl -s localhost:8080/api/stats | head -c 300; echo
curl -s localhost:8080/api/books?page_size=3 | head -c 500; echo
curl -sI -H 'Range: bytes=0-99' localhost:8080/api/books/$(
  curl -s 'localhost:8080/api/books?page_size=1' | python3 -c \
  'import sys,json;print(json.load(sys.stdin)["items"][0]["sha256"])'
)/file | head -5
kill %1
```
Expected: a status summary, a page of real books, and `HTTP/1.1 206 Partial Content` with a `Content-Range` header.

---
# Phase 3 — SPA

No JS test framework (a Global Constraint), so each task verifies with `npm run build` plus a named browser observation. The backend contract is already covered by pytest; these tasks are about the view.

### Task 20: Vite scaffold, build integration, API client, app shell

**Files:**
- Create: `web/package.json`, `web/vite.config.ts`, `web/tsconfig.json`, `web/index.html`
- Create: `web/src/main.tsx`, `web/src/api.ts`, `web/src/App.tsx`, `web/src/styles.css`
- Create: `web/README.md`
- Modify: `.gitignore` (`web/node_modules`, `src/reshelf/web/static`)
- Modify: `pyproject.toml` (`[tool.hatch.build]` includes `src/reshelf/web/static`)

**Interfaces:**
- Produces: `api.ts` exporting `getCapabilities`, `getStats`, `listBooks`, `getBook`, `patchMetadata`, `chooseCandidate`, `convertBook`, `rematchBook`, `listJobs`, `createJob`, `cancelJob`, `subscribeJob`, `getSettings`, `putSettings`, and the `Book`, `BookDetail`, `Job`, `Capabilities` types. Every later SPA task imports from here and adds nothing of its own.
- Vite builds into `src/reshelf/web/static`, which `app.py` already mounts (Task 14).

- [ ] **Step 1: Scaffold**

```bash
cd web
npm create vite@latest . -- --template react-ts
npm install
npm install react-router-dom
```

- [ ] **Step 2: Point the build at the Python package**

`web/vite.config.ts`:

```ts
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  // The FastAPI app mounts this directory as the SPA.
  build: { outDir: "../src/reshelf/web/static", emptyOutDir: true },
  // `npm run dev` talks to `reshelf serve` on 8080.
  server: { proxy: { "/api": "http://127.0.0.1:8080" } },
});
```

Add to `.gitignore`:

```
web/node_modules
src/reshelf/web/static
```

In `pyproject.toml`, make sure the built SPA ships in the wheel:

```toml
[tool.hatch.build.targets.wheel]
packages = ["src/reshelf"]
artifacts = ["src/reshelf/web/static/**"]
```

- [ ] **Step 3: Write the API client**

`web/src/api.ts`:

```ts
export type Capabilities = {
  ai: boolean;
  ai_provider: string | null;
  commit_mode: "copy" | "move";
  embeddable: string[];
  convert: Record<string, string>;
  converters_available: Record<string, boolean>;
  metadata_layout: string;
};

export type BookListItem = {
  sha256: string;
  title: string | null;
  authors: string | null;
  series: string | null;
  pubdate: string | null;
  primary_format: string | null;
  status: string | null;
  resolver: string | null;
  confidence: number | null;
  has_cover: boolean;
};

export type BookMetadata = {
  title: string | null;
  subtitle: string | null;
  authors: string[];
  translators: string[];
  series: string | null;
  series_index: number | null;
  publisher: string | null;
  pubdate: string | null;
  language: string | null;
  isbn10: string | null;
  isbn13: string | null;
  tags: string[];
  description: string | null;
};

export type Sidecar = {
  schema: number;
  sha256: string;
  files: { path: string; format: string; role: string }[];
  metadata: BookMetadata;
  source: { resolver: string; provider: string | null; confidence: number };
};

export type BookDetail = {
  sha256: string;
  sidecar: Sidecar;
  status: string | null;
  paths: string[];
  candidates: Record<string, unknown>[];
  has_cover: boolean;
};

export type Job = {
  id: number;
  command: string;
  status: string;
  progress: number;
  total: number | null;
  message: string | null;
  log: string | null;
  error: string | null;
};

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw Object.assign(new Error(res.statusText), {
      status: res.status,
      detail: detail.detail,
    });
  }
  return res.status === 204 ? (undefined as T) : res.json();
}

export const getCapabilities = () => req<Capabilities>("/capabilities");
export const getStats = () =>
  req<{ total: number; by_status: Record<string, number> }>("/stats");
export const getSettings = () => req<Record<string, unknown>>("/settings");
export const putSettings = (patch: Record<string, unknown>) =>
  req<Record<string, unknown>>("/settings", {
    method: "PUT",
    body: JSON.stringify(patch),
  });

export const listBooks = (params: Record<string, string | number | undefined>) => {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== "") qs.set(k, String(v));
  }
  return req<{
    items: BookListItem[];
    total: number;
    page: number;
    page_size: number;
  }>(`/books?${qs}`);
};

export const getBook = (sha: string) => req<BookDetail>(`/books/${sha}`);
export const listTags = () => req<string[]>("/tags");

export const patchMetadata = (
  sha: string,
  metadata: BookMetadata,
  write_back: { library_file: boolean; embed: boolean },
) =>
  req<{
    sidecar: Sidecar;
    library_file: string | null;
    embedded: boolean;
    warnings: string[];
  }>(`/books/${sha}/metadata`, {
    method: "PATCH",
    body: JSON.stringify({ metadata, write_back }),
  });

export const chooseCandidate = (sha: string, candidate_id: number) =>
  req<BookDetail>(`/books/${sha}/choose`, {
    method: "POST",
    body: JSON.stringify({ candidate_id }),
  });

export const convertBook = (sha: string) =>
  req<{ job_id: number }>(`/books/${sha}/convert`, { method: "POST" });

export const rematchBook = (
  sha: string,
  body: { query?: Record<string, string>; ai?: boolean },
) =>
  req<{ job_id: number }>(`/books/${sha}/rematch`, {
    method: "POST",
    body: JSON.stringify(body),
  });

export const listJobs = () => req<Job[]>("/jobs");
export const getJob = (id: number) => req<Job>(`/jobs/${id}`);
export const createJob = (command: string, args: Record<string, unknown> = {}) =>
  req<{ job_id: number }>("/jobs", {
    method: "POST",
    body: JSON.stringify({ command, args }),
  });
export const cancelJob = (id: number) =>
  req<{ cancelled: boolean }>(`/jobs/${id}`, { method: "DELETE" });

/** Live job progress. Returns an unsubscribe function. */
export function subscribeJob(id: number, onEvent: (job: Job) => void): () => void {
  const source = new EventSource(`/api/jobs/${id}/events`);
  source.onmessage = (e) => {
    const job: Job = JSON.parse(e.data);
    onEvent(job);
    if (["done", "failed", "cancelled", "interrupted"].includes(job.status)) {
      source.close();
    }
  };
  source.onerror = () => source.close();
  return () => source.close();
}

export const fileUrl = (sha: string) => `/api/books/${sha}/file`;
export const coverUrl = (sha: string, size: "thumb" | "full" = "thumb") =>
  `/api/books/${sha}/cover?size=${size}`;
```

- [ ] **Step 4: Write the shell**

`web/src/App.tsx`:

```tsx
import { NavLink, Route, Routes } from "react-router-dom";
import Library from "./routes/Library";
import BookDetailPage from "./routes/BookDetail";
import Review from "./routes/Review";
import Jobs from "./routes/Jobs";
import Settings from "./routes/Settings";
import JobStatusBar from "./components/JobStatusBar";

export default function App() {
  return (
    <div className="app">
      <nav className="nav">
        <span className="brand">reshelf</span>
        <NavLink to="/">Library</NavLink>
        <NavLink to="/review">Review</NavLink>
        <NavLink to="/jobs">Jobs</NavLink>
        <NavLink to="/settings">Settings</NavLink>
      </nav>
      <main>
        <Routes>
          <Route path="/" element={<Library />} />
          <Route path="/book/:sha" element={<BookDetailPage />} />
          <Route path="/review" element={<Review />} />
          <Route path="/jobs" element={<Jobs />} />
          <Route path="/settings" element={<Settings />} />
        </Routes>
      </main>
      <JobStatusBar />
    </div>
  );
}
```

`web/src/main.tsx`:

```tsx
import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import "./styles.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </React.StrictMode>,
);
```

Create stub files for the five routes and `components/JobStatusBar.tsx`, each exporting a default component returning its name in an `<h1>`. Tasks 21-24 fill them in.

`web/src/styles.css` — a plain stylesheet, no framework: a dark-on-light default, a `.grid` of `repeat(auto-fill, minmax(150px, 1fr))` for covers, `.nav` as a flex row, `.status-bar` fixed to the bottom.

- [ ] **Step 5: Verify**

```bash
cd web && npm run build && ls ../src/reshelf/web/static/index.html
cd .. && .venv/bin/reshelf serve --root . &
sleep 2 && curl -s localhost:8080/ | grep -c '<div id="root">'
kill %1
```
Expected: the build writes `index.html` into the package, and the server serves it at `/`.

- [ ] **Step 6: Commit**

```bash
git add web .gitignore pyproject.toml
git commit -m "feat(web): Vite + React scaffold, API client and app shell"
```

---

### Task 21: Library page

**Files:**
- Create: `web/src/routes/Library.tsx`, `web/src/components/BookCard.tsx`, `web/src/components/Filters.tsx`
- Modify: `web/src/styles.css`

**Interfaces:**
- Consumes: `listBooks`, `listTags`, `getStats`, `coverUrl` from `api.ts`.
- Produces: `<Library />` at `/`, linking each card to `/book/:sha`.

- [ ] **Step 1: Write `BookCard`**

```tsx
import { Link } from "react-router-dom";
import { BookListItem, coverUrl } from "../api";

const STATUS_CLASS: Record<string, string> = {
  MATCHED: "ok",
  COMMITTED: "ok",
  REVIEW: "warn",
  UNRESOLVED: "bad",
  DUPLICATE: "muted",
  ERROR: "bad",
};

export default function BookCard({ book }: { book: BookListItem }) {
  return (
    <Link className="card" to={`/book/${book.sha256}`}>
      {book.has_cover ? (
        <img src={coverUrl(book.sha256)} alt="" loading="lazy" />
      ) : (
        <div className="card-placeholder">{book.primary_format ?? "?"}</div>
      )}
      <div className="card-title">{book.title ?? "(no title)"}</div>
      <div className="card-authors">{book.authors ?? "—"}</div>
      {book.status && (
        <span className={`badge ${STATUS_CLASS[book.status] ?? "muted"}`}>
          {book.status}
        </span>
      )}
    </Link>
  );
}
```

- [ ] **Step 2: Write `Library`**

State: `q`, `status`, `format`, `tag`, `sort`, `page`. Debounce `q` by 250ms. Fetch on any change; reset `page` to 1 whenever a filter changes. Render `<Filters/>`, a result count, the `.grid` of `<BookCard/>`, and prev/next paging. Show `getStats()` totals as status filter chips so the unresolved backlog is one click away.

```tsx
import { useEffect, useState } from "react";
import { BookListItem, listBooks, listTags } from "../api";
import BookCard from "../components/BookCard";
import Filters from "../components/Filters";

export default function Library() {
  const [q, setQ] = useState("");
  const [debounced, setDebounced] = useState("");
  const [filters, setFilters] = useState({ status: "", format: "", tag: "" });
  const [sort, setSort] = useState("title");
  const [page, setPage] = useState(1);
  const [items, setItems] = useState<BookListItem[]>([]);
  const [total, setTotal] = useState(0);
  const [tags, setTags] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const t = setTimeout(() => setDebounced(q), 250);
    return () => clearTimeout(t);
  }, [q]);

  useEffect(() => setPage(1), [debounced, filters, sort]);

  useEffect(() => {
    listBooks({ q: debounced, ...filters, sort, page, page_size: 60 })
      .then((r) => {
        setItems(r.items);
        setTotal(r.total);
        setError(null);
      })
      .catch((e) => setError(String(e.detail ?? e.message)));
  }, [debounced, filters, sort, page]);

  useEffect(() => {
    listTags().then(setTags).catch(() => setTags([]));
  }, []);

  return (
    <div>
      <Filters
        q={q}
        onQ={setQ}
        filters={filters}
        onFilters={setFilters}
        sort={sort}
        onSort={setSort}
        tags={tags}
      />
      {error && <p className="error">{error}</p>}
      <p className="muted">{total} books</p>
      <div className="grid">
        {items.map((b) => (
          <BookCard key={b.sha256} book={b} />
        ))}
      </div>
      <div className="pager">
        <button disabled={page === 1} onClick={() => setPage(page - 1)}>
          Previous
        </button>
        <span>
          {page} / {Math.max(1, Math.ceil(total / 60))}
        </span>
        <button disabled={page * 60 >= total} onClick={() => setPage(page + 1)}>
          Next
        </button>
      </div>
    </div>
  );
}
```

`Filters` is a controlled form: a search input, `<select>`s for status (`MATCHED`, `REVIEW`, `UNRESOLVED`, `COMMITTED`, `DUPLICATE`, `ERROR`), format (`epub`, `pdf`, `mobi`, `azw3`, `txt`, `djvu`) and tag (from `tags`), and a sort `<select>` matching the backend whitelist (`title`, `authors`, `pubdate`, `updated_at`, `added`).

- [ ] **Step 3: Verify in the browser**

```bash
cd web && npm run build && cd ..
.venv/bin/reshelf serve --root .
```
Open `http://127.0.0.1:8080`. Expected: a cover grid of the real library; typing "dune" filters within ~250ms; selecting status `UNRESOLVED` shows the unresolved backlog; paging works; a card click routes to `/book/<sha>`.

- [ ] **Step 4: Commit**

```bash
git add web
git commit -m "feat(web): library page with covers, search, filters and paging"
```

---
### Task 22: Book detail — metadata form, write-back, Fix metadata, Convert

The densest screen in the app, and the one that has to make the write-back tiers legible rather than dangerous.

**Files:**
- Create: `web/src/routes/BookDetail.tsx`, `web/src/components/MetadataForm.tsx`, `web/src/components/CandidateList.tsx`, `web/src/hooks/useCapabilities.ts`, `web/src/hooks/useJob.ts`

**Interfaces:**
- Consumes: `getBook`, `patchMetadata`, `chooseCandidate`, `convertBook`, `rematchBook`, `subscribeJob`, `getCapabilities`, `fileUrl`, `coverUrl`.
- Produces: `<BookDetailPage />` at `/book/:sha`; `useCapabilities()` (cached module-level promise, one fetch per page load); `useJob(id)` returning `{ job, running }` backed by `subscribeJob`.

- [ ] **Step 1: Write the hooks**

```ts
// hooks/useCapabilities.ts - one fetch per page load, shared by every component.
import { useEffect, useState } from "react";
import { Capabilities, getCapabilities } from "../api";

let cached: Promise<Capabilities> | null = null;

export default function useCapabilities() {
  const [caps, setCaps] = useState<Capabilities | null>(null);
  useEffect(() => {
    cached = cached ?? getCapabilities();
    cached.then(setCaps).catch(() => setCaps(null));
  }, []);
  return caps;
}
```

```ts
// hooks/useJob.ts - live progress for one job, used inline rather than on /jobs.
import { useEffect, useState } from "react";
import { Job, subscribeJob } from "../api";

export default function useJob(id: number | null) {
  const [job, setJob] = useState<Job | null>(null);
  useEffect(() => {
    setJob(null);
    if (id === null) return;
    return subscribeJob(id, setJob);
  }, [id]);
  const running = job !== null && ["queued", "running"].includes(job.status);
  return { job, running };
}
```

- [ ] **Step 2: Write the metadata form**

`MetadataForm` takes `metadata`, `capabilities`, `primaryFormat`, and `onSave(metadata, writeBack)`. Fields: title, subtitle, authors (comma-separated text bound to a `string[]`), series + index, publisher, pubdate, language, isbn13, tags (comma-separated), description (textarea).

Below the fields, the two write-back checkboxes, each defaulting from `capabilities` and each labelled with its actual consequence:

```tsx
<label>
  <input
    type="checkbox"
    checked={writeBack.library_file}
    onChange={(e) =>
      setWriteBack({ ...writeBack, library_file: e.target.checked })
    }
  />
  Rename the copy in library/ to match (reversible with rollback)
</label>

<label title={embedDisabledReason ?? ""}>
  <input
    type="checkbox"
    disabled={embedDisabledReason !== null}
    checked={writeBack.embed}
    onChange={(e) => setWriteBack({ ...writeBack, embed: e.target.checked })}
  />
  Write the metadata into the file itself
</label>
{embedDisabledReason && (
  <p className="hint">
    {embedDisabledReason}{" "}
    {convertible && <button onClick={onConvert}>Convert to EPUB</button>}
  </p>
)}
```

where

```ts
const embeddable = caps?.embeddable ?? [];
const convertible = caps ? Boolean(caps.convert[primaryFormat ?? ""]) : false;
const embedDisabledReason = embeddable.includes(primaryFormat ?? "")
  ? null
  : `${primaryFormat ?? "This format"} cannot hold metadata; convert it first.`;
```

Saving calls `patchMetadata`. On a 422 whose `detail.reason === "convert_first"`, do not show a raw error — surface the Convert button instead, because the sidecar did save. Render `warnings` from the response as a non-blocking notice.

- [ ] **Step 3: Write `CandidateList`**

A table of the candidates from `getBook`: title, author, ISBN, publisher, score, confidence, resolver, and the `evidence_json` shown on expand. Each row has a **Use this** button calling `chooseCandidate(sha, edition_id)`, after which the page refetches and the header shows `resolver: human`.

- [ ] **Step 4: Write `BookDetail`**

Layout: cover on the left; on the right the title, status badge, `source.resolver` and confidence, the file list (each path with its `role`, linking to `fileUrl(sha)`), then the metadata form, then the Fix-metadata block, then candidates.

The Fix-metadata block:

```tsx
<section>
  <h3>Fix metadata</h3>
  <input placeholder="title" value={query.title} onChange={...} />
  <input placeholder="author" value={query.author} onChange={...} />
  <input placeholder="isbn" value={query.isbn} onChange={...} />
  <button disabled={running} onClick={() => start(false)}>Search providers</button>
  <button
    disabled={running || !caps?.ai}
    title={caps?.ai ? "" : "Configure an AI provider in Settings"}
    onClick={() => start(true)}
  >
    Search + AI judge
  </button>
  {running && <progress value={job?.progress ?? 0} max={job?.total ?? 1} />}
  {job?.status === "failed" && <p className="error">{job.error}</p>}
</section>
```

`start(ai)` calls `rematchBook(sha, { query: nonEmpty(query), ai })`, stores the returned `job_id` in state so `useJob` streams it, and refetches the book when the job reaches `done`. The AI button is disabled — never hidden — when `caps.ai` is false, with the tooltip saying why.

Convert is the same shape: `convertBook(sha)`, stream the job, refetch on completion. Hide it when `caps.convert[primaryFormat]` is undefined; show it disabled with the reason when `caps.converters_available[primaryFormat] === false` (the `ddjvu` case).

- [ ] **Step 5: Verify in the browser**

```bash
cd web && npm run build && cd .. && .venv/bin/reshelf serve --root .
```

Check each of these on a real book:
1. Editing the title and saving with both checkboxes off changes the page and the library list, and the file on disk is untouched (`ls -l` the path shown).
2. The embed checkbox is disabled on an `.azw3` book, with the Convert button offered.
3. Convert on that book produces a `derived/` entry in the file list, and the embed checkbox becomes enabled.
4. "Search + AI judge" is disabled with a tooltip while `ai.provider` is unset; setting it in Settings enables it without a reload of the server.
5. "Use this" on a candidate flips the header to `resolver: human`.

- [ ] **Step 6: Commit**

```bash
git add web
git commit -m "feat(web): book detail with metadata editing, write-back tiers, fix-metadata and convert"
```

---

### Task 23: Review queue

The screen that replaces the `review` CLI loop. It is the same data as the library page filtered to `REVIEW`, but optimised for going through a backlog fast: one book at a time, keyboard-driven.

**Files:**
- Create: `web/src/routes/Review.tsx`

**Interfaces:**
- Consumes: `listBooks` (with `status=REVIEW`), `getBook`, `chooseCandidate`, `patchMetadata`.
- Produces: `<Review />` at `/review`.

- [ ] **Step 1: Write the page**

Load the `REVIEW` queue once into state, track a cursor, and render the current book's candidates in the same table as Task 22. Advance the cursor after any decision.

Keyboard handling, bound on `window` with a `useEffect` cleanup:

| Key | Action |
|---|---|
| `1`–`9` | Choose that candidate, then advance |
| `j` / `ArrowDown` | Next book, no decision |
| `k` / `ArrowUp` | Previous book |
| `s` | Skip (advance without deciding) |
| `o` | Open the full detail page in a new tab |
| `?` | Toggle the shortcut legend |

Ignore every shortcut while the focus is in an `input` or `textarea` — check `document.activeElement?.tagName`. Show the legend inline under the header so the bindings are discoverable without pressing `?`.

Show "Queue empty" with a link back to the library when the queue is exhausted, and a `n of m` counter in the header so the backlog's size is visible.

- [ ] **Step 2: Verify in the browser**

Open `/review` with real REVIEW-status books. Expected: pressing `2` picks the second candidate and advances; `j`/`k` move without deciding; typing in the search field does not trigger shortcuts; the counter decrements as decisions are made; `resolver: human` sticks after a reload.

- [ ] **Step 3: Commit**

```bash
git add web
git commit -m "feat(web): keyboard-driven review queue replacing the review CLI loop"
```

---

### Task 24: Jobs page, status bar, and Settings page

**Files:**
- Create: `web/src/routes/Jobs.tsx`, `web/src/routes/Settings.tsx`, `web/src/components/JobStatusBar.tsx`, `web/src/components/PlanPreview.tsx`

**Interfaces:**
- Consumes: `listJobs`, `createJob`, `cancelJob`, `subscribeJob`, `getSettings`, `putSettings`, `getCapabilities`.
- Produces: `<Jobs />` at `/jobs`, `<Settings />` at `/settings`, `<JobStatusBar />` in the shell.

- [ ] **Step 1: Jobs page**

A **Run** panel with a button per pipeline stage — `scan`, `extract`, `match`, `resolve`, `plan`, `reindex` — each calling `createJob(command, args)`. `match` gets an `offline` checkbox, `extract` a `force` checkbox, `resolve` a `limit` field and an `include_unresolved` checkbox. `resolve` is disabled with a tooltip when `caps.ai` is false.

Below it, the job list from `listJobs()`, newest first: command, status, `progress/total`, elapsed, a Cancel button while `queued` or `running`, and an expandable `log`. Subscribe to the newest non-terminal job with `subscribeJob` so the log tails live; poll `listJobs()` every 5s only as a backstop.

**The commit gate.** `commit` and `rollback` are not buttons in the Run panel. `commit` lives behind `<PlanPreview/>`:

1. Run `plan`, wait for it to finish, and read the plan path from the job's `message`.
2. Fetch and render the plan's actions grouped by kind (`import`, `quarantine`, `mark_duplicate`) with counts and the first 50 rows of `src → dest`.
3. Only then enable **Apply this plan**, which calls `createJob("commit", { confirmed: true, plan })`.

The backend returns 409 without `confirmed` (Task 18), so this is a real gate, not a UI convention. `rollback` takes a journal id from the list of `reports/journal-*.json` and uses the same confirm step.

- [ ] **Step 2: Status bar**

A fixed bottom bar, rendered in `App.tsx` on every route. Poll `listJobs()` every 5s; when a job is `queued` or `running`, show `command`, a `<progress>`, the latest `message`, and a Cancel button, and switch to `subscribeJob` for that job so it updates without polling. Render nothing when everything is idle.

- [ ] **Step 3: Settings page**

A form over `getSettings()` — the backend's `SETTABLE` list is the source of truth for which keys exist. Group them: **Library** (`library.commit_mode`, `metadata.layout`), **Web** (`web.host`, `web.port`), **Write-back defaults** (`write_back.library_file`, `write_back.embed`), **AI** (`ai.provider`, `ai.model`, `ai.api_key`, `ai.base_url`), **Matching** (`matching.auto_accept`, `matching.review_below`, `convert.timeout`).

Three details that matter:
- `ai.api_key` renders as `type="password"`, and a note says it can be supplied as `RESHELF_AI_API_KEY` instead of being stored in `config.yaml`.
- Setting `ai.provider` to empty means `null`, which turns AI off everywhere — say so next to the field.
- `library.commit_mode: move` gets a warning that originals leave `incoming/` and only `rollback` restores them.

On save, `putSettings` returns the new values; re-render from the response and refetch capabilities so the AI toggles across the app update without a reload. Render a 422's `detail` next to the offending field.

- [ ] **Step 4: Verify in the browser**

1. `/jobs`: clicking **scan** produces a running job with a live log, and the status bar shows it from any route.
2. Cancel on a long `match` moves it to `cancelled` within a second or two.
3. `commit` cannot be started without going through the plan preview; the preview lists real `src → dest` rows.
4. `/settings`: setting `ai.provider` to `claude-cli` immediately enables the AI buttons on a book page; clearing it disables them again.
5. `library.commit_mode` survives a server restart (it is written to `config.yaml`).

- [ ] **Step 5: Final full verification**

```bash
.venv/bin/pytest -q
cd web && npm run build && cd ..
git status --short
```
Expected: all tests pass, the SPA builds, and nothing is left uncommitted.

- [ ] **Step 6: Update the README**

Add a **Web app** section to `README.md`: `reshelf serve`, the default `127.0.0.1:8080` bind and the fact that there is no authentication, the `migrate-json` / `reindex` bootstrap, that sidecars under `metadata/` are the source of truth and the database is disposable, and that AI features stay off until `ai.provider` is set. Note that Docker packaging is sub-project C.

- [ ] **Step 7: Commit**

```bash
git add web README.md
git commit -m "feat(web): jobs page with plan-gated commit, status bar and settings"
```

---

### Task 25: Amend `spec.md`

Spec 14 requires this, and leaving it out would leave `spec.md` stating two
things the code no longer does.

**Files:**
- Modify: `spec.md:125-137` (3.3), `spec.md:844-853` (18 preamble)

- [ ] **Step 1: Amend 3.3**

`spec.md` 3.3 currently reads "Originals must be preserved" and states the
Skill MUST NOT modify files under `incoming/`. Replace the heading with
**"Originals are preserved by default"** and add, after the directory block:

```markdown
`library.commit_mode` selects what a commit does with the original:

* `copy` (default) - the original stays in `incoming/`, a copy lands in `library/`.
* `move` - the original is moved into `library/`. This is an explicit opt-in,
  and the commit journal records it so `rollback` restores the original to
  its former path.

Scan, extract and match MUST NOT modify anything under `incoming/` in either
mode. Writing metadata into a file (write-back tier 3) targets the copy under
`library/` or a derived file under `derived/`, never an original.
```

- [ ] **Step 2: Amend 18**

Add before "Suggested schema":

```markdown
### The database is derived

As of the web app, SQLite is **not** the system of record. A per-book JSON
sidecar (default `metadata/<sha256>.json`) holds the metadata, provenance,
reading position and annotations; see the sub-project A spec for its schema.
The tables below are a cache of scan results and provider candidates, plus
the FTS5 search index and the job queue. The database can be deleted at any
time and rebuilt with `reshelf reindex`.
```

- [ ] **Step 3: Commit**

```bash
git add spec.md
git commit -m "docs(spec): commit_mode opt-in for moves; SQLite demoted to a derived index"
```

---

## Verification against the spec's Definition of Done

Run these against the real library once every task is complete.

- [ ] **1. Serve and browse** — `reshelf serve --root .`, open `http://127.0.0.1:8080`, confirm every scanned book is listed with covers, search and filters.
- [ ] **2. The database is disposable** — `reshelf migrate-json`, then `mv db/books.sqlite3 /tmp/ && reshelf reindex`, then reload the UI. Same books, same metadata, no loss.
- [ ] **3. Edits survive the matcher** — edit a title in the UI, run `match` from the Jobs page, reload the book. The edit stands and `resolver` reads `human`.
- [ ] **4. Conversion** — convert one `.azw3`, one `.mobi` and one `.txt`. Each produces a readable EPUB in `derived/`, the original still exists, and the embed checkbox becomes enabled on that book.
- [ ] **5. Fix metadata** — on an `UNRESOLVED` book, run Search, pick a candidate, reload. It sticks.
- [ ] **6. AI is opt-in** — with `ai.provider` unset, confirm the app is fully usable and `grep` the job logs for any model call. Set it to `claude-cli` and confirm the AI judge appears.
- [ ] **7. Pipeline from the UI** — run scan, extract, match with live progress; cancel one mid-run; confirm `commit` is unreachable without the plan preview.
- [ ] **8. The CLI is intact** — `.venv/bin/pytest -q` passes and `reshelf scan/extract/match/report` still work from the terminal.
