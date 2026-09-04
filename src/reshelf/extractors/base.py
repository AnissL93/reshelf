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
