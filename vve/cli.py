"""Command line for the VVE Buyer Finder.

  python -m vve research "Find 10 genuine turmeric powder importers in UAE"
  python -m vve status [JOB]          progress of one job, or the job list
  python -m vve retry JOB             re-run failed searches / page fetches
  python -m vve history [JOB]         research history log
  python -m vve providers             search and trade-data integration status (live test)
  python -m vve import-trade SOURCE FILE [--country C]   manual Volza/Panjiva/ImportGenius/ImportYeti export
  python -m vve rankings HS YEAR LABEL   pull a country ranking from UN Comtrade into data/country_rankings.json
  python -m vve campaign [--top N]    plan product x country jobs from trade statistics
  python -m vve report                rebuild CSV / Excel reports (buyer_intel.build)
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date

import buyer_intel as bi

from . import campaign, tradedata
from .config import load_config
from .http import Fetcher
from .jobs import JobRunner, merge_save
from .search import PROVIDERS, SearchError
from .store import Store


def main(argv=None):
    ap = argparse.ArgumentParser(prog="vve", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("research"); r.add_argument("request")
    s = sub.add_parser("status"); s.add_argument("job", nargs="?", type=int)
    rt = sub.add_parser("retry"); rt.add_argument("job", type=int)
    h = sub.add_parser("history"); h.add_argument("job", nargs="?", type=int)
    sub.add_parser("providers")
    it = sub.add_parser("import-trade"); it.add_argument("source", choices=[k for k, v in tradedata.INTEGRATIONS.items()
                                                                            if v["mode"] == "manual_import"])
    it.add_argument("file"); it.add_argument("--country", default="")
    rk = sub.add_parser("rankings"); rk.add_argument("hs"); rk.add_argument("year", type=int); rk.add_argument("label")
    cp = sub.add_parser("campaign"); cp.add_argument("--top", type=int, default=5); cp.add_argument("--target", type=int, default=10)
    sub.add_parser("report")
    a = ap.parse_args(argv)

    cfg = load_config()
    store = Store(cfg.db_path)

    if a.cmd == "research":
        runner = JobRunner(cfg, store)
        job = runner.submit(a.request)
        print(f"job {job} created: {store.job(job)['product']} / {store.job(job)['country']}")
        try:
            res = runner.run(job)
        except SearchError as e:
            print(f"BLOCKED: {e}", file=sys.stderr)
            return 2
        bi.build(cfg.data_dir, cfg.out_dir)
        print(json.dumps(res, indent=1))
    elif a.cmd == "status":
        if a.job:
            print(json.dumps({"job": store.job(a.job), "progress": store.progress(a.job)}, indent=1))
        else:
            for j in store.jobs():
                print(f"{j['id']:>4} {j['status']:<28} {j['found']:>3}/{j['target']:<3} {j['request']}")
    elif a.cmd == "retry":
        res = JobRunner(cfg, store).retry(a.job)
        bi.build(cfg.data_dir, cfg.out_dir)
        print(json.dumps(res, indent=1))
    elif a.cmd == "history":
        for e in reversed(store.history(a.job)):
            print(e["at"], e["job_id"], e["event"], e["detail"])
    elif a.cmd == "providers":
        f = Fetcher(cfg, store)
        out = [cls(cfg, f).test_connection() for cls in PROVIDERS.values()]
        out.append(tradedata.Comtrade(cfg, f).test_connection())
        out += [{"provider": k, "status": "manual import", "notes": v["notes"]} for k, v in tradedata.INTEGRATIONS.items()
                if v["mode"] == "manual_import"]
        print(json.dumps(out, indent=1))
    elif a.cmd == "import-trade":
        res = tradedata.import_shipments(a.file, a.source, a.country, date.today().isoformat())
        by_country = {}
        for c in res["companies"]:
            by_country.setdefault(c["country"] or a.country or "Unknown", []).append(c)
        for ctry, cs in by_country.items():
            print("saved", merge_save(cfg.data_dir, ctry, cs), len(cs), "importers")
        store.log(None, "trade_import", f"{a.source} {a.file}: {res['rows']} rows, {res['skipped']} skipped")
        print(json.dumps({k: v for k, v in res.items() if k != "companies"}, indent=1))
        bi.build(cfg.data_dir, cfg.out_dir)
    elif a.cmd == "rankings":
        rows = tradedata.Comtrade(cfg, Fetcher(cfg, store)).ranking(a.hs, a.year, a.label)
        p = cfg.data_dir / "country_rankings.json"
        cur = json.loads(p.read_text()) if p.exists() else []
        cur = [r for r in cur if not (r["hs"] == a.hs and r["year"] == a.year)] + rows
        p.write_text(json.dumps(cur, indent=1))
        print(f"{len(rows)} rows saved for HS {a.hs} {a.year}")
    elif a.cmd == "campaign":
        p = campaign.save_plan(cfg.data_dir, a.top, a.target)
        for r in json.loads(p.read_text()):
            print(f"{r['product']:<17} #{r['rank']} {r['country']:<22} USD {r['import_value_usd_m']}M  -> {r['request']}")
        print("saved", p)
    elif a.cmd == "report":
        bi.build(cfg.data_dir, cfg.out_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
