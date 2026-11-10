import json
import buyer_intel as bi
from buyer_intel import Company, ProductRelevance as PR
from vve import forms
from vve.jobs import merge_save, parse_request


def co(**kw):
    base = dict(company_id="x", name="X Trading LLC", country="United Arab Emirates", research_date="2026-11-10")
    base.update(kw)
    return Company(**base)


# ---- turmeric product-form rule -------------------------------------------
def test_hs_091030_alone_is_not_exact_for_powder_or_fingers():
    for p in ("turmeric_powder", "turmeric_fingers"):
        pr = PR(product=p, evidence="shipment", forms="HS 091030")
        assert bi.effective_evidence(pr) == "shipment_form_unknown"
        assert not bi.exact_product_confirmed(pr)
        c = co(website_checked=True, independent_sources=["u"])
        assert bi.verification_status(c, pr) == "Partially verified"


def test_turmeric_catalogue_without_form_is_category_only():
    pr = PR(product="turmeric_powder", evidence="catalogue_exact")
    assert bi.effective_evidence(pr) == "category_only"
    assert bi.score(co(), pr)["product_relevance"] == 10


def test_turmeric_with_form_evidence_is_exact():
    pr = PR(product="turmeric_fingers", evidence="catalogue_exact", form_evidence="catalogue")
    assert bi.effective_evidence(pr) == "catalogue_exact"
    pr2 = PR(product="turmeric_powder", evidence="shipment_form_unknown", form_evidence="shipment_description")
    assert bi.effective_evidence(pr2) == "shipment"
    # HS-only customs record + catalogue naming the form: exact via catalogue, import record still counts
    pr3 = PR(product="turmeric_powder", evidence="shipment_form_unknown", form_evidence="catalogue", last_shipment_date="2026-07")
    assert bi.effective_evidence(pr3) == "catalogue_exact" and bi.has_import_record(pr3)
    s3 = bi.score(co(), pr3)
    assert s3["product_relevance"] == 20 and s3["import_activity"] == 20


def test_form_detection_distinguishes_powder_and_fingers():
    assert forms.product_evidence("turmeric_fingers", "Turmeric fingers (Salem, polished)")["form_evidence"] == "catalogue"
    assert forms.product_evidence("turmeric_powder", "Turmeric fingers (Salem, polished)")["form_evidence"] == ""
    assert forms.product_evidence("turmeric_powder", "Ground turmeric 25kg bags")["evidence"] == "catalogue_exact"
    assert forms.product_evidence("turmeric_powder", "TURMERIC HS 091030", "shipment_description")["evidence"] == "shipment_form_unknown"
    assert forms.product_evidence("turmeric_powder", "chilli powder only") is None


def test_whole_vs_ground_spices():
    ws = forms.whole_spices("Black pepper powder, cumin seeds, green cardamom 8mm")
    assert ws["black pepper"]["whole"] is False and ws["cumin"]["whole"] and ws["cardamom"]["whole"]


# ---- missing information --------------------------------------------------
def test_missing_information_scores_zero_everywhere():
    s = bi.score(co(), PR(product="whole_spices"))
    assert s["total"] == 0 and all(v == 0 for k, v in s.items() if k not in ("total", "priority"))
    assert bi.verification_status(co(), PR(product="whole_spices")) == "Unverified"
    assert bi.size_class(co(), PR(product="whole_spices")) == bi.SIZE_D


def test_unknown_india_sourcing_gets_no_target_points():
    assert bi.score(co(), PR(product="cashew_kernels", sources_from_india=None))["target_market"] == 0


# ---- classification -------------------------------------------------------
def test_importer_classification():
    c = co(website_checked=True, business_category="importer, wholesaler", independent_sources=["u"])
    assert bi.size_class(c, PR(product="cashew_kernels", evidence="shipment", shipment_volume="120 MT")) == bi.SIZE_A
    assert bi.size_class(c, PR(product="cashew_kernels", evidence="shipment", shipment_count=14)) == bi.SIZE_A
    assert bi.size_class(c, PR(product="whole_spices", evidence="shipment", ranking_evidence="#4, 5.4% share")) == bi.SIZE_A
    assert bi.size_class(c, PR(product="cashew_kernels", evidence="shipment", shipment_count=2)) == bi.SIZE_B
    assert bi.size_class(c, PR(product="cashew_kernels", evidence="catalogue_exact")) == bi.SIZE_C
    # catalogue + website but no independent/distribution evidence -> D
    c2 = co(website_checked=True, business_category="wholesaler")
    assert bi.size_class(c2, PR(product="cashew_kernels", evidence="catalogue_exact")) == bi.SIZE_D
    # a big website is not an import record
    c3 = co(website_checked=True, business_category="retailer", distribution_network="300 stores")
    assert bi.size_class(c3, PR(product="cashew_kernels", evidence="catalogue_exact")) == bi.SIZE_D


# ---- duplicates ------------------------------------------------------------
def test_dedupe_by_domain_and_by_name():
    a = co(company_id="a", name="Spice Co LLC", website="https://spice.ae", products=[PR(product="turmeric_powder")])
    b = co(company_id="b", name="SPICE CO", website="", phone="+9714", products=[PR(product="cashew_kernels")])
    c = co(company_id="c", name="Other name", website="http://www.spice.ae/contact", products=[PR(product="whole_spices")])
    d = co(company_id="d", name="Spice Co", country="USA", products=[PR(product="whole_spices")])
    out = bi.dedupe([a, b, c, d])
    assert len(out) == 2
    uae = next(o for o in out if o.country != "USA")
    assert {p.product for p in uae.products} == {"turmeric_powder", "cashew_kernels", "whole_spices"} and uae.phone == "+9714"


def test_merge_save_keeps_one_record_and_stronger_evidence(tmp_path):
    d = tmp_path
    first = {"company_id": "1", "name": "Spice Co LLC", "country": "United Arab Emirates", "website": "https://spice.ae/",
             "notes_assessment": "manual note", "products": [{"product": "whole_spices", "evidence": "category_only"}]}
    merge_save(d, "United Arab Emirates", [first])
    second = dict(first, company_id="2", name="Spice Co", notes_assessment="", general_email="info@spice.ae",
                  products=[{"product": "whole_spices", "evidence": "catalogue_exact", "evidence_urls": ["https://spice.ae/p"]}])
    p = merge_save(d, "United Arab Emirates", [second])
    rows = json.loads(p.read_text())
    assert len(rows) == 1 and rows[0]["notes_assessment"] == "manual note" and rows[0]["general_email"] == "info@spice.ae"
    assert rows[0]["products"][0]["evidence"] == "catalogue_exact"


def test_parse_request():
    assert parse_request("Find 10 genuine turmeric powder importers in UAE") == \
        {"product": "turmeric_powder", "country": "United Arab Emirates", "target": 10}
    assert parse_request("find 5 cashew kernel buyers in the USA")["country"] == "USA"
    assert parse_request("Find 10 Indian whole spices importers in Germany")["product"] == "whole_spices"
