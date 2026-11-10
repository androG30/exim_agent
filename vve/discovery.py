"""Web discovery: product x country queries, result triage, company research.

Pipeline for one candidate:
  search result -> (trade-fair page -> official site link) -> official site
  -> home + contact/about/products pages -> facts with source URLs
  -> genuineness checks -> Company record in the buyer_intel structure.
Nothing is inferred that a page does not state. Emails are only taken as printed
on the company's own pages.
"""
from __future__ import annotations

import re
import urllib.parse
from datetime import date

from . import extract, forms
from .http import FetchError

PRODUCT_QUERIES = {
    "turmeric_powder": ['"turmeric powder" importer {country}', '"turmeric powder" wholesale supplier {city}',
                        'spices powder grinding packing company {country}', 'Indian spices importer distributor {country} turmeric',
                        'site:gulfood.com/exhibitors spices {country}', 'private label spices manufacturer {country} turmeric powder'],
    "turmeric_fingers": ['"turmeric fingers" importer {country}', '"whole turmeric" wholesaler {country}',
                         'spice grinding mill {country} raw turmeric', 'whole spices importer {city} turmeric finger',
                         'site:gulfood.com/exhibitors whole spices {country}'],
    "cashew_kernels": ['"cashew kernels" importer {country}', 'cashew W320 W240 importer {country}',
                       'dry fruits nuts importer wholesaler {city} cashew', 'nuts processing packing company {country} cashew',
                       'site:gulfood.com/exhibitors nuts cashew {country}'],
    "whole_spices": ['whole spices importer {country}', 'black pepper cardamom importer {city}',
                     'cumin seeds coriander seeds wholesaler {country}', 'cloves cinnamon star anise importer {country}',
                     'Indian whole spices distributor {country}', 'site:gulfood.com/exhibitors spices {country}'],
}

COUNTRY_TERMS = {
    "United Arab Emirates": (["uae", "u.a.e", "united arab emirates", "dubai", "abu dhabi", "sharjah", "ajman", "jebel ali",
                              "ras al khaimah", "fujairah", "umm al quwain", "deira", "jafza", "dmcc"], ".ae", "Dubai"),
    "USA": (["usa", "u.s.a", "united states", "new york", "new jersey", "california", "texas", "illinois", "florida"], ".us", "New York"),
    "United Kingdom": (["united kingdom", "uk", "england", "london", "birmingham", "manchester", "leicester"], ".uk", "London"),
    "Germany": (["germany", "deutschland", "hamburg", "bremen", "berlin", "munich"], ".de", "Hamburg"),
    "Netherlands": (["netherlands", "holland", "rotterdam", "amsterdam"], ".nl", "Rotterdam"),
    "Saudi Arabia": (["saudi arabia", "ksa", "riyadh", "jeddah", "dammam"], ".sa", "Jeddah"),
    "Malaysia": (["malaysia", "kuala lumpur", "penang", "selangor", "klang"], ".my", "Kuala Lumpur"),
}

# Domains that are never a company's own website.
AGGREGATORS = re.compile(r"(^|\.)(tridge|volza|panjiva|importyeti|importgenius|tradeint|alibaba|aliexpress|indiamart|tradekey|"
                         r"go4worldbusiness|exportersindia|freshdi|globallinker|tradologie|foodcodirectory|bestfoodimporters|"
                         r"importkey|nutrada|ensun|kompass|zoominfo|dnb|yellowpages|yelp|linkedin|facebook|instagram|twitter|x|"
                         r"youtube|wikipedia|amazon|noon|talabat|desertcart|carrefouruae|naukrigulf|glassdoor|prospeo|"
                         r"rocketreach|apollo|crunchbase|made-in-china|ec21|tradeindia|exportgenius|seair|zauba|"
                         r"trendeconomy|worldstopexports|trademap|comtrade|oec|statista|medium|blogspot|wordpress|"
                         r"google|bing|duckduckgo|macro|leadstal|village|commonshare|usetorg|eximnext)\.[a-z.]+$", re.I)
TRADE_FAIRS = re.compile(r"(gulfood(manufacturing)?\.com|anuga\.com|sialparis\.com|aahar|worldspicecongress|"
                         r"thesaudifoodshow\.com|ift\.org|biofach)", re.I)
