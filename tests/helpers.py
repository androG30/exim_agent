import json
from vve.config import Config
from vve.store import Store
from vve.http import Fetcher, FetchError


def cfg(tmp_path, **keys):
    c = Config(data_dir=tmp_path / "data", out_dir=tmp_path / "out", state_dir=tmp_path / "state", min_delay_seconds=0)
    c.data_dir.mkdir(parents=True, exist_ok=True)
    c.keys = dict(keys)
    return c


class FakeWeb:
    """Transport stub: pages maps URL -> body or (status, body) or Exception."""
    def __init__(self, pages=None):
        self.pages = pages or {}
        self.calls = []

    def __call__(self, method, url, headers=None, params=None, timeout=20):
        self.calls.append((url, params))
        if url.endswith("/robots.txt") and url not in self.pages:
            return 404, url, ""
        key = url
        if params:
            key = url + "?" + "&".join(f"{k}={v}" for k, v in sorted(params.items()))
            if key not in self.pages:
                key = url
        v = self.pages.get(key)
        if v is None:
            return 404, url, "not found"
        if isinstance(v, Exception):
            raise v
        if isinstance(v, list):          # sequence of responses for retries
            v = v.pop(0) if len(v) > 1 else v[0]
            if isinstance(v, Exception):
                raise v
        if isinstance(v, tuple):
            return v[0], url, v[1]
        return 200, url, v if isinstance(v, str) else json.dumps(v)


def fetcher(c, web, store=None):
    return Fetcher(c, store, transport=web, sleep=lambda s: None)
