import httpx

from reshelf.providers.cache import FileCache
from reshelf.providers.douban import DoubanProvider

ISBN_RESPONSE = {
    "id": "2567698",
    "title": "三体",
    "subtitle": "“地球往事”三部曲之一",
    "author": ["刘慈欣"],
    "publisher": "重庆出版社",
    "pubdate": "2008-1",
    "isbn10": "7536692935",
    "isbn13": "9787536692930",
}

SUGGEST_RESPONSE = [
    {
        "title": "三体",
        "url": "https://book.douban.com/subject/2567698/",
        "author_name": "刘慈欣",
        "year": "2008",
        "type": "b",
        "id": "2567698",
    },
    {
        "title": "某部电影",
        "type": "m",
        "id": "999",
    },
]


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_lookup_isbn_parses_candidate():
    def handler(request):
        assert request.url.path == "/v2/book/isbn/9787536692930"
        assert request.url.params["apikey"]
        return httpx.Response(200, json=ISBN_RESPONSE)

    provider = DoubanProvider(client=_client(handler), min_interval=0)
    [cand] = provider.lookup_isbn("9787536692930")
    assert cand.provider == "douban"
    assert cand.provider_id == "2567698"
    assert cand.edition.isbn13 == "9787536692930"
    assert cand.edition.publisher == "重庆出版社"
    assert cand.edition.language == "zh"
    assert cand.edition.work.title == "三体"
    assert cand.edition.work.authors[0].name == "刘慈欣"


def test_search_filters_books_and_infers_language():
    def handler(request):
        assert request.url.path == "/j/subject_suggest"
        return httpx.Response(200, json=SUGGEST_RESPONSE)

    provider = DoubanProvider(client=_client(handler), min_interval=0)
    cands = provider.search("三体")
    assert len(cands) == 1  # movie filtered out
    assert cands[0].edition.work.title == "三体"
    assert cands[0].edition.language == "zh"
    assert cands[0].edition.publication_date == "2008"
    assert cands[0].edition.isbn13 is None


def test_enrich_fills_isbn():
    def handler(request):
        if request.url.path == "/j/subject_suggest":
            return httpx.Response(200, json=SUGGEST_RESPONSE)
        assert request.url.path == "/v2/book/2567698"
        return httpx.Response(200, json=ISBN_RESPONSE)

    provider = DoubanProvider(client=_client(handler), min_interval=0)
    [cand] = provider.search("三体")
    cand.score, cand.evidence = 80.0, ["exact_title"]
    enriched = provider.enrich(cand)
    assert enriched.edition.isbn13 == "9787536692930"
    assert enriched.edition.publisher == "重庆出版社"
    assert enriched.score == 80.0 and enriched.evidence == ["exact_title"]


def test_enrich_survives_api_failure():
    def handler(request):
        if request.url.path == "/j/subject_suggest":
            return httpx.Response(200, json=SUGGEST_RESPONSE)
        return httpx.Response(403)

    provider = DoubanProvider(
        client=_client(handler), min_interval=0, backoff=0
    )
    [cand] = provider.search("三体")
    assert provider.enrich(cand).edition.isbn13 is None  # unchanged, no raise


def test_offline_mode(tmp_path):
    provider = DoubanProvider(client=None, cache=FileCache(tmp_path))
    assert provider.lookup_isbn("9787536692930") == []
    assert provider.search("三体") == []


def test_lookup_isbn_404_means_no_result():
    def handler(request):
        return httpx.Response(404)

    provider = DoubanProvider(client=_client(handler), min_interval=0, backoff=0)
    assert provider.lookup_isbn("9789070967017") == []
