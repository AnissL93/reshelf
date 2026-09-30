"""Pydantic request/response models for the web API."""

from typing import Any, Literal

from pydantic import BaseModel, Field

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


class ReadableFile(BaseModel):
    """One file of a book, described for the reader.

    `file_sha` is what an annotation binds to, resolved here rather than
    in the SPA: `FileEntry.sha256` is None on originals, where the book's
    own hash is the original's, and getting that fallback wrong would
    attach a PDF's highlights to its converted EPUB."""

    file_sha: str
    path: str
    format: str
    role: str
    engine: Literal["pdf", "epub"] | None = None
    convert_to: str | None = None


class BookDetail(BaseModel):
    sha256: str
    sidecar: dict[str, Any]
    status: str | None = None
    paths: list[str] = []
    candidates: list[dict[str, Any]] = []
    has_cover: bool = False
    # Resolved server-side (see books.get_book): the SPA has no library
    # root and so cannot rank files[] correctly on its own.
    primary_format: str | None = None
    readable: list[ReadableFile] = []


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


class AnnotationCreate(BaseModel):
    """`id`, `created_at` and `updated_at` are assigned server-side. They
    are absent from this model on purpose: a client that sends one gets
    it ignored by FastAPI rather than honoured."""

    type: Literal["highlight", "bookmark"] = "highlight"
    file_sha: str
    anchor: dict[str, Any]
    color: Literal["yellow", "green", "blue", "pink"] = "yellow"
    note: str = ""


class AnnotationPatch(BaseModel):
    """`extra="forbid"` is what turns an attempt to move an anchor into a
    422 instead of a silently ignored field."""

    model_config = {"extra": "forbid"}

    note: str | None = None
    color: Literal["yellow", "green", "blue", "pink"] | None = None


class ReadingUpdate(BaseModel):
    locator: str | None = None
    percent: float = Field(0.0, ge=0.0, le=1.0)
