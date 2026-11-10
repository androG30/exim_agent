import json
from helpers import cfg, FakeWeb, fetcher
from vve.jobs import JobRunner
from vve.search import StaticProvider, SearchError
from vve.store import Store
from vve.http import FetchError
import io

GOOD = """<html><head><title>Gulf Spice Trading LLC | Spice Importers Dubai</title></head><body>
<a href="/products">Products</a> <a href="/contact-us">Contact</a>
<a href="https://www.linkedin.com/company/gulf-spice">LinkedIn</a>
<p>Gulf Spice Trading is an importer and wholesale distributor based in Deira, Dubai, UAE.
We import spices sourced from India and Vietnam.</p></body></html>"""
PRODUCTS = "<html><body><h2>Our range</h2><ul><li>Turmeric Powder (Erode)</li><li>Turmeric fingers</li><li>Cumin seeds</li></ul></body></html>"
CONTACT = "<html><body>Call +971 4 222 3344 or write to <a href='mailto:info@gulfspice.ae'>info@gulfspice.ae</a> or purchase@gulfspice.ae</body></html>"
ABROAD = "<html><head><title>Euro Spice GmbH</title></head><body>Spice importer in Hamburg, Germany. Turmeric powder.</body></html>"
NOPRODUCT = "<html><head><title>Dubai Cars LLC</title></head><body>Car dealer and importer in Dubai, UAE.</body></html>"
FAIR = "<html><body><h1>Desert Mills LLC</h1><a href='https://desertmills.ae/'>Visit website</a></body></html>"
MILLS = "<html><head><title>Desert Mills LLC</title></head><body>Spice grinding and packing in Sharjah, UAE. Ground turmeric, chilli powder.</body></html>"

RESULTS = [
    {"title": "Gulf Spice Trading", "url": "https://www.gulfspice.ae/about", "snippet": ""},
    {"title": "Euro Spice", "url": "https://eurospice.de/", "snippet": ""},
    {"title": "Dubai Cars", "url": "https://dubaicars.ae/", "snippet": ""},
    {"title": "Tridge listing", "url": "https://www.tridge.com/sellers/x", "snippet": ""},
    {"title": "Desert Mills - Gulfood", "url": "https://www.gulfood.com/exhibitors/desert-mills-llc", "snippet": ""},
    {"title": "Broken Site", "url": "https://broken.ae/", "snippet": ""},
]


def setup(tmp_path, broken=FetchError("connection reset")):
    c = cfg(tmp_path)
    web = FakeWeb({"https://gulfspice.ae/": GOOD, "https://gulfspice.ae/products": PRODUCTS,
                   "https://gulfspice.ae/contact-us": CONTACT, "https://eurospice.de/": ABROAD,
                   "https://dubaicars.ae/": NOPRODUCT, "https://www.gulfood.com/exhibitors/desert-mills-llc": FAIR,
                   "https://desertmills.ae/": MILLS, "https://broken.ae/": broken})
    store = Store(c.db_path)
    f = fetcher(c, web, store)
    prov = StaticProvider(c, f, store, results={'"Gulf Spice Trading LLC" United Arab Emirates': [
        {"title": "Gulf Spice Trading LLC - Gulfood exhibitor", "url": "https://www.gulfood.com/exhibitors/gulf-spice", "snippet": ""}],
        "*": RESULTS})
    return c, store, f, prov, web


def test_end_to_end_turmeric_powder_job(tmp_path):
    c, store, f, prov, web = setup(tmp_path)
    r = JobRunner(c, store, f, prov, out=io.StringIO(), today="2026-11-10")
    job = r.submit("Find 10 genuine turmeric powder importers in UAE")
    res = r.run(job)
    assert res["status"].startswith("complete_below_target")
    saved = json.loads((c.data_dir / "buyers_uae.json").read_text())
    names = {s["name"] for s in saved}
    assert names == {"Gulf Spice Trading LLC", "Desert Mills LLC"}
    g = next(s for s in saved if s["name"].startswith("Gulf"))
    assert g["general_email"] == "info@gulfspice.ae" and g["purchasing_contact"] == "purchase@gulfspice.ae"
    assert g["phone"] == "+97142223344" and g["linkedin"] == "https://www.linkedin.com/company/gulf-spice"
    p = g["products"][0]
    assert p["evidence"] == "catalogue_exact" and p["form_evidence"] == "catalogue" and p["sources_from_india"] is True
    assert "https://www.gulfood.com/exhibitors/gulf-spice" in g["independent_sources"]
    m = next(s for s in saved if s["name"].startswith("Desert"))
    assert m["independent_sources"] == ["https://www.gulfood.com/exhibitors/desert-mills-llc"]
    # rejected: wrong country, no product; aggregator never researched
    tasks = store.tasks(job, "research")
    reasons = {json.loads(t["payload"])["url"]: (t["status"], t["last_error"]) for t in tasks}
    assert reasons["https://eurospice.de/"][0] == "rejected" and "United Arab Emirates" in reasons["https://eurospice.de/"][1]
    assert reasons["https://dubaicars.ae/"][0] == "rejected"
    assert reasons["https://broken.ae/"][0] == "failed"
    assert not any("tridge" in u for u in reasons)
    report = open(res["report"]).read()
    assert "Gulf Spice Trading LLC" in report and "Failed tasks" in report
    assert store.evidence_for(g["company_id"]), "page evidence must be stored"
    assert any(h["event"] == "job_finished" for h in store.history(job))


def test_retry_recovers_failed_fetch(tmp_path):
    c, store, f, prov, web = setup(tmp_path)
    r = JobRunner(c, store, f, prov, out=io.StringIO(), today="2026-11-10")
    job = r.submit("Find 3 genuine turmeric powder importers in UAE")
    r.run(job)
    web.pages["https://broken.ae/"] = "<html><head><title>Broken Spices LLC</title></head><body>Importer of turmeric powder, Dubai UAE</body></html>"
    res = r.retry(job)
    assert res["found"] == 3 and res["status"] == "complete"
    assert any(h["event"] == "retry" for h in store.history(job))


def test_job_blocked_without_provider(tmp_path):
    import pytest
    c = cfg(tmp_path)
    store = Store(c.db_path)
    r = JobRunner(c, store, fetcher(c, FakeWeb(), store), provider=None, out=io.StringIO())
    r.provider = None
    job = r.submit("Find 10 genuine cashew kernels importers in UAE")
    with pytest.raises(SearchError, match="No search provider"):
        r.run(job)
    assert store.job(job)["status"] == "blocked"


def test_failed_search_is_recorded_and_retried(tmp_path):
    c, store, f, prov, web = setup(tmp_path)
    prov.results = {"*": SearchError("quota exceeded")}
    r = JobRunner(c, store, f, prov, out=io.StringIO(), today="2026-11-10")
    job = r.submit("Find 10 genuine cashew kernels importers in UAE")
    r.run(job)
    assert store.progress(job)["search"] == {"failed": len(store.tasks(job, "search"))}
    prov.results = {"*": RESULTS}
    r.retry(job)
    assert store.progress(job)["search"].get("done") == len(store.tasks(job, "search"))
