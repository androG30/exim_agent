"""Research jobs: parse a request, search, research candidates, validate, save.

Example: run("Find 10 genuine turmeric powder importers in UAE")
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import buyer_intel as bi

from .discovery import Researcher, queries_for, triage, official_link_from_fair_page, host, company_name
from .http import Fetcher, FetchError
from .search import SearchError, get_provider
from .store import Store, now_iso

PRODUCT_ALIASES = [
    ("turmeric_powder", r"turmeric\s+powder|ground\s+turmeric|haldi\s+powder"),
    ("turmeric_fingers", r"turmeric\s+fingers?|whole\s+turmeric|dried\s+turmeric|turmeric\s+bulbs?"),
    ("cashew_kernels", r"cashew(\s+(nut\s+)?kernels?|s|\s+nuts?)?"),
    ("whole_spices", r"(indian\s+)?whole\s+spices?|spices?"),
]
COUNTRY_ALIASES = {"uae": "United Arab Emirates", "u.a.e": "United Arab Emirates", "emirates": "United Arab Emirates",
                   "united arab emirates": "United Arab Emirates", "dubai": "United Arab Emirates",
                   "usa": "USA", "us": "USA", "united states": "USA", "america": "USA",
                   "uk": "United Kingdom", "united kingdom": "United Kingdom", "britain": "United Kingdom",
                   "ksa": "Saudi Arabia", "saudi": "Saudi Arabia", "saudi arabia": "Saudi Arabia",
                   "holland": "Netherlands", "the netherlands": "Netherlands"}
FILE_SLUG = {"United Arab Emirates": "uae", "USA": "usa", "United Kingdom": "uk"}


def parse_request(text: str) -> dict:
    t = text.lower().strip().rstrip(".")
    m = re.search(r"\b(\d{1,3})\b", t)
    target = int(m.group(1)) if m else 10
    product = next((p for p, rx in PRODUCT_ALIASES if re.search(rx, t)), None)
    if not product:
        raise ValueError("could not tell the product; say turmeric powder, turmeric fingers, cashew kernels or whole spices")
    cm = re.search(r"\b(?:in|from|for)\s+(?:the\s+)?([a-z .]+?)\s*$", t)
    raw = (cm.group(1).strip() if cm else "")
    country = COUNTRY_ALIASES.get(raw, raw.title() if raw else "")
    if not country:
        raise ValueError("could not tell the country; end the request with 'in <country>'")
    return {"product": product, "country": country, "target": target}


def buyers_file(data_dir: Path, country: str) -> Path:
    return Path(data_dir) / f"buyers_{FILE_SLUG.get(country, re.sub(r'[^a-z0-9]+', '_', country.lower()).strip('_'))}.json"


def _ev_rank(p: dict) -> int:
    pr = bi.ProductRelevance(**{k: v for k, v in p.items() if k in bi.ProductRelevance.__dataclass_fields__})
    order = {"": 0, "category_only": 1, "shipment_form_unknown": 2, "catalogue_exact": 3, "shipment": 4}
    return order.get(bi.effective_evidence(pr), 0) * 2 + (1 if pr.ranking_evidence else 0)


def merge_save(data_dir: Path, country: str, new: list[dict]) -> Path:
    """Merge new company dicts into data/buyers_<country>.json without duplicates.
    Existing manual notes are kept; for the same product the stronger evidence wins;
    fresh research updates research_date, website_checked and empty contact fields."""
    path = buyers_file(data_dir, country)
    existing = json.loads(path.read_text()) if path.exists() else []
    objs = [bi.Company.from_dict(d) for d in existing]
    for d in new:
        c = bi.Company.from_dict(d)
        match = next((o for o in objs if (c.website and o.website and bi.norm_domain(o.website) == bi.norm_domain(c.website))
                      or bi.norm_name(o.name) == bi.norm_name(c.name)), None)
        if not match:
            objs.append(c)
            continue
        for f in ("website", "general_email", "sales_email", "purchasing_contact", "phone", "linkedin", "address", "city",
                  "contact_page", "catalogue_url"):
            if getattr(c, f) and not getattr(match, f):
                setattr(match, f, getattr(c, f))
        match.website_checked = match.website_checked or c.website_checked
        match.research_date = max(match.research_date or "", c.research_date or "")
        match.independent_sources = sorted(set(match.independent_sources + c.independent_sources))
        match.source_urls = sorted(set(match.source_urls + c.source_urls))
        have = {p.product: p for p in match.products}
        for p in c.products:
            old = have.get(p.product)
            if old is None:
                match.products.append(p)
            elif _ev_rank(p.__dict__) > _ev_rank(old.__dict__):
                p.evidence_urls = list(dict.fromkeys(p.evidence_urls + old.evidence_urls))
                p.ranking_evidence = p.ranking_evidence or old.ranking_evidence
                match.products[match.products.index(old)] = p
            else:
                old.evidence_urls = list(dict.fromkeys(old.evidence_urls + p.evidence_urls))
    from dataclasses import asdict
    path.write_text(json.dumps([asdict(o) for o in objs], indent=1, ensure_ascii=False))
    return path


class JobRunner:
    def __init__(self, cfg, store: Store | None = None, fetcher: Fetcher | None = None, provider=None, out=sys.stdout,
                 today=None):
        self.cfg = cfg
        self.store = store or Store(cfg.db_path)
        self.fetcher = fetcher or Fetcher(cfg, self.store)
        self.provider = provider if provider is not None else get_provider(cfg, self.fetcher, self.store)
        self.out = out
        self.today = today

    def say(self, job_id, msg):
        print(f"[job {job_id}] {msg}", file=self.out)

    def submit(self, request: str) -> int:
        spec = parse_request(request)
        pname = self.provider.name if self.provider else "none"
        job_id = self.store.create_job(request, spec["product"], spec["country"], spec["target"], pname)
        for q in queries_for(spec["product"], spec["country"], self.cfg.max_queries_per_job):
            self.store.add_task(job_id, "search", {"query": q})
        # revalidate known companies for this country
        path = buyers_file(self.cfg.data_dir, spec["country"])
        if path.exists():
            for d in json.loads(path.read_text()):
                if d.get("website"):
                    self.store.add_task(job_id, "research", {"url": d["website"], "via": "seed", "name": d["name"],
                                                             "source_url": d["website"]})
        return job_id

    def run(self, job_id: int) -> dict:
        job = self.store.job(job_id)
        if not self.provider:
            self.store.update_job(job_id, status="blocked", summary="No search provider configured")
            self.store.log(job_id, "blocked", "no search provider credentials")
            raise SearchError("No search provider configured. Set GOOGLE_CSE_KEY+GOOGLE_CSE_CX, SERPAPI_KEY or BRAVE_SEARCH_KEY.")
        self.store.update_job(job_id, status="running")
        product, country = job["product"], job["country"]
        researcher = Researcher(self.cfg, self.fetcher, self.store, self.provider, self.today)

        # 1. searches
        for t in self.store.tasks(job_id, "search", "pending") + [x for x in self.store.tasks(job_id, "search", "failed")
                                                                 if x["attempts"] < self.cfg.max_attempts]:
            q = json.loads(t["payload"])["query"]
            try:
                res = self.provider.search(q, country)
                self.store.finish_task(t["id"], "done", res)
                self.say(job_id, f"search ok ({len(res)}): {q}")
            except SearchError as e:
                self.store.finish_task(t["id"], "failed", error=str(e))
                self.store.log(job_id, "search_failed", f"{q}: {e}")
                self.say(job_id, f"search FAILED: {q}: {e}")

        # 2. candidates from search results
        known = {host(json.loads(t["payload"])["url"]) for t in self.store.tasks(job_id, "research")}
        for t in self.store.tasks(job_id, "search", "done"):
            for r in json.loads(t["result"] or "[]"):
                kind = triage(r)
                url = r["url"]
                via = "search"
                if kind == "trade_fair":
                    try:
                        page = self.fetcher.get_page(url)
                        off = official_link_from_fair_page(page["body"], url)
                    except FetchError:
                        off = ""
                    if not off:
                        continue
                    via, src, url = "trade_fair", r["url"], off
                elif kind != "official":
                    continue
                else:
                    src = url
                h = host(url)
                if h in known or len(known) >= self.cfg.max_candidates_per_job:
                    continue
                known.add(h)
                self.store.add_task(job_id, "research", {"url": f"https://{h}/" if via == "search" else url,
                                                         "via": via, "source_url": src, "name": r.get("title", "")})

        # 3. research candidates
        accepted, rejected = [], []
        todo = self.store.tasks(job_id, "research", "pending") + [x for x in self.store.tasks(job_id, "research", "failed")
                                                                  if x["attempts"] < self.cfg.max_attempts]
        for i, t in enumerate(todo, 1):
            cand = json.loads(t["payload"])
            res = researcher.research(cand, product, country, job_id)
            status = {"accepted": "done", "rejected": "rejected", "failed": "failed"}[res["status"]]
            self.store.finish_task(t["id"], status, res.get("company"), res.get("reason"))
            self.say(job_id, f"[{i}/{len(todo)}] {res['status']}: {cand['url']} {res.get('reason', '')}")
            if res["status"] == "accepted":
                accepted.append(res["company"])
            else:
                rejected.append((cand["url"], res["reason"]))
        # include companies accepted in earlier runs of this job
        accepted_all = [json.loads(t["result"]) for t in self.store.tasks(job_id, "research", "done") if t["result"]]

        # 4. save + rank
        if accepted_all:
            merge_save(self.cfg.data_dir, country, accepted_all)
        ranked = self.rank(country, product)
        found = len(ranked)
        status = "complete" if found >= job["target"] else "complete_below_target"
        failed = len(self.store.tasks(job_id, "search", "failed")) + len(self.store.tasks(job_id, "research", "failed"))
        if failed:
            status += "_with_failures"
        summary = f"{found} genuine {product} buyers in {country} (target {job['target']}); {len(accepted)} accepted this run, {len(rejected)} rejected, {failed} failed tasks"
        self.store.update_job(job_id, status=status, found=found, summary=summary)
        self.store.log(job_id, "job_finished", summary)
        report = self.write_report(job_id, ranked[: job["target"]], rejected)
        self.say(job_id, summary)
        return {"job_id": job_id, "status": status, "found": found, "summary": summary, "report": str(report)}

    def retry(self, job_id: int) -> dict:
        for kind in ("search", "research"):
            for t in self.store.tasks(job_id, kind, "failed"):
                if t["attempts"] < self.cfg.max_attempts:
                    self.store.log(job_id, "retry", f"{kind} task {t['id']}")
        return self.run(job_id)

    def rank(self, country, product):
        path = buyers_file(self.cfg.data_dir, country)
        if not path.exists():
            return []
        comps = bi.dedupe([bi.Company.from_dict(d) for d in json.loads(path.read_text())])
        rows = []
        for c in comps:
            for p in c.products:
                if p.product != product:
                    continue
                st = bi.verification_status(c, p)
                if st == "Unverified":
                    continue
                s = bi.score(c, p)
                rows.append({"company": c.name, "website": c.website, "score": s["total"], "priority": s["priority"],
                             "verification": st, "size_class": bi.size_class(c, p), "evidence": bi.effective_evidence(p),
                             "forms": p.forms, "email": c.general_email or c.sales_email, "phone": c.phone,
                             "sources": p.evidence_urls[:3]})
        # largest first: size class, then score
        return sorted(rows, key=lambda r: (r["size_class"][0], -r["score"], r["company"]))

    def write_report(self, job_id, ranked, rejected) -> Path:
        job = self.store.job(job_id)
        d = Path(self.cfg.out_dir) / "jobs"
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"job_{job_id}_{job['product']}_{job['country'].replace(' ', '_')}.md"
        lines = [f"# Job {job_id}: {job['request']}", "", f"Status: {job['status']}  ", f"Search provider: {job['provider']}  ",
                 f"Finished: {now_iso()}", "", f"Found {len(ranked)} of {job['target']} requested.", ""]
        if ranked:
            lines += ["| # | Company | Class | Score | Verification | Evidence | Contact | Sources |", "|---|---|---|---|---|---|---|---|"]
            for i, r in enumerate(ranked, 1):
                lines.append(f"| {i} | {r['company']} | {r['size_class']} | {r['score']} {r['priority']} | {r['verification']} | "
                             f"{r['evidence']}: {r['forms']} | {r['email']} {r['phone']} | {' '.join(r['sources'])} |")
        if rejected:
            lines += ["", "## Rejected candidates", ""] + [f"- {u}: {why}" for u, why in rejected]
        fails = self.store.tasks(job_id, "search", "failed") + self.store.tasks(job_id, "research", "failed")
        if fails:
            lines += ["", "## Failed tasks (retry with `python -m vve retry %d`)" % job_id, ""] + \
                     [f"- {t['kind']} {json.loads(t['payload']).get('query') or json.loads(t['payload']).get('url')}: {t['last_error']}" for t in fails]
        p.write_text("\n".join(lines) + "\n")
        return p
