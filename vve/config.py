"""Configuration: search provider, API keys, limits, paths.

Settings come from (later wins): defaults -> config.json in the repo root ->
environment variables. API keys are only ever read from the environment so they
are never committed.

Environment variables
  VVE_SEARCH_PROVIDER   google_cse | serpapi | brave | none   (default: first one with credentials)
  GOOGLE_CSE_KEY, GOOGLE_CSE_CX     Google Programmable Search (Custom Search JSON API)
  SERPAPI_KEY                       SerpApi (Google results)
  BRAVE_SEARCH_KEY                  Brave Search API
  COMTRADE_KEY                      UN Comtrade subscription key (optional; public preview used without it)
  VOLZA_API_KEY                     Volza API (paid; only if your plan includes API access)
  VVE_USER_AGENT                    HTTP user agent for page fetches
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@dataclass
class Config:
    root: Path = ROOT
    data_dir: Path = ROOT / "data"
    out_dir: Path = ROOT / "output"
    state_dir: Path = ROOT / "data" / "state"
    search_provider: str = ""
    results_per_query: int = 10
    max_queries_per_job: int = 12
    max_candidates_per_job: int = 40
    max_pages_per_company: int = 5
    request_timeout: int = 20
    min_delay_seconds: float = 1.0       # politeness delay per host
    max_attempts: int = 3                # retries for failed searches / fetches
    cache_ttl_days: int = 14
    user_agent: str = "VVE-BuyerFinder/0.2 (B2B importer research; respects robots.txt)"
    keys: dict = field(default_factory=dict)

    @property
    def db_path(self) -> Path:
        return self.state_dir / "research.db"


KEY_VARS = ["GOOGLE_CSE_KEY", "GOOGLE_CSE_CX", "SERPAPI_KEY", "BRAVE_SEARCH_KEY", "COMTRADE_KEY", "VOLZA_API_KEY"]


def load_config(path: Path | None = None, env: dict | None = None) -> Config:
    env = os.environ if env is None else env
    cfg = Config()
    p = Path(path) if path else ROOT / "config.json"
    if p.exists():
        for k, v in json.loads(p.read_text()).items():
            if k in ("data_dir", "out_dir", "state_dir"):
                v = (ROOT / v) if not Path(v).is_absolute() else Path(v)
            if hasattr(cfg, k) and k != "keys":
                setattr(cfg, k, v)
    cfg.keys = {k: env[k] for k in KEY_VARS if env.get(k)}
    cfg.search_provider = env.get("VVE_SEARCH_PROVIDER", cfg.search_provider)
    cfg.user_agent = env.get("VVE_USER_AGENT", cfg.user_agent)
    return cfg
