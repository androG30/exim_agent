# VVE International Buyer Finder: Global Importer Intelligence (UAE test)

Research date: 10 Nov 2026. Status: **UAE test run, first pass. Global research NOT complete.**

## What is here
- `buyer_intel.py`: the extension module. It holds the product/HS catalogue, the 100-point deterministic score, the verification status, the A–D size class, dedupe across products, and the CSV and Excel exports. Run `python3 buyer_intel.py`.
- `data/`: research inputs, each with source URLs (`buyers_uae.json`, `uae_trade_stats.json`, `country_rankings.json`, `turmeric_trend.json`, `research_log.json`).
- `output/VVE_Global_Buyer_Intelligence.xlsx`: Summary, Country ranking, one sheet per product, deduplicated companies, and Sources & limits.
- `output/consolidated_buyers.csv` and `output/by_product_country/*.csv`.

## Scoring (missing information = 0 points)
| Component | Max | Rule |
|---|---|---|
| Product relevance | 25 | Shipment record for exact product 25; shipment under right HS but form unknown 15; exact product in own catalogue 20; category only 10 |
| Buyer type | 20 | Importer/processor 20; wholesaler/distributor 18; manufacturer/packer 15; retailer 10; trader 8 |
| Import activity | 20 | Shipment ≤24 months old 20; older/undated shipment 12; self-declared importer confirmed by independent listing 6 |
| Target market | 10 | Evidence of sourcing from India 10 |
| Business verification | 15 | Official site checked + independent source 15; site only 8; listing only 4 |
| Contact availability | 10 | Public email 4, phone 3, purchasing contact 3 |

Verification: Verified = site checked + independent source + exact product evidence; Partially verified = 1–2 of these; Unverified = none.

## UAE trade picture (2023, latest available)
- Cashew kernels 080132: USD 210M, #5 world importer, about half from India (USD 105M).
- Black pepper whole 090411: USD 79M, #3 world importer; India 24% of heading 0904.
- Cardamom (heading 0908): USD 133M; India 29%, Guatemala 66%.
- Cumin/coriander/fennel/anise (0909): USD ~90M; India 50%.
- Cloves 090710: USD 43.5M; India 9%.
- Turmeric 091030: USD 10.1M (USD 33.7M in 2025 per worldstopexports); India USD 9.6M.
- Cinnamon (0906): USD 15M, mostly China/Vietnam; India negligible.

## Limits
- No access to Volza, Panjiva, ImportGenius, ITC Trade Map, TradeInt full records, Tridge Eye, Hunter, Apollo, Snov, RocketReach or NeverBounce. Only free public pages were used. Shipment volumes are therefore mostly missing.
- The UN Comtrade API and WITS were unusable from this environment. TrendEconomy (Comtrade-based) gave 2023 data.
- HS 0910.30 does not separate turmeric powder from fingers, so the form comes from company evidence only.
- Web page fetching is capped at about 100 pages per hour, which limits research speed.
- No email was guessed. Personal mobile numbers and named staff emails were left out.

## Running
```
pip install -r requirements.txt
python3 buyer_intel.py        # rebuilds output/ from data/
python3 -m pytest -q tests    # scoring and dedupe tests
```
To add a country, put its researched records in `data/buyers_<country>.json` (same shape as `buyers_uae.json`) and rerun.
