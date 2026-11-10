"""VVE International Buyer Finder - Global Importer Intelligence module.

Drop-in extension: it does not replace any existing buyer-finder logic. It adds
  * product / HS-code catalogue (turmeric powder, turmeric fingers, cashew, whole spices)
  * country ranking from trade statistics
  * company records with per-product relevance (one record per company, no duplicates)
  * deterministic 100-point lead score
  * verification status and buyer-size classification
  * per product x country reports, consolidated report, CSV and Excel exports

Input data lives in data/*.json (research results with source URLs).
Run:  python3 buyer_intel.py  -> writes output/
Rule: missing information never earns points. Nothing here invents data; every
score component is derived only from evidence fields present in the record.
"""
from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
OUT = ROOT / "output"

# --------------------------------------------------------------------------
# 1. Product catalogue
# --------------------------------------------------------------------------
PRODUCTS = {
    "turmeric_powder": {
        "label": "Turmeric Powder",
        "hs": {"091030": "Turmeric (curcuma) - ground form"},
        "buyer_types": ["bulk importer", "spice distributor", "food manufacturer", "private label brand"],
        "form": "ground",
    },
    "turmeric_fingers": {
        "label": "Turmeric Fingers (whole)",
        "hs": {"091030": "Turmeric (curcuma) - whole/dried fingers"},
        "buyer_types": ["bulk importer", "spice processor", "grinding company", "wholesaler"],
        "form": "whole",
    },
    "cashew_kernels": {
        "label": "Cashew Kernels",
        "hs": {"080131": "Cashew nuts, in shell", "080132": "Cashew nuts, shelled (kernels)"},
        "buyer_types": ["bulk kernel importer", "nut processor/packer", "wholesaler", "food manufacturer"],
        "form": "kernel",
    },
    "whole_spices": {
        "label": "Indian Whole Spices",
        "hs": {
            "090411": "Black pepper, neither crushed nor ground",
            "090831": "Cardamom, neither crushed nor ground",
            "090710": "Cloves, neither crushed nor ground",
            "090611": "Cinnamon (Cinnamomum zeylanicum), neither crushed nor ground",
            "090619": "Cinnamon, other, neither crushed nor ground",
            "090931": "Cumin seeds, neither crushed nor ground",
            "090921": "Coriander seeds, neither crushed nor ground",
            "090961": "Anise, badian (star anise), fennel, caraway, juniper seeds, neither crushed nor ground",
        },
        "buyer_types": ["bulk importer", "spice processor/packer", "wholesaler", "distributor"],
        "form": "whole",
    },
}
# Note: HS 0910.30 does not separate whole from ground turmeric at 6 digits, so
# national statistics cannot split powder vs fingers. The split is made per buyer
# from shipment descriptions / catalogues (field `forms` in product relevance).

# --------------------------------------------------------------------------
# 2. Data model
# --------------------------------------------------------------------------
@dataclass
class ProductRelevance:
    product: str                      # key of PRODUCTS
    evidence: str = ""                # shipment | shipment_form_unknown | catalogue_exact | category_only | ""
    forms: str = ""                   # whole / ground / kernel grades etc. as evidenced
    grades: str = ""                  # cashew grades if evidenced
    shipment_count: Optional[int] = None
    shipment_volume: str = ""         # as stated by source, with unit
    last_shipment_date: str = ""      # YYYY-MM or YYYY-MM-DD as stated by source
    supplier_countries: str = ""
    sources_from_india: Optional[bool] = None
    evidence_text: str = ""
    evidence_urls: list = field(default_factory=list)


