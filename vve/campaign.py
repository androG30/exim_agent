"""Country research campaign: rank import markets per product from trade
statistics, then plan one research job per product x country."""
from __future__ import annotations

import json
from pathlib import Path

# Which ranking rows feed which product. Whole spices combine several HS codes.
PRODUCT_RANK_KEYS = {
    "turmeric_powder": ["091030"], "turmeric_fingers": ["091030"],
    "cashew_kernels": ["080132"],
    "whole_spices": ["090411", "090831", "090710", "090611", "090619", "090931", "090921", "090961",
                     "0908", "0907", "0906", "0909"],   # 4-digit headings used where 6-digit data is missing
}
LABELS = {"turmeric_powder": "turmeric powder", "turmeric_fingers": "turmeric fingers",
          "cashew_kernels": "cashew kernels", "whole_spices": "Indian whole spices"}
EXCLUDE = {"India"}   # the exporter's own market


def plan(data_dir: Path, top: int = 5, target: int = 10) -> list[dict]:
    rows = json.loads((Path(data_dir) / "country_rankings.json").read_text())
    out = []
    for product, codes in PRODUCT_RANK_KEYS.items():
        totals: dict[str, dict] = {}
        for r in rows:
            if r["hs"] in codes and r["country"] not in EXCLUDE:
                t = totals.setdefault(r["country"], {"value": 0.0, "codes": set(), "india": 0.0, "years": set()})
                t["value"] += float(r["import_value_usd_m"] or 0)
                t["codes"].add(r["hs"])
                t["years"].add(r["year"])
                if isinstance(r.get("imports_from_india_usd_m"), (int, float)):
                    t["india"] += r["imports_from_india_usd_m"]
        ranked = sorted(totals.items(), key=lambda kv: -kv[1]["value"])[:top]
        missing = [c for c in codes if not any(r["hs"] == c for r in rows)]
        for i, (country, t) in enumerate(ranked, 1):
            out.append({"product": product, "rank": i, "country": country, "import_value_usd_m": round(t["value"], 2),
                        "imports_from_india_usd_m": round(t["india"], 2) or "not stated",
                        "hs_codes_ranked": ", ".join(sorted(t["codes"])), "years": ", ".join(map(str, sorted(t["years"]))),
                        "hs_codes_without_data": ", ".join(missing),
                        "request": f"Find {target} genuine {LABELS[product]} importers in {country}"})
    return out


def save_plan(data_dir: Path, top=5, target=10) -> Path:
    p = Path(data_dir) / "campaign.json"
    p.write_text(json.dumps(plan(data_dir, top, target), indent=1))
    return p