# Independent sources that corroborate identity (not the company's own site)
INDEPENDENT = re.compile(r"(gulfood|anuga|sialparis|aahar|worldspicecongress|thesaudifoodshow|chamber|\.gov\.|\.gov$|"
                         r"gulfnews\.com|khaleejtimes\.com|zawya\.com|thenationalnews\.com|panjiva\.com|importyeti\.com|"
                         r"indianspices\.com|commerce\.gov\.in|opencorporates\.com|companieshouse)", re.I)
CUSTOMS_PROFILES = re.compile(r"(importyeti\.com/company|panjiva\.com/|volza\.com/company|importgenius\.com/importers)", re.I)


def host(url: str) -> str:
    return urllib.parse.urlsplit(url).netloc.lower().removeprefix("www.")


def queries_for(product: str, country: str, limit: int) -> list[str]:
    city = COUNTRY_TERMS.get(country, ([], "", country))[2]
    return [q.format(country=country, city=city) for q in PRODUCT_QUERIES[product]][:limit]


def triage(result: dict) -> str:
    """official | trade_fair | customs_profile | aggregator"""
    h = host(result.get("url", ""))
    if TRADE_FAIRS.search(h + urllib.parse.urlsplit(result.get("url", "")).path):
        return "trade_fair"
    if CUSTOMS_PROFILES.search(result.get("url", "")):
        return "customs_profile"
    if not h or AGGREGATORS.search(h):
        return "aggregator"
    return "official"


def in_country(text: str, url: str, country: str) -> str:
    terms, tld, _ = COUNTRY_TERMS.get(country, ([country.lower()], "", country))
    if tld and host(url).endswith(tld):
        return f"domain {tld}"
    low = (text or "").lower()
    for t in terms:
        if re.search(r"(?<![a-z])" + re.escape(t) + r"(?![a-z])", low):
            return t
    return ""


def company_name(html: str, fallback: str) -> str:
    n = extract.og_site_name(html)
    if not n:
        t = extract.title(html)
        parts = [p.strip() for p in re.split(r"\s[|\-–—:]\s", t) if p.strip()]
        n = max(parts, key=lambda p: bool(re.search(r"(llc|l\.l\.c|trading|foodstuff|fz|dmcc|ltd|inc|co\b|group|spice|nuts?)", p, re.I))) if parts else ""
    return (n or fallback).strip()[:120]


def official_link_from_fair_page(html: str, page_url: str) -> str:
    """Trade-fair exhibitor pages link the exhibitor's own website."""
    for url, label in extract.links(html, page_url):
        h = host(url)
        if not h or TRADE_FAIRS.search(h) or AGGREGATORS.search(h):
            continue
        if re.search(r"(website|visit|www\.|http)", label, re.I) or re.match(r"https?://(www\.)?[a-z0-9-]+\.[a-z.]+/?$", url):
            return url
    return ""