@dataclass
class Company:
    company_id: str
    name: str
    country: str
    city: str = ""
    address: str = ""
    website: str = ""
    website_checked: bool = False     # site opened and identity confirmed
    business_category: str = ""       # importer | wholesaler | distributor | processor | manufacturer | retailer | trader
    distribution_network: str = ""
    general_email: str = ""
    purchasing_contact: str = ""
    sales_email: str = ""
    procurement_manager: str = ""
    phone: str = ""
    linkedin: str = ""
    contact_page: str = ""
    catalogue_url: str = ""
    independent_sources: list = field(default_factory=list)  # fair directory, registry, chamber, etc.
    products: list = field(default_factory=list)              # list[ProductRelevance]
    source_urls: list = field(default_factory=list)
    research_date: str = ""
    notes_verified: str = ""          # verified facts only
    notes_assessment: str = ""        # AI assessment, kept separate

    @staticmethod
    def from_dict(d: dict) -> "Company":
        d = dict(d)
        d["products"] = [ProductRelevance(**p) for p in d.get("products", [])]
        return Company(**d)


# --------------------------------------------------------------------------
# 3. Deterministic scoring (100 points). Missing info = 0.
# --------------------------------------------------------------------------
BUYER_TYPE_POINTS = {
    "importer": 20, "processor": 20, "wholesaler": 18, "distributor": 18,
    "manufacturer": 15, "packer": 15, "retailer": 10, "trader": 8,
}
PRODUCT_EVIDENCE_POINTS = {"shipment": 25, "shipment_form_unknown": 15, "catalogue_exact": 20, "category_only": 10}
SHIPMENT_EVIDENCE = ("shipment", "shipment_form_unknown")
# shipment_form_unknown: customs record under the right HS code, but whole vs ground
# (turmeric) or kernel grade cannot be told from the public record.


def _recent(date: str, research_date: str, months: int = 24) -> bool:
    m1 = re.match(r"(\d{4})-(\d{2})", date or "")
    m2 = re.match(r"(\d{4})-(\d{2})", research_date or "")
    if not (m1 and m2):
        return False
    a = int(m1[1]) * 12 + int(m1[2])
    b = int(m2[1]) * 12 + int(m2[2])
    return 0 <= b - a <= months


def score(c: Company, pr: ProductRelevance) -> dict:
    s = {}
    s["product_relevance"] = PRODUCT_EVIDENCE_POINTS.get(pr.evidence, 0)

    cat = (c.business_category or "").lower()
    s["buyer_type"] = max([v for k, v in BUYER_TYPE_POINTS.items() if k in cat], default=0)

    if pr.evidence in SHIPMENT_EVIDENCE and pr.last_shipment_date and _recent(pr.last_shipment_date, c.research_date):
        ia = 20
    elif pr.evidence in SHIPMENT_EVIDENCE:
        ia = 12
    elif "importer" in cat and c.independent_sources:
        ia = 6     # self-declared importer confirmed by an independent listing, no shipment data
    else:
        ia = 0
    s["import_activity"] = ia

    s["target_market"] = 10 if pr.sources_from_india else 0

    if c.website_checked and c.independent_sources:
        bv = 15
    elif c.website_checked:
        bv = 8
    elif c.independent_sources:
        bv = 4
    else:
        bv = 0
    s["business_verification"] = bv

    ca = 0
    if c.general_email or c.sales_email:
        ca += 4
    if c.phone:
        ca += 3
    if c.purchasing_contact or c.procurement_manager:
        ca += 3
    s["contact_availability"] = ca

    s["total"] = sum(s.values())
    s["priority"] = "High" if s["total"] >= 80 else "Medium" if s["total"] >= 60 else "Low"
    return s


def verification_status(c: Company, pr: ProductRelevance) -> str:
    checks = [c.website_checked, bool(c.independent_sources), pr.evidence in SHIPMENT_EVIDENCE + ("catalogue_exact",)]
    n = sum(checks)
    return "Verified" if n == 3 else "Partially verified" if n >= 1 else "Unverified"


