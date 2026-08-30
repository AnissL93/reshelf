import httpx
import pytest

from book_organizer.providers.cache import FileCache
from book_organizer.providers.openlibrary import OpenLibraryProvider

ISBN_RESPONSE = {
    "ISBN:9780765382030": {
        "key": "/books/OL26831316M",
        "title": "The Three-Body Problem",
        "authors": [{"name": "Liu Cixin"}],
        "publishers": [{"name": "Tor Books"}],
        "publish_date": "2014",
        "identifiers": {"isbn_13": ["9780765382030"], "isbn_10": ["0765382032"]},
    }
}

SEARCH_RESPONSE = {
    "docs": [
        {
            "key": "/works/OL17091839W",
            "title": "The Three-Body Problem",
            "author_name": ["Liu Cixin"],
            "first_publish_year": 2008,
            "isbn": ["0765382032", "9780765382030"],
            "language": ["eng"],
        }
    ]
}


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_lookup_isbn_parses_candidate():
    def handler(request):
        assert request.url.path == "/api/books"
        return httpx.Response(200, json=ISBN_RESPONSE)

    provider = OpenLibraryProvider(client=_client(handler))
    [cand] = provider.lookup_isbn("9780765382030")
    assert cand.provider == "openlibrary"
    assert cand.provider_id == "/books/OL26831316M"
    assert cand.edition.isbn13 == "9780765382030"
    assert cand.edition.isbn10 == "0765382032"
    assert cand.edition.publisher == "Tor Books"
    assert cand.edition.work.title == "The Three-Body Problem"
    assert cand.edition.work.authors[0].name == "Liu Cixin"


def test_search_parses_candidates():
    def handler(request):
        assert request.url.path == "/search.json"
        return httpx.Response(200, json=SEARCH_RESPONSE)

    provider = OpenLibraryProvider(client=_client(handler))
    [cand] = provider.search("The Three-Body Problem", author="Liu Cixin")
    assert cand.edition.isbn13 == "9780765382030"
    assert cand.edition.language == "en"
    assert cand.edition.publication_date == "2008"


def test_cache_hit_avoids_network(tmp_path):
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(200, json=ISBN_RESPONSE)

    cache = FileCache(tmp_path)
    p1 = OpenLibraryProvider(client=_client(handler), cache=cache)
    p1.lookup_isbn("9780765382030")
    p2 = OpenLibraryProvider(client=_client(handler), cache=cache)
    p2.lookup_isbn("9780765382030")
    assert calls == ["/api/books"]  # second call served from cache


def test_offline_mode(tmp_path):
    provider = OpenLibraryProvider(client=None, cache=FileCache(tmp_path))
    assert provider.lookup_isbn("9780765382030") == []


def test_retries_on_server_error():
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) < 3:
            return httpx.Response(500)
        return httpx.Response(200, json=ISBN_RESPONSE)

    provider = OpenLibraryProvider(
        client=_client(handler), min_interval=0, backoff=0
    )
    [cand] = provider.lookup_isbn("9780765382030")
    assert len(calls) == 3 and cand.edition.isbn13 == "9780765382030"


def test_gives_up_after_max_retries():
    def handler(request):
        return httpx.Response(500)

    provider = OpenLibraryProvider(
        client=_client(handler), min_interval=0, backoff=0
    )
    with pytest.raises(httpx.HTTPStatusError):
        provider.lookup_isbn("9780765382030")
