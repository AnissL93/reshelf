import re

import httpx

from reshelf.metadata.models import Author, Candidate, Edition, Work
from reshelf.metadata.normalization import clean_text
from reshelf.providers.base import MetadataProvider
from reshelf.providers.cache import FileCache

API = "https://api.douban.com/v2"
SUGGEST = "https://book.douban.com/j/subject_suggest"

# Widely-circulated community API key for the legacy v2 endpoint; override
# via providers.douban.apikey in config.yaml.
DEFAULT_APIKEY = "0ac44ae016490db2204ce0a042db2916"

# The suggest endpoint rejects non-browser user agents.
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
}

_CJK = re.compile(r"[一-鿿]")


def _language_of(title: str) -> str | None:
    return "zh" if _CJK.search(title) else None


class DoubanProvider(MetadataProvider):
    name = "douban"

    def __init__(
        self,
        client: httpx.Client | None = None,
        cache: FileCache | None = None,
        apikey: str = DEFAULT_APIKEY,
        **kwargs,
    ):
        super().__init__(client=client, cache=cache, **kwargs)
        self.apikey = apikey

    def _candidate(self, d: dict) -> Candidate:
        title = clean_text(d.get("title")) or ""
        edition = Edition(
            work=Work(
                title=title,
                authors=[
                    Author(name=a)
                    for a in (clean_text(x) for x in d.get("author", []))
                    if a
                ],
            ),
            isbn13=d.get("isbn13") or None,
            isbn10=d.get("isbn10") or None,
            publisher=clean_text(d.get("publisher")),
            publication_date=clean_text(d.get("pubdate")),
            language=_language_of(title),
            edition_name=clean_text(d.get("subtitle")),
        )
        return Candidate(
            provider=self.name,
            provider_id=str(d.get("id", "")),
            edition=edition,
        )

    def lookup_isbn(self, isbn: str) -> list[Candidate]:
        try:
            data = self._get(
                f"isbn:{isbn}",
                f"{API}/book/isbn/{isbn}",
                {"apikey": self.apikey},
                headers=_HEADERS,
            )
        except httpx.HTTPStatusError as e:
            if e.response is not None and e.response.status_code == 404:
                return []  # douban simply doesn't know this ISBN
            raise
        if not data or not data.get("title"):
            return []
        return [self._candidate(data)]

    def search(
        self,
        title: str,
        author: str | None = None,
        language: str | None = None,
    ) -> list[Candidate]:
        # subject_suggest only takes a free-text query; author is unused
        data = self._get(
            f"search:{title}".lower(), SUGGEST, {"q": title}, headers=_HEADERS
        )
        if not data:
            return []
        out: list[Candidate] = []
        for item in data[:10]:
            if item.get("type") != "b":
                continue
            item_title = clean_text(item.get("title")) or ""
            author_name = clean_text(item.get("author_name"))
            edition = Edition(
                work=Work(
                    title=item_title,
                    authors=[Author(name=author_name)] if author_name else [],
                ),
                publication_date=str(item["year"]) if item.get("year") else None,
                language=_language_of(item_title),
            )
            out.append(
                Candidate(
                    provider=self.name,
                    provider_id=str(item.get("id", "")),
                    edition=edition,
                )
            )
        return out

    def enrich(self, cand: Candidate) -> Candidate:
        """Fetch full record for a chosen search hit to fill ISBN/publisher."""
        if cand.edition.isbn13 or not cand.provider_id:
            return cand
        try:
            data = self._get(
                f"book:{cand.provider_id}",
                f"{API}/book/{cand.provider_id}",
                {"apikey": self.apikey},
                headers=_HEADERS,
            )
        except httpx.HTTPError:
            return cand
        if not data or not data.get("title"):
            return cand
        enriched = self._candidate(data)
        enriched.score = cand.score
        enriched.confidence = cand.confidence
        enriched.evidence = cand.evidence
        return enriched
