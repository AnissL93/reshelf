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
