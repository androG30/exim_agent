import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from buyer_intel import Company, ProductRelevance, score, verification_status, dedupe


def test_missing_info_scores_zero():
    c = Company(company_id="x", name="X", country="UAE", research_date="2026-11-10")
    pr = ProductRelevance(product="cashew_kernels")
    assert score(c, pr)["total"] == 0
    assert verification_status(c, pr) == "Unverified"


def test_full_evidence_scores_100():
    c = Company(company_id="x", name="X", country="UAE", website="https://x.ae", website_checked=True,
                business_category="importer", general_email="info@x.ae", phone="1", purchasing_contact="p",
                independent_sources=["u"], research_date="2026-11-10")
    pr = ProductRelevance(product="cashew_kernels", evidence="shipment", last_shipment_date="2026-07",
                          sources_from_india=True)
    s = score(c, pr)
    assert s["total"] == 100 and s["priority"] == "High"


def test_dedupe_merges_products():
    a = Company(company_id="a", name="Spice Co LLC", country="UAE", website="https://spice.ae",
                products=[ProductRelevance(product="turmeric_powder")])
    b = Company(company_id="b", name="Spice Co", country="UAE", website="http://www.spice.ae/about",
                products=[ProductRelevance(product="cashew_kernels")])
    out = dedupe([a, b])
    assert len(out) == 1 and {p.product for p in out[0].products} == {"turmeric_powder", "cashew_kernels"}
