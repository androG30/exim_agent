"""Trade-data integration layer.

Connected by API:   UN Comtrade (public preview without a key, full API with COMTRADE_KEY).
Manual import only: ImportYeti, Volza, Panjiva, ImportGenius, ITC Trade Map. These are
paid or login-only and have no API available to this project, so the agent reads the
CSV/XLSX exports you download from your own account. Nothing is fetched from them and
no shipment data is ever generated.
"""
from __future__ import annotations

import csv
import re
from collections import defaultdict
from pathlib import Path

from . import forms
from .http import FetchError

INTEGRATIONS = {
    "un_comtrade": {"mode": "api", "env": "COMTRADE_KEY (optional)", "notes": "Public preview API (max 500 rows) without a key; full data API with a subscription key."},
    "itc_trade_map": {"mode": "manual_import", "notes": "No public API. Export country/product tables from trademap.org (free registration) and import them."},
    "importyeti": {"mode": "manual_import", "notes": "US bills of lading only. No official API; import CSV exports from your account."},
    "volza": {"mode": "manual_import", "notes": "Paid. API exists only on enterprise plans; until a key and API docs are provided, import Excel exports."},
    "panjiva": {"mode": "manual_import", "notes": "S&P Global paid platform; import CSV/XLSX exports."},
    "importgenius": {"mode": "manual_import", "notes": "Paid; import CSV/XLSX exports."},
}

HS_TO_PRODUCTS = {
    "091030": ["turmeric_powder", "turmeric_fingers"],
    "080131": ["cashew_kernels"], "080132": ["cashew_kernels"],
    "090411": ["whole_spices"], "090831": ["whole_spices"], "090710": ["whole_spices"], "090611": ["whole_spices"],
    "090619": ["whole_spices"], "090931": ["whole_spices"], "090921": ["whole_spices"], "090961": ["whole_spices"],
}

# Column-name synonyms seen in Volza / Panjiva / ImportGenius / ImportYeti / Trade Map exports.
COLUMNS = {
    "importer": ["importer", "importer name", "consignee", "consignee name", "buyer", "buyer name", "company"],
    "importer_country": ["importer country", "consignee country", "destination country", "country of destination", "buyer country", "importing country"],
    "exporter": ["exporter", "exporter name", "shipper", "shipper name", "supplier", "supplier name"],
    "origin_country": ["origin country", "country of origin", "exporter country", "shipper country", "supplier country", "origin"],
    "hs_code": ["hs code", "hs", "hscode", "hs_code", "commodity code", "product code", "tariff code"],
    "description": ["product description", "goods description", "description", "item description", "commodity", "product"],
    "date": ["date", "shipment date", "arrival date", "bill of lading date", "b/l date", "import date"],
    "quantity": ["quantity", "qty", "net weight", "weight", "weight (kg)", "net weight (kg)", "std. quantity", "weight kg"],
    "unit": ["unit", "quantity unit", "uqc", "std. unit", "weight unit"],
    "value_usd": ["value usd", "total value usd", "value (usd)", "fob usd", "cif usd", "value", "total value", "usd value", "amount usd"],
}


def _norm_header(h: str) -> str:
    return re.sub(r"\s+", " ", str(h or "").strip().lower().replace("_", " "))


def read_table(path: Path) -> list[dict]:
    path = Path(path)
    if path.suffix.lower() in (".xlsx", ".xlsm"):
        from openpyxl import load_workbook
        ws = load_workbook(path, read_only=True, data_only=True).active
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            return []
        # header = first row with at least 3 non-empty cells
        hi = next(i for i, r in enumerate(rows) if sum(1 for c in r if c not in (None, "")) >= 3)
        hdr = [_norm_header(h) for h in rows[hi]]
        return [dict(zip(hdr, r)) for r in rows[hi + 1:] if any(c not in (None, "") for c in r)]
    with open(path, newline="", encoding="utf-8-sig") as f:
        return [{_norm_header(k): v for k, v in r.items()} for r in csv.DictReader(f)]


def map_columns(headers) -> dict:
    hs = set(headers)
    out = {}
    for field, names in COLUMNS.items():
        for n in names:
            if n in hs:
                out[field] = n
                break
    return out


def _num(v):
    if v in (None, ""):
        return None
    try:
        return float(str(v).replace(",", "").strip())
    except ValueError:
        return None


