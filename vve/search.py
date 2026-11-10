"""Configurable web search providers.

Only authorised APIs are used: Google Programmable Search (Custom Search JSON API),
SerpApi and Brave Search API. No scraping of search-engine result pages.
Each provider returns a list of {"title","url","snippet","rank"}.
"""
from __future__ import annotations

from .http import Fetcher, FetchError

COUNTRY_GL = {"United Arab Emirates": "ae", "USA": "us", "United States": "us", "United Kingdom": "uk", "Germany": "de",
              "Netherlands": "nl", "Saudi Arabia": "sa", "Malaysia": "my", "Morocco": "ma", "China": "cn", "Japan": "jp",
              "Canada": "ca", "France": "fr", "Spain": "es", "Turkey": "tr", "Australia": "au", "Italy": "it",
              "Egypt": "eg", "Bangladesh": "bd", "Pakistan": "pk", "Vietnam": "vn", "Poland": "pl", "Oman": "om",
              "Kuwait": "kw", "Qatar": "qa", "Bahrain": "bh", "South Africa": "za", "Indonesia": "id", "Thailand": "th"}


class SearchError(Exception):
    pass


class Provider:
    name = "base"
    requires = ()

    def __init__(self, cfg, fetcher: Fetcher, store=None):
        self.cfg, self.fetcher, self.store = cfg, fetcher, store

    def configured(self) -> bool:
        return all(self.cfg.keys.get(k) for k in self.requires)

    def missing(self):
        return [k for k in self.requires if not self.cfg.keys.get(k)]

    def search(self, query: str, country: str = "", num: int | None = None) -> list[dict]:
        num = num or self.cfg.results_per_query
        params = {"q": query, "country": country, "num": num}
        if self.store:
            hit = self.store.get_search(self.name, query, params, self.cfg.cache_ttl_days)
            if hit is not None:
                return hit
        if not self.configured():
            raise SearchError(f"{self.name} not configured: set {', '.join(self.missing())}")
        try:
            results = self._search(query, COUNTRY_GL.get(country, ""), num)
        except FetchError as e:
            raise SearchError(f"{self.name}: {e}") from e
        for i, r in enumerate(results, 1):
            r["rank"] = i
        if self.store:
            self.store.put_search(self.name, query, params, results)
        return results

    def test_connection(self) -> dict:
        if not self.configured():
            return {"provider": self.name, "status": "awaiting credentials", "missing": self.missing()}
        try:
            res = self._search("spice importers Dubai", "ae", 3)
            return {"provider": self.name, "status": "connected", "sample_results": len(res)}
        except FetchError as e:
            return {"provider": self.name, "status": "error", "error": str(e)}


class GoogleCSE(Provider):
    name = "google_cse"
    requires = ("GOOGLE_CSE_KEY", "GOOGLE_CSE_CX")
    URL = "https://www.googleapis.com/customsearch/v1"

    def _search(self, q, gl, num):
        out = []
        for start in range(1, num + 1, 10):
            params = {"key": self.cfg.keys["GOOGLE_CSE_KEY"], "cx": self.cfg.keys["GOOGLE_CSE_CX"], "q": q,
                      "num": min(10, num - start + 1), "start": start}
            if gl:
                params["gl"] = gl
            data = self.fetcher.get_json(self.URL, params=params)
            items = data.get("items", [])
            out += [{"title": i.get("title", ""), "url": i.get("link", ""), "snippet": i.get("snippet", "")} for i in items]
            if len(items) < 10:
                break
        return out[:num]


class SerpApi(Provider):
    name = "serpapi"
    requires = ("SERPAPI_KEY",)
    URL = "https://serpapi.com/search.json"

    def _search(self, q, gl, num):
        params = {"engine": "google", "q": q, "num": num, "api_key": self.cfg.keys["SERPAPI_KEY"]}
        if gl:
            params["gl"] = gl
        data = self.fetcher.get_json(self.URL, params=params)
        if data.get("error"):
            raise FetchError(data["error"])
        return [{"title": r.get("title", ""), "url": r.get("link", ""), "snippet": r.get("snippet", "")}
                for r in data.get("organic_results", [])][:num]


class Brave(Provider):
    name = "brave"
    requires = ("BRAVE_SEARCH_KEY",)
    URL = "https://api.search.brave.com/res/v1/web/search"

    def _search(self, q, gl, num):
        params = {"q": q, "count": min(num, 20)}
        if gl:
            params["country"] = gl.upper() if gl != "uk" else "GB"
        data = self.fetcher.get_json(self.URL, params=params, headers={"X-Subscription-Token": self.cfg.keys["BRAVE_SEARCH_KEY"],
                                                                       "Accept": "application/json"})
        return [{"title": r.get("title", ""), "url": r.get("url", ""), "snippet": r.get("description", "")}
                for r in data.get("web", {}).get("results", [])][:num]


class StaticProvider(Provider):
    """Offline provider for tests and replaying saved result sets. `results` maps a
    query (or '*') to a list of result dicts."""
    name = "static"

    def __init__(self, cfg, fetcher=None, store=None, results=None):
        super().__init__(cfg, fetcher, store)
        self.results = results or {}
        self.calls = []

    def configured(self):
        return True

    def _search(self, q, gl, num):
        self.calls.append(q)
        if q in self.results:
            r = self.results[q]
        else:
            r = self.results.get("*", [])
        if isinstance(r, Exception):
            raise r
        return [dict(x) for x in r][:num]


PROVIDERS = {p.name: p for p in (GoogleCSE, SerpApi, Brave)}


def get_provider(cfg, fetcher, store=None) -> Provider | None:
    """The provider named in config, else the first one with credentials, else None."""
    if cfg.search_provider and cfg.search_provider != "none":
        if cfg.search_provider not in PROVIDERS:
            raise SearchError(f"unknown search provider {cfg.search_provider!r}; choose from {sorted(PROVIDERS)}")
        return PROVIDERS[cfg.search_provider](cfg, fetcher, store)
    for cls in PROVIDERS.values():
        p = cls(cfg, fetcher, store)
        if p.configured():
            return p
    return None
