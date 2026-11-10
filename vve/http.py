"""Polite HTTP layer: robots.txt, per-host delay, retries, page cache.

The transport is injectable so tests (and offline runs) never touch the network.
A transport is a callable (method, url, headers, params, timeout) -> (status, final_url, text).
"""
from __future__ import annotations

import time
import urllib.parse
import urllib.request
import urllib.robotparser
from typing import Callable, Optional


class FetchError(Exception):
    pass


def urllib_transport(method, url, headers=None, params=None, timeout=20):
    if params:
        url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read(3_000_000)
            charset = r.headers.get_content_charset() or "utf-8"
            return r.status, r.geturl(), raw.decode(charset, "replace")
    except urllib.error.HTTPError as e:
        body = e.read(200_000).decode("utf-8", "replace") if hasattr(e, "read") else ""
        return e.code, url, body
    except Exception as e:  # network refused, DNS, TLS, timeout
        raise FetchError(f"{type(e).__name__}: {e}") from e


class Fetcher:
    def __init__(self, cfg, store=None, transport: Optional[Callable] = None, sleep=time.sleep):
        self.cfg = cfg
        self.store = store
        self.transport = transport or urllib_transport
        self.sleep = sleep
        self._last_hit: dict[str, float] = {}
        self._robots: dict[str, Optional[urllib.robotparser.RobotFileParser]] = {}

    # API calls (search providers, Comtrade): no robots check, retries on 429/5xx
    def get_json(self, url, params=None, headers=None):
        import json
        last = None
        for attempt in range(self.cfg.max_attempts):
            try:
                status, _, text = self.transport("GET", url, headers=headers or {}, params=params,
                                                 timeout=self.cfg.request_timeout)
            except FetchError as e:
                last = str(e)
            else:
                if status == 200:
                    return json.loads(text)
                last = f"HTTP {status}: {text[:200]}"
                if status not in (429, 500, 502, 503, 504):
                    break
            self.sleep(min(2 ** attempt, 8))
        raise FetchError(last or "request failed")

    def allowed(self, url) -> bool:
        p = urllib.parse.urlsplit(url)
        host = f"{p.scheme}://{p.netloc}"
        if host not in self._robots:
            rp = urllib.robotparser.RobotFileParser()
            try:
                status, _, text = self.transport("GET", host + "/robots.txt",
                                                 headers={"User-Agent": self.cfg.user_agent}, params=None,
                                                 timeout=self.cfg.request_timeout)
                if status == 200:
                    rp.parse(text.splitlines())
                elif status in (401, 403):
                    rp.parse(["User-agent: *", "Disallow: /"])
                else:
                    rp.parse([])
            except FetchError:
                rp = None   # unreachable: page fetch will fail anyway
            self._robots[host] = rp
        rp = self._robots[host]
        return True if rp is None else rp.can_fetch(self.cfg.user_agent, url)

    def get_page(self, url) -> dict:
        """Returns {'status','final_url','body','cached'}; raises FetchError."""
        if self.store:
            hit = self.store.get_page(url, self.cfg.cache_ttl_days)
            if hit:
                return {"status": hit["status"], "final_url": hit["final_url"], "body": hit["body"], "cached": True}
        if not self.allowed(url):
            raise FetchError("blocked by robots.txt")
        host = urllib.parse.urlsplit(url).netloc
        wait = self.cfg.min_delay_seconds - (time.time() - self._last_hit.get(host, 0))
        if wait > 0:
            self.sleep(wait)
        last = None
        for attempt in range(self.cfg.max_attempts):
            try:
                status, final, body = self.transport("GET", url, headers={"User-Agent": self.cfg.user_agent},
                                                     params=None, timeout=self.cfg.request_timeout)
                self._last_hit[host] = time.time()
                if status in (429, 500, 502, 503, 504):
                    last = f"HTTP {status}"
                    self.sleep(min(2 ** attempt, 8))
                    continue
                if self.store and status == 200:
                    self.store.put_page(url, status, final, body)
                if status != 200:
                    raise FetchError(f"HTTP {status}")
                return {"status": status, "final_url": final, "body": body, "cached": False}
            except FetchError as e:
                last = str(e)
                if last.startswith("HTTP 4"):
                    break
                self.sleep(min(2 ** attempt, 8))
        raise FetchError(last or "fetch failed")
