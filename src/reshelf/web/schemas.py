"""Pydantic request/response models for the web API."""

from typing import Any

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
    # Resolved server-side (see books.get_book): the SPA has no library
    # root and so cannot rank files[] correctly on its own.
    primary_format: str | None = None


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
