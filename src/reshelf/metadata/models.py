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