def size_class(c: Company, pr: ProductRelevance) -> str:
    if pr.evidence in SHIPMENT_EVIDENCE and (pr.shipment_count or pr.shipment_volume):
        return "A. Large verified importer" if (pr.shipment_count or 0) >= 10 or pr.shipment_volume else "C. Potential bulk buyer"
    cat = (c.business_category or "").lower()
    if c.website_checked and c.distribution_network and any(k in cat for k in ("distributor", "wholesaler", "importer")):
        return "B. Established distributor"
    if c.website_checked and any(k in cat for k in ("importer", "wholesaler", "processor", "manufacturer", "packer")):
        return "C. Potential bulk buyer"
    return "D. Buyer requiring further verification"


# --------------------------------------------------------------------------
# 4. Duplicate handling
# --------------------------------------------------------------------------
_SUFFIX = re.compile(r"\b(l\.?l\.?c|fze|fzco|fzc|fz llc|co|company|trading|general trading|foodstuff|est|establishment|ltd|limited|inc|group|international|intl)\b\.?", re.I)


def norm_name(name: str) -> str:
    n = _SUFFIX.sub(" ", name.lower())
    return re.sub(r"[^a-z0-9]+", "", n)


def norm_domain(url: str) -> str:
    m = re.sub(r"^https?://(www\.)?", "", (url or "").lower()).split("/")[0]
    return m


def dedupe(companies: list[Company]) -> list[Company]:
    by_key: dict[str, Company] = {}
    for c in companies:
        key = (norm_domain(c.website) or norm_name(c.name)) + "|" + c.country.lower()
        if key in by_key:
            base = by_key[key]
            have = {p.product for p in base.products}
            base.products += [p for p in c.products if p.product not in have]
            base.source_urls = sorted(set(base.source_urls + c.source_urls))
            base.independent_sources = sorted(set(base.independent_sources + c.independent_sources))
            for f in ("general_email", "sales_email", "purchasing_contact", "phone", "linkedin", "address", "city"):
                if not getattr(base, f) and getattr(c, f):
                    setattr(base, f, getattr(c, f))
        else:
            by_key[key] = c
    return list(by_key.values())


# --------------------------------------------------------------------------
# 5. Reports
# --------------------------------------------------------------------------
COLUMNS = [
    "S.No", "Product", "Country", "Company name", "Official website", "Business category",
    "Public business email", "Purchasing department contact", "Telephone", "LinkedIn",
    "Import activity evidence", "Shipment volume", "Last shipment date", "Supplier countries",
    "Buyer size classification", "Lead score", "Priority", "Verification status", "Source URLs",
    "Research date", "Product form / grades", "Score breakdown", "AI assessment (not verified)",
]


def rows_for(companies: list[Company]) -> list[dict]:
    rows = []
    for c in companies:
        for pr in c.products:
            sc = score(c, pr)
            rows.append({
                "Product": PRODUCTS[pr.product]["label"],
                "_product": pr.product,
                "Country": c.country,
                "Company name": c.name,
                "Official website": c.website,
                "Business category": c.business_category,
                "Public business email": c.general_email or c.sales_email,
                "Purchasing department contact": c.purchasing_contact or c.procurement_manager,
                "Telephone": c.phone,
                "LinkedIn": c.linkedin,
                "Import activity evidence": pr.evidence_text,
                "Shipment volume": pr.shipment_volume or (f"{pr.shipment_count} shipments" if pr.shipment_count else ""),
                "Last shipment date": pr.last_shipment_date,
                "Supplier countries": pr.supplier_countries,
                "Buyer size classification": size_class(c, pr),
                "Lead score": sc["total"],
                "Priority": sc["priority"],
                "Verification status": verification_status(c, pr),
                "Source URLs": " | ".join(dict.fromkeys(pr.evidence_urls + c.source_urls)),
                "Research date": c.research_date,
                "Product form / grades": "; ".join(x for x in (pr.forms, pr.grades) if x),
                "Score breakdown": ", ".join(f"{k}={v}" for k, v in sc.items() if k not in ("total", "priority")),
                "AI assessment (not verified)": c.notes_assessment,
            })
    rows.sort(key=lambda r: (r["_product"], r["Country"], -r["Lead score"], r["Company name"]))
    return rows


