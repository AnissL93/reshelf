"""The sidecar document: one JSON file per book, the source of truth.

Every model allows extra keys so that a sidecar written by a newer
reshelf (sub-project B adds annotation fields) survives a rewrite by an
older one. Load, mutate, dump - unknown keys ride along.
"""

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from reshelf.paths import under

logger = logging.getLogger(__name__)

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


class Annotation(BaseModel):
    """One highlight or bookmark. `anchor` is opaque here by design:
    the backend stores and returns it verbatim, which is also what makes
    an anchor kind from a future version survive a rewrite."""

    model_config = _EXTRA

    id: str
    type: Literal["highlight", "bookmark"] = "highlight"
    file_sha: str
    color: str = "yellow"
    note: str = ""
    anchor: dict = Field(default_factory=dict)
    created_at: str = Field(default_factory=now)
    updated_at: str = Field(default_factory=now)


class Book(BaseModel):
    model_config = _EXTRA

    schema_version: int = Field(default=1, alias="schema")
    sha256: str
    files: list[FileEntry] = Field(default_factory=list)
    metadata: BookMetadata = Field(default_factory=BookMetadata)
    source: Source = Field(default_factory=Source)
    reading: Reading = Field(default_factory=Reading)
    annotations: list[Annotation] = Field(default_factory=list)

    @field_validator("annotations", mode="before")
    @classmethod
    def _drop_malformed_annotations(cls, value: object) -> object:
        """Sidecars are hand-editable. A junk entry costs that entry, not
        the book: strict validation here would raise inside
        model_validate and 500 the detail page of a book whose metadata
        is fine."""
        if not isinstance(value, list):
            return value
        kept = []
        for item in value:
            if isinstance(item, Annotation):
                kept.append(item)
            elif isinstance(item, dict) and isinstance(item.get("id"), str):
                kept.append(item)
            else:
                logger.warning("dropping malformed annotation entry: %r", item)
        return kept

    created_at: str = Field(default_factory=now)
    updated_at: str = Field(default_factory=now)

    @property
    def is_human(self) -> bool:
        """A human decision is sticky - match and resolve must not overwrite it."""
        return self.source.resolver == "human"

    def primary_file(self, library_dir: Path | None = None) -> FileEntry | None:
        """Converted first (readable and metadata-writable), then library, then the rest.

        `library_dir` is `<root>/library`; without it the library tier
        simply doesn't apply (converted-first still does). It is a real
        containment check, not a `"library" in parts` component match -
        that one calls every file a library copy as soon as the user's
        root is itself named `library`, which is exactly what let tier-3
        embedding reach an original under `incoming/`.
        """
        if not self.files:
            return None

        def rank(f: FileEntry) -> int:
            if f.role == "converted":
                return 0
            return 1 if under(f.path, library_dir) else 2

        return sorted(self.files, key=rank)[0]
