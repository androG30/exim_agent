# VVE International Buyer Finder: Global Importer Intelligence

Status (10 Nov 2026): discovery agent implemented and tested offline; **no live search provider is connected yet** (no API key). UAE research is a partial first pass. **Global research is NOT complete.**

## Autonomous discovery agent (`vve/`)
```
python -m vve providers                      # live connection test of every integration
python -m vve research "Find 10 genuine turmeric powder importers in UAE"
python -m vve status [JOB] | retry JOB | history [JOB]
python -m vve import-trade volza export.xlsx --country "United Arab Emirates"
python -m vve rankings 091030 2023 "Turmeric"   # UN Comtrade -> data/country_rankings.json
python -m vve campaign --top 5                  # product x country job plan from trade data
python -m vve report                            # rebuild CSV/Excel
```
A research job: builds product-specific queries -> calls the configured search API -> drops marketplaces and data vendors, follows trade-fair exhibitor pages to the exhibitor's own site -> opens the official site plus contact/about/products pages (robots.txt respected, per-host delay, cache) -> extracts location, trade role, product and product-form evidence, India sourcing, printed emails/phones, LinkedIn -> searches the company name for independent corroboration (trade fairs, chambers, press, customs profiles) -> rejects candidates with no country, product or trade-role evidence -> merges into `data/buyers_<country>.json` without duplicates -> writes `output/jobs/job_<id>_*.md` with accepted, rejected and failed items. Known companies for the country are re-researched (revalidated) in every job. Search results and pages are cached in `data/state/research.db`, which also holds jobs, tasks, evidence (URL, excerpt, SHA-256, fetch time) and history.

### Configuration
Keys are read from environment variables only (see `vve/config.py`). Optional `config.json` (see `config.example.json`) sets limits.

| Integration | Mode | Status here |
|---|---|---|
| Google Programmable Search | API (`GOOGLE_CSE_KEY`, `GOOGLE_CSE_CX`) | Awaiting credentials (endpoint reachable) |
| SerpApi | API (`SERPAPI_KEY`) | Awaiting credentials (host blocked in this cloud sandbox) |
| Brave Search | API (`BRAVE_SEARCH_KEY`) | Awaiting credentials (host blocked in this cloud sandbox) |
| UN Comtrade | API (public preview, or `COMTRADE_KEY`) | Implemented; host blocked in this cloud sandbox |
| ImportYeti, Volza, Panjiva, ImportGenius, ITC Trade Map | CSV/XLSX import of your own exports | Implemented |

### Product-form rule (turmeric)
HS 0910.30 covers whole and ground turmeric. A record under 0910.30 alone is `shipment_form_unknown` (15 points, not exact). Exact product evidence for powder or fingers needs the form named in a company catalogue (`form_evidence: catalogue`) or a shipment goods description (`form_evidence: shipment_description`).

### Buyer classes
A. Confirmed large importer: import record with volume, 10+ shipments, or a customs-derived importer ranking. B. Confirmed importer, volume unknown: import record only. C. Established distributor / wholesaler: checked website, trade role, and independent or distribution evidence. D. Potential buyer requiring verification: everything else.

## What is here
- `buyer_intel.py`: scoring/reporting core (kept; extended with the form rule and new classes). It holds the product/HS catalogue, the 100-point deterministic score, the verification status, the A–D size class, dedupe across products, and the CSV and Excel exports. Run `python3 buyer_intel.py`.
- `data/`: research inputs, each with source URLs (`buyers_uae.json`, `uae_trade_stats.json`, `country_rankings.json`, `turmeric_trend.json`, `research_log.json`).
- `output/VVE_Global_Buyer_Intelligence.xlsx`: Summary, Country ranking, one sheet per product, deduplicated companies, and Sources & limits.
- `output/consolidated_buyers.csv` and `output/by_product_country/*.csv`.

## Scoring (missing information = 0 points)
| Component | Max | Rule |
|---|---|---|
| Product relevance | 25 | Shipment record naming the exact product/form 25; exact product/form in own catalogue 20; shipment under right HS but form unknown 15; category only 10 |
| Buyer type | 20 | Importer/processor 20; wholesaler/distributor 18; manufacturer/packer 15; retailer 10; trader 8 |
| Import activity | 20 | Import record or customs ranking ≤24 months old 20; older/undated 12; self-declared importer confirmed by independent listing 6 |
| Target market | 10 | Evidence of sourcing from India 10 |
| Business verification | 15 | Official site checked + independent source 15; site only 8; listing only 4 |
| Contact availability | 10 | Public email 4, phone 3, purchasing contact 3 |

Verification: Verified = site checked + independent source + exact product evidence (with form evidence for turmeric); Partially verified = 1–2 of these; Unverified = none.

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