def _date(v) -> str:
    if v in (None, ""):
        return ""
    if hasattr(v, "strftime"):
        return v.strftime("%Y-%m-%d")
    s = str(v).strip()
    m = re.match(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", s)
    if m:
        return f"{m[1]}-{int(m[2]):02d}-{int(m[3]):02d}"
    m = re.match(r"(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})", s)
    if m:   # day-first, the usual format in these exports
        return f"{m[3]}-{int(m[2]):02d}-{int(m[1]):02d}"
    return s


def import_shipments(path: Path, source: str, default_country: str = "", research_date: str = "") -> dict:
    """Parse a shipment export into per-importer Company dicts (buyer_intel shape).
    Returns {'companies': [...], 'rows': n, 'skipped': n, 'mapping': {...}}."""
    if source not in INTEGRATIONS or INTEGRATIONS[source]["mode"] != "manual_import":
        raise ValueError(f"unknown manual-import source {source!r}")
    rows = read_table(path)
    if not rows:
        return {"companies": [], "rows": 0, "skipped": 0, "mapping": {}}
    mp = map_columns(rows[0].keys())
    if "importer" not in mp:
        raise ValueError(f"no importer/consignee column found; headers: {list(rows[0].keys())}")
    agg = defaultdict(lambda: {"n": 0, "qty": defaultdict(float), "value": 0.0, "dates": [], "origins": set(),
                               "descs": [], "hs": set()})
    skipped = 0
    for r in rows:
        imp = str(r.get(mp["importer"]) or "").strip()
        if not imp:
            skipped += 1
            continue
        hs = re.sub(r"\D", "", str(r.get(mp.get("hs_code", ""), "") or ""))[:6]
        desc = str(r.get(mp.get("description", ""), "") or "")
        products = HS_TO_PRODUCTS.get(hs) or [p for p in ("turmeric_powder", "turmeric_fingers", "cashew_kernels", "whole_spices")
                                              if forms.product_evidence(p, desc, "shipment_description")]
        if not products:
            skipped += 1
            continue
        country = str(r.get(mp.get("importer_country", ""), "") or default_country).strip()
        for p in products:
            a = agg[(imp.upper(), country, p)]
            a["name"] = imp
            a["n"] += 1
            q = _num(r.get(mp.get("quantity", "")))
            if q:
                a["qty"][str(r.get(mp.get("unit", ""), "") or "units").strip().upper()] += q
            v = _num(r.get(mp.get("value_usd", "")))
            if v:
                a["value"] += v
            d = _date(r.get(mp.get("date", "")))
            if d:
                a["dates"].append(d)
            o = str(r.get(mp.get("origin_country", ""), "") or "").strip()
            if o:
                a["origins"].add(o)
            if desc:
                a["descs"].append(desc)
            if hs:
                a["hs"].add(hs)

    companies = {}
    for (key, country, p), a in agg.items():
        descs = " | ".join(dict.fromkeys(a["descs"]))
        fe = forms.product_evidence(p, descs, "shipment_description") if descs else None
        ev = "shipment"
        form_ev = ""
        if p in ("turmeric_powder", "turmeric_fingers"):
            if fe and fe.get("form_evidence"):
                form_ev = "shipment_description"
            else:
                ev = "shipment_form_unknown"
                if fe is None and not a["hs"] & {"091030"}:
                    continue
            # a record that only proves the *other* turmeric form does not count for this one
            other = "fingers" if p == "turmeric_powder" else "powder"
            if not form_ev and forms.turmeric_forms(descs)[other]:
                continue
        vol = "; ".join(f"{q:,.1f} {u}" for u, q in a["qty"].items())
        pr = {"product": p, "evidence": ev, "form_evidence": form_ev, "shipment_count": a["n"],
              "shipment_volume": (vol + f" across {a['n']} shipments" if vol else ""),
              "last_shipment_date": max(a["dates"]) if a["dates"] else "",
              "supplier_countries": ", ".join(sorted(a["origins"])),
              "sources_from_india": True if any(o.lower() == "india" for o in a["origins"]) else None,
              "forms": (fe or {}).get("forms", "") + (f"; HS {', '.join(sorted(a['hs']))}" if a["hs"] else ""),
              "grades": (fe or {}).get("grades", ""),
              "evidence_text": f"{a['n']} shipment records in {source} export ({Path(path).name})"
                               + (f", value USD {a['value']:,.0f}" if a["value"] else "") + (f". Descriptions: {descs[:200]}" if descs else ""),
              "evidence_urls": [f"file:{Path(path).name}"]}
        cid = f"TRADE-{re.sub(r'[^A-Z0-9]+', '-', key)[:50]}-{country[:3].upper()}"
        c = companies.setdefault(cid, {"company_id": cid, "name": a["name"], "country": country, "products": [],
                                       "source_urls": [f"file:{Path(path).name}"], "research_date": research_date,
                                       "discovered_by": f"trade:{source}",
                                       "notes_verified": f"Imported from {source} export {Path(path).name}."})
        c["products"].append(pr)
    return {"companies": list(companies.values()), "rows": len(rows), "skipped": skipped, "mapping": mp}


# ---------------------------------------------------------------------------
# UN Comtrade
# ---------------------------------------------------------------------------
M49 = {"842": "USA", "699": "India", "276": "Germany", "458": "Malaysia", "504": "Morocco", "156": "China",
       "528": "Netherlands", "826": "United Kingdom", "682": "Saudi Arabia", "784": "United Arab Emirates", "392": "Japan",
       "124": "Canada", "251": "France", "76": "Brazil", "724": "Spain", "710": "South Africa", "360": "Indonesia",
       "36": "Australia", "788": "Tunisia", "512": "Oman", "792": "Turkey", "380": "Italy", "764": "Thailand",
       "616": "Poland", "376": "Israel", "757": "Switzerland", "203": "Czechia", "818": "Egypt", "586": "Pakistan",
       "608": "Philippines", "50": "Bangladesh", "704": "Vietnam", "414": "Kuwait", "634": "Qatar", "48": "Bahrain",
       "56": "Belgium", "208": "Denmark", "643": "Russia", "410": "South Korea", "702": "Singapore", "398": "Kazakhstan"}


class Comtrade:
    PREVIEW = "https://comtradeapi.un.org/public/v1/preview/C/A/HS"
    FULL = "https://comtradeapi.un.org/data/v1/get/C/A/HS"

    def __init__(self, cfg, fetcher):
        self.cfg, self.fetcher = cfg, fetcher
        self.key = cfg.keys.get("COMTRADE_KEY")

    def imports(self, hs: str, year: int, partner: str = "0") -> list[dict]:
        params = {"cmdCode": hs, "flowCode": "M", "period": str(year), "partnerCode": partner}
        headers = {"Ocp-Apim-Subscription-Key": self.key} if self.key else {}
        data = self.fetcher.get_json(self.FULL if self.key else self.PREVIEW, params=params, headers=headers)
        out = []
        for r in data.get("data", []):
            code = str(r.get("reporterCode"))
            out.append({"reporter_code": code, "country": r.get("reporterDesc") or M49.get(code, f"M49:{code}"),
                        "year": r.get("period") or year, "value_usd": r.get("primaryValue"),
                        "net_weight_kg": r.get("netWgt"), "partner": partner})
        return out

    def ranking(self, hs: str, year: int, product_label: str, top: int = 20) -> list[dict]:
        world = sorted((r for r in self.imports(hs, year, "0") if r["value_usd"]), key=lambda r: -r["value_usd"])[:top]
        try:
            india = {r["reporter_code"]: r for r in self.imports(hs, year, "699")}
        except FetchError:
            india = {}
        src = (self.FULL if self.key else self.PREVIEW) + f"?cmdCode={hs}&flowCode=M&period={year}"
        return [{"product": product_label, "hs": hs, "rank": i, "country": r["country"], "year": r["year"],
                 "import_value_usd_m": round(r["value_usd"] / 1e6, 2),
                 "import_tonnes": round(r["net_weight_kg"] / 1000, 1) if r.get("net_weight_kg") else None,
                 "imports_from_india_usd_m": round(india[r["reporter_code"]]["value_usd"] / 1e6, 2)
                 if r["reporter_code"] in india and india[r["reporter_code"]]["value_usd"] else "not stated",
                 "source": src} for i, r in enumerate(world, 1)]

    def test_connection(self) -> dict:
        try:
            rows = self.imports("091030", 2023, "0")
            return {"provider": "un_comtrade", "status": "connected", "mode": "full" if self.key else "public preview",
                    "rows": len(rows)}
        except FetchError as e:
            return {"provider": "un_comtrade", "status": "error", "error": str(e)}
