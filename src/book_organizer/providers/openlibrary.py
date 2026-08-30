from book_organizer.metadata.models import Author, Candidate, Edition, Work
from book_organizer.metadata.normalization import normalize_language
from book_organizer.providers.base import MetadataProvider

BASE = "https://openlibrary.org"


class OpenLibraryProvider(MetadataProvider):
    name = "openlibrary"

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
