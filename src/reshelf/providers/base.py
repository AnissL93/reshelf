import time
from abc import ABC, abstractmethod

import httpx

from reshelf.metadata.models import Candidate
from reshelf.providers.cache import FileCache


class MetadataProvider(ABC):
    name: str

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

    def _request(self, url: str, params: dict, headers: dict | None = None) -> dict:
        last_exc: Exception | None = None
        for attempt in range(self.max_retries):
            if attempt and self.backoff:
                time.sleep(self.backoff ** attempt)
            wait = self.min_interval - (time.monotonic() - self._last_request)
            if wait > 0:
                time.sleep(wait)
            self._last_request = time.monotonic()
            try:
                resp = self.client.get(url, params=params, headers=headers, timeout=20)
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

    def _get(
        self, key: str, url: str, params: dict, headers: dict | None = None
    ) -> dict | list | None:
        if self.cache is not None:
            hit = self.cache.get(key)
            if hit is not None:
                return hit
        if self.client is None:  # offline
            return None
        data = self._request(url, params, headers)
        if self.cache is not None:
            self.cache.put(key, data)
        return data

    @abstractmethod
    def lookup_isbn(self, isbn: str) -> list[Candidate]: ...

    @abstractmethod
    def search(
        self,
        title: str,
        author: str | None = None,
        language: str | None = None,
    ) -> list[Candidate]: ...

    def enrich(self, cand: Candidate) -> Candidate:
        """Optionally fill in missing edition details for a chosen candidate."""
        return cand
