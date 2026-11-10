import pytest
from helpers import cfg, FakeWeb, fetcher
from vve.search import GoogleCSE, SerpApi, Brave, SearchError, get_provider
from vve.store import Store
from vve.http import FetchError


def test_google_cse_parses_and_caches(tmp_path):
    c = cfg(tmp_path, GOOGLE_CSE_KEY="k", GOOGLE_CSE_CX="cx")
    web = FakeWeb({GoogleCSE.URL: {"items": [{"title": "Spice Co LLC", "link": "https://spiceco.ae/", "snippet": "importer"}]}})
    store = Store(c.db_path)
    p = GoogleCSE(c, fetcher(c, web), store)
    r = p.search("spice importers", "United Arab Emirates", 5)
    assert r == [{"title": "Spice Co LLC", "url": "https://spiceco.ae/", "snippet": "importer", "rank": 1}]
    sent = web.calls[0][1]
    assert sent["key"] == "k" and sent["cx"] == "cx" and sent["gl"] == "ae"
    p.search("spice importers", "United Arab Emirates", 5)
    assert len(web.calls) == 1, "second identical search must come from cache"


def test_serpapi_and_brave_parse(tmp_path):
    c = cfg(tmp_path, SERPAPI_KEY="s", BRAVE_SEARCH_KEY="b")
    web = FakeWeb({SerpApi.URL: {"organic_results": [{"title": "A", "link": "https://a.ae", "snippet": "x"}]},
                   Brave.URL: {"web": {"results": [{"title": "B", "url": "https://b.ae", "description": "y"}]}}})
    f = fetcher(c, web)
    assert SerpApi(c, f).search("q", "United Arab Emirates")[0]["url"] == "https://a.ae"
    assert Brave(c, f).search("q", "United Arab Emirates")[0]["snippet"] == "y"


def test_unconfigured_provider_refuses(tmp_path):
    c = cfg(tmp_path)
    p = GoogleCSE(c, fetcher(c, FakeWeb()))
    with pytest.raises(SearchError, match="not configured"):
        p.search("q")
    assert p.test_connection()["status"] == "awaiting credentials"
    assert get_provider(c, fetcher(c, FakeWeb())) is None


def test_search_retries_429_then_succeeds(tmp_path):
    c = cfg(tmp_path, SERPAPI_KEY="s")
    web = FakeWeb({SerpApi.URL: [(429, "slow down"), {"organic_results": [{"title": "A", "link": "https://a.ae"}]}]})
    assert len(SerpApi(c, fetcher(c, web)).search("q")) == 1
    assert len(web.calls) == 2


def test_search_api_error_surfaces(tmp_path):
    c = cfg(tmp_path, SERPAPI_KEY="s")
    web = FakeWeb({SerpApi.URL: (401, '{"error":"Invalid API key"}')})
    with pytest.raises(SearchError, match="401"):
        SerpApi(c, fetcher(c, web)).search("q")
