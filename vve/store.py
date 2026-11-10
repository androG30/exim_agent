"""SQLite state: search cache, page cache, evidence, jobs, tasks, history."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS search_cache (key TEXT PRIMARY KEY, provider TEXT, query TEXT, results TEXT, fetched_at REAL);
CREATE TABLE IF NOT EXISTS page_cache (url TEXT PRIMARY KEY, status INTEGER, final_url TEXT, body TEXT, fetched_at REAL);
CREATE TABLE IF NOT EXISTS evidence (id INTEGER PRIMARY KEY, company_key TEXT, url TEXT, kind TEXT, excerpt TEXT,
    sha256 TEXT, fetched_at TEXT, job_id INTEGER);
CREATE TABLE IF NOT EXISTS jobs (id INTEGER PRIMARY KEY, request TEXT, product TEXT, country TEXT, target INTEGER,
    status TEXT, provider TEXT, created_at TEXT, updated_at TEXT, found INTEGER DEFAULT 0, summary TEXT);
CREATE TABLE IF NOT EXISTS tasks (id INTEGER PRIMARY KEY, job_id INTEGER, kind TEXT, payload TEXT, status TEXT,
    attempts INTEGER DEFAULT 0, last_error TEXT, result TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS history (id INTEGER PRIMARY KEY, at TEXT, job_id INTEGER, event TEXT, detail TEXT);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Store:
    def __init__(self, path: Path | str):
        path = Path(path)
        if str(path) != ":memory:":
            path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path))
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    # ---- caches ---------------------------------------------------------
    @staticmethod
    def _key(*parts) -> str:
        return hashlib.sha256("\x1f".join(map(str, parts)).encode()).hexdigest()

    def get_search(self, provider, query, params, ttl_days):
        r = self.db.execute("SELECT results, fetched_at FROM search_cache WHERE key=?",
                            (self._key(provider, query, json.dumps(params, sort_keys=True)),)).fetchone()
        if r and time.time() - r["fetched_at"] < ttl_days * 86400:
            return json.loads(r["results"])
        return None

    def put_search(self, provider, query, params, results):
        self.db.execute("REPLACE INTO search_cache VALUES (?,?,?,?,?)",
                        (self._key(provider, query, json.dumps(params, sort_keys=True)), provider, query,
                         json.dumps(results), time.time()))
        self.db.commit()

    def get_page(self, url, ttl_days):
        r = self.db.execute("SELECT * FROM page_cache WHERE url=?", (url,)).fetchone()
        if r and time.time() - r["fetched_at"] < ttl_days * 86400:
            return dict(r)
        return None

    def put_page(self, url, status, final_url, body):
        self.db.execute("REPLACE INTO page_cache VALUES (?,?,?,?,?)", (url, status, final_url, body, time.time()))
        self.db.commit()

    # ---- evidence -------------------------------------------------------
    def add_evidence(self, company_key, url, kind, excerpt, body="", job_id=None):
        self.db.execute("INSERT INTO evidence (company_key,url,kind,excerpt,sha256,fetched_at,job_id) VALUES (?,?,?,?,?,?,?)",
                        (company_key, url, kind, excerpt[:1000], hashlib.sha256(body.encode()).hexdigest() if body else "",
                         now_iso(), job_id))
        self.db.commit()

    def evidence_for(self, company_key):
        return [dict(r) for r in self.db.execute("SELECT * FROM evidence WHERE company_key=? ORDER BY id", (company_key,))]

    # ---- jobs / tasks ---------------------------------------------------
    def create_job(self, request, product, country, target, provider):
        cur = self.db.execute("INSERT INTO jobs (request,product,country,target,status,provider,created_at,updated_at) "
                              "VALUES (?,?,?,?,?,?,?,?)", (request, product, country, target, "queued", provider,
                                                          now_iso(), now_iso()))
        self.db.commit()
        self.log(cur.lastrowid, "job_created", request)
        return cur.lastrowid

    def update_job(self, job_id, **kw):
        kw["updated_at"] = now_iso()
        self.db.execute(f"UPDATE jobs SET {', '.join(k + '=?' for k in kw)} WHERE id=?", (*kw.values(), job_id))
        self.db.commit()

    def job(self, job_id):
        r = self.db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return dict(r) if r else None

    def jobs(self, limit=50):
        return [dict(r) for r in self.db.execute("SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (limit,))]

    def add_task(self, job_id, kind, payload):
        cur = self.db.execute("INSERT INTO tasks (job_id,kind,payload,status,updated_at) VALUES (?,?,?,?,?)",
                              (job_id, kind, json.dumps(payload), "pending", now_iso()))
        self.db.commit()
        return cur.lastrowid

    def tasks(self, job_id, kind=None, status=None):
        q, a = "SELECT * FROM tasks WHERE job_id=?", [job_id]
        if kind:
            q, a = q + " AND kind=?", a + [kind]
        if status:
            q, a = q + " AND status=?", a + [status]
        return [dict(r) for r in self.db.execute(q + " ORDER BY id", a)]

    def finish_task(self, task_id, status, result=None, error=None):
        self.db.execute("UPDATE tasks SET status=?, attempts=attempts+1, result=?, last_error=?, updated_at=? WHERE id=?",
                        (status, json.dumps(result) if result is not None else None, error, now_iso(), task_id))
        self.db.commit()

    def progress(self, job_id):
        rows = self.db.execute("SELECT kind, status, COUNT(*) n FROM tasks WHERE job_id=? GROUP BY kind, status", (job_id,))
        out = {}
        for r in rows:
            out.setdefault(r["kind"], {})[r["status"]] = r["n"]
        return out

    # ---- history --------------------------------------------------------
    def log(self, job_id, event, detail=""):
        self.db.execute("INSERT INTO history (at,job_id,event,detail) VALUES (?,?,?,?)", (now_iso(), job_id, event, detail))
        self.db.commit()

    def history(self, job_id=None, limit=200):
        if job_id:
            rows = self.db.execute("SELECT * FROM history WHERE job_id=? ORDER BY id DESC LIMIT ?", (job_id, limit))
        else:
            rows = self.db.execute("SELECT * FROM history ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]