class Researcher:
    def __init__(self, cfg, fetcher, store=None, provider=None, today: str | None = None):
        self.cfg, self.fetcher, self.store, self.provider = cfg, fetcher, store, provider
        self.today = today or date.today().isoformat()

    def research(self, candidate: dict, product: str, country: str, job_id=None) -> dict:
        """candidate: {'url', 'via': 'search'|'trade_fair'|'seed', 'source_url', 'name'}.
        Returns {'status': 'accepted'|'rejected', 'reason', 'company': dict|None}."""
        url = candidate["url"]
        independent = [candidate["source_url"]] if candidate.get("via") == "trade_fair" else []
        try:
            home = self.fetcher.get_page(url)
        except FetchError as e:
            return {"status": "failed", "reason": f"website not reachable: {e}", "company": None}
        site = host(home["final_url"] or url)
        pages = {"home": (home["final_url"] or url, home["body"])}
        for kind, u in extract.classify_page_links(extract.links(home["body"], pages["home"][0]), site).items():
            if len(pages) >= self.cfg.max_pages_per_company:
                break
            try:
                pages[kind] = (u, self.fetcher.get_page(u)["body"])
            except FetchError:
                continue
        texts = {k: extract.strip_tags(b) for k, (u, b) in pages.items()}
        alltext = " ".join(texts.values())
        name = company_name(home["body"], candidate.get("name") or site)

        where = in_country(alltext, url, country)
        if not where:
            return {"status": "rejected", "reason": f"no evidence the company is in {country}", "company": None}
        ev = None
        ev_url = ""
        for k in ("products", "home", "about", "contact"):
            if k in texts:
                e = forms.product_evidence(product, texts[k], "catalogue")
                if e and (not ev or forms_rank(e) > forms_rank(ev)):
                    ev, ev_url = e, pages[k][0]
        if not ev:
            return {"status": "rejected", "reason": f"no {product} evidence on the company website", "company": None}
        role = forms.roles(alltext)
        if not role:
            return {"status": "rejected", "reason": "no trade role (importer/wholesaler/distributor/processor) stated", "company": None}

        all_html = " ".join(b for _, b in pages.values())
        mails = extract.emails(all_html, site)
        own = [m for m in mails if m.split("@")[1].endswith(site)] or mails
        general = next((m for m in own if not re.match(r"(sales|purchas|procure|buy|import)", m)), own[0] if own else "")
        sales = next((m for m in own if m.startswith("sales")), "")
        purch = next((m for m in own if re.match(r"(purchas|procure|buy|import)", m)), "")
        contact_text = texts.get("contact", texts["home"])
        ph = extract.phones(contact_text) or extract.phones(alltext)
        li = extract.linkedin_company(extract.links(all_html, url))
        india = forms.india_sourcing(alltext)

        pr = dict(ev)
        pr.update(evidence_urls=[ev_url], sources_from_india=True if india else None,
                  supplier_countries="India (stated)" if india else "", form_evidence_url=ev_url if ev.get("form_evidence") else "")
        if india:
            pr["evidence_text"] = (pr.get("evidence_text", "") + " | India sourcing: " + india[:160]).strip(" |")

        # independent corroboration: second search for the company name
        if self.provider:
            try:
                for r in self.provider.search(f'"{name}" {country}', country, 10):
                    h = host(r["url"])
                    if h.endswith(site):
                        continue
                    if INDEPENDENT.search(h):
                        independent.append(r["url"])
                    if CUSTOMS_PROFILES.search(r["url"]):
                        se = forms.product_evidence(product, r.get("title", "") + " " + r.get("snippet", ""), "shipment_description")
                        if se and forms_rank(se) >= forms_rank(pr) - 1:
                            pr.update(evidence=se["evidence"], evidence_urls=pr["evidence_urls"] + [r["url"]],
                                      evidence_text=pr.get("evidence_text", "") + " | Customs profile: " + r.get("snippet", "")[:160])
                            if se.get("form_evidence"):
                                pr.update(form_evidence=se["form_evidence"], form_evidence_url=r["url"])
            except Exception:
                pass

        company = {
            "company_id": f"{(COUNTRY_TERMS.get(country, ([], '', ''))[1] or '.xx').strip('.').upper()}-{site}",
            "name": name, "country": country, "city": where.title() if where not in ("uae", "u.a.e") else "",
            "website": f"https://{site}/", "website_checked": True,
            "business_category": ", ".join(role), "general_email": general, "sales_email": sales,
            "purchasing_contact": purch, "phone": ph[0] if ph else "", "linkedin": li,
            "contact_page": pages["contact"][0] if "contact" in pages else "",
            "catalogue_url": pages["products"][0] if "products" in pages else "",
            "independent_sources": sorted(set(independent)), "products": [pr],
            "source_urls": [u for u, _ in pages.values()], "research_date": self.today,
            "notes_verified": f"Location evidence: '{where}'. Roles stated on site: {', '.join(role)}.",
            "notes_assessment": "", "discovered_by": f"search:{self.provider.name}" if self.provider else "seed",
        }
        if self.store:
            key = company["company_id"]
            for k, (u, b) in pages.items():
                self.store.add_evidence(key, u, f"page:{k}", texts[k][:500], b, job_id)
        return {"status": "accepted", "reason": "", "company": company}


def forms_rank(e: dict) -> int:
    order = {"": 0, "category_only": 1, "shipment_form_unknown": 2, "catalogue_exact": 3, "shipment": 4}
    return order.get(e.get("evidence", ""), 0) + (1 if e.get("form_evidence") else 0)