def write_csv(path: Path, rows: list[dict]):
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
        w.writeheader()
        for i, r in enumerate(rows, 1):
            w.writerow({**r, "S.No": i})


def load_json(name: str):
    p = DATA / name
    return json.loads(p.read_text()) if p.exists() else []


def build():
    OUT.mkdir(exist_ok=True)
    companies = dedupe([Company.from_dict(d) for f in sorted(DATA.glob("buyers_*.json")) for d in json.loads(f.read_text())])
    rows = rows_for(companies)
    rankings = load_json("country_rankings.json")   # list of dicts per product/country
    limits = load_json("research_log.json")

    # per product x country CSVs
    per = OUT / "by_product_country"
    per.mkdir(exist_ok=True)
    groups: dict[tuple, list] = {}
    for r in rows:
        groups.setdefault((r["_product"], r["Country"]), []).append(r)
    for (p, ctry), rs in groups.items():
        write_csv(per / f"{p}__{ctry.replace(' ', '_')}.csv", rs)

    write_csv(OUT / "consolidated_buyers.csv", rows)

    # Excel workbook
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    ws.title = "Summary"
    hdr = Font(bold=True, color="FFFFFF")
    fill = PatternFill("solid", fgColor="1F4E78")

    def table(ws, header, data, start_row=1):
        for j, h in enumerate(header, 1):
            c = ws.cell(row=start_row, column=j, value=h)
            c.font, c.fill = hdr, fill
            c.alignment = Alignment(wrap_text=True, vertical="top")
        for i, row in enumerate(data, start_row + 1):
            for j, v in enumerate(row, 1):
                ws.cell(row=i, column=j, value=v).alignment = Alignment(wrap_text=True, vertical="top")
        for j in range(1, len(header) + 1):
            ws.column_dimensions[get_column_letter(j)].width = 22
        ws.freeze_panes = ws.cell(row=start_row + 1, column=1)

    summ = []
    for p, meta in PRODUCTS.items():
        pr_rows = [r for r in rows if r["_product"] == p]
        countries = sorted({r["Country"] for r in pr_rows})
        for ctry in countries or ["-"]:
            rs = [r for r in pr_rows if r["Country"] == ctry]
            summ.append([meta["label"], ctry, len(rs), 10, sum(r["Verification status"] == "Verified" for r in rs),
                         sum(r["Priority"] == "High" for r in rs), sum(r["Priority"] == "Medium" for r in rs)])
    table(ws, ["Product", "Country", "Buyers found", "Target", "Verified", "High priority", "Medium priority"], summ)

    rk = wb.create_sheet("Country ranking")
    if rankings:
        keys = list(rankings[0].keys())
        table(rk, keys, [[r.get(k) for k in keys] for r in rankings])

    for p, meta in PRODUCTS.items():
        s = wb.create_sheet(meta["label"][:31])
        prs = [r for r in rows if r["_product"] == p]
        table(s, COLUMNS, [[i if k == "S.No" else r.get(k, "") for k in COLUMNS] for i, r in enumerate(prs, 1)])

    cs = wb.create_sheet("Companies (deduplicated)")
    table(cs, ["Company", "Country", "City", "Website", "Products", "Category", "Email", "Phone", "LinkedIn", "Address", "Verified facts"],
          [[c.name, c.country, c.city, c.website, ", ".join(PRODUCTS[p.product]["label"] for p in c.products),
            c.business_category, c.general_email or c.sales_email, c.phone, c.linkedin, c.address, c.notes_verified] for c in companies])

    if limits:
        lg = wb.create_sheet("Sources & limits")
        keys = list(limits[0].keys())
        table(lg, keys, [[r.get(k) for k in keys] for r in limits])

    wb.save(OUT / "VVE_Global_Buyer_Intelligence.xlsx")
    print(f"{len(companies)} companies, {len(rows)} product-buyer rows, {len(groups)} product-country lists")


if __name__ == "__main__":
    build()
