import time

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
        min_interval: float = 1.0,
        max_retries: int = 3,
        backoff: float = 2.0,
    ):
        self.client = client
        self.cache = cache
        self.min_interval = min_interval
        self.max_retries = max_retries
        self.backoff = backoff
        self._last_request = 0.0

    def _request(self, url: str, params: dict) -> dict:
        last_exc: Exception | None = None
        for attempt in range(self.max_retries):
            if attempt and self.backoff:
                time.sleep(self.backoff ** attempt)
            wait = self.min_interval - (time.monotonic() - self._last_request)
            if wait > 0:
                time.sleep(wait)
            self._last_request = time.monotonic()
            try:
                resp = self.client.get(url, params=params, timeout=20)
            except httpx.TransportError as e:
                last_exc = e
                continue
            if resp.status_code == 429 or resp.status_code >= 500:
                last_exc = httpx.HTTPStatusError(
                    f"server returned {resp.status_code}",
                    request=resp.request,
                    response=resp,
                )
                continue
            resp.raise_for_status()
            return resp.json()
        assert last_exc is not None
        raise last_exc

    def _get(self, key: str, url: str, params: dict) -> dict | None:
        if self.cache is not None:
            hit = self.cache.get(key)
            if hit is not None:
                return hit
        if self.client is None:  # offline
            return None
        data = self._request(url, params)
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
