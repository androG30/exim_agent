"""Product and product-form detection from catalogue text or shipment descriptions.

Key rule: HS 0910.30 alone never proves turmeric powder or turmeric fingers.
Form evidence needs words such as "turmeric powder" / "ground turmeric" or
"turmeric fingers" / "whole turmeric" / "turmeric bulbs" in a catalogue or a
detailed shipment description.
"""
from __future__ import annotations

import re

TURMERIC_POWDER = re.compile(r"\b(turmeric|haldi|curcuma)\s*(powder|ground|pwd)\b|\bground\s+(turmeric|curcuma)\b|"
                             r"\bpowdered\s+turmeric\b", re.I)
TURMERIC_FINGERS = re.compile(r"\b(turmeric|haldi|curcuma)\s*(fingers?|whole|bulbs?|polished|unpolished|rhizomes?|sticks?)\b|"
                              r"\bwhole\s+(turmeric|haldi|curcuma)\b|\b(dried|dry)\s+(turmeric|haldi)\s+(fingers?|whole)\b", re.I)
TURMERIC_ANY = re.compile(r"\b(turmeric|haldi|curcuma)\b", re.I)

CASHEW = re.compile(r"\bcashews?\b|\bkaju\b|\banacardium\b", re.I)
CASHEW_KERNEL = re.compile(r"\bcashew\s*(nut\s*)?kernels?\b|\b(w|sw|sww|ws)\s?-?(180|210|240|320|450|500)\b|"
                           r"\bcashew\s+(splits?|pieces|butts|lwp|swp|bb)\b", re.I)
CASHEW_GRADE = re.compile(r"\b(S?W{1,2}S?)\s?-?(180|210|240|320|450|500)\b|\b(LWP|SWP|BB|SSW|DW|SP|SS|LP|SB|JH)\b")

WHOLE_SPICES = {
    "black pepper": (r"\bblack\s+pepper(corns?)?\b|\bpeppercorns?\b|\bpiper\s+nigrum\b", r"\b(whole|corns?|seeds?|garbled|ungarbled|bold|\d{3}\s?g/?l)\b"),
    "cardamom": (r"\b(green\s+|small\s+|black\s+|large\s+)?cardamom(s)?\b|\belaichi\b", r"\b(whole|pods?|seeds?|bold|\d\s?mm)\b"),
    "cloves": (r"\bcloves?\b|\blaung\b", r"\b(whole|hand\s*picked|stem)\b"),
    "cinnamon": (r"\bcinnamon\b|\bcassia\b|\bdalchini\b", r"\b(whole|sticks?|quills?|tubes?|cigarettes?|split|bark|rolls?)\b"),
    "cumin": (r"\bcumin\b|\bjeera\b", r"\b(seeds?|whole|singapore|europe\s+quality|99%)\b"),
    "coriander": (r"\bcoriander\b|\bdhania\b", r"\b(seeds?|whole|eagle|scooter|split)\b"),
    "fennel": (r"\bfennel\b|\bsaunf\b", r"\b(seeds?|whole|lucknowi)\b"),
    "star anise": (r"\bstar\s*ani(se|seed)\b|\bbadian\b|\bchakra\s*phool\b", r"\b(whole|flowers?|stars?)\b"),
    "fenugreek": (r"\bfenugreek\b|\bmethi\b", r"\b(seeds?|whole)\b"),
    "mustard seed": (r"\bmustard\s+seeds?\b", r""),
    "nutmeg": (r"\bnutmegs?\b", r"\b(whole|shell)\b"),
    "dry ginger": (r"\bdr(y|ied)\s+ginger\b|\bsonth\b", r"\b(whole|slices?|split)\b"),
}
GROUND_WORDS = re.compile(r"\b(powder|ground|crushed|masala)\b", re.I)


def _window(text, m, width=60):
    return text[max(0, m.start() - width): m.end() + width]


def turmeric_forms(text: str) -> dict:
    """{'powder': snippet|None, 'fingers': snippet|None, 'unspecified': bool}"""
    p = TURMERIC_POWDER.search(text or "")
    f = TURMERIC_FINGERS.search(text or "")
    return {"powder": _window(text, p) if p else None,
            "fingers": _window(text, f) if f else None,
            "unspecified": bool(TURMERIC_ANY.search(text or "")) and not p and not f}


def cashew_info(text: str) -> dict:
    t = text or ""
    grades = sorted({"".join(g for g in m if g) for m in CASHEW_GRADE.findall(t)})
    k = CASHEW_KERNEL.search(t)
    return {"mentioned": bool(CASHEW.search(t)), "kernel": _window(t, k) if k else None, "grades": grades}


def whole_spices(text: str) -> dict:
    """{spice: {'snippet':..., 'whole': bool}} for spices named in the text.
    'whole' is True only when a whole-form word appears near the spice name and no
    ground word is attached to it."""
    out = {}
    t = text or ""
    for spice, (name_re, whole_re) in WHOLE_SPICES.items():
        for m in re.finditer(name_re, t, re.I):
            win = _window(t, m, 40)
            whole = bool(whole_re) and bool(re.search(whole_re, win, re.I))
            after, before = t[m.end():m.end() + 12], t[max(0, m.start() - 10):m.start()]
            ground_attached = bool(re.match(r"\s*(powder|ground)", after, re.I) or re.search(r"(powder|ground)\s*$", before, re.I))
            whole = whole and not ground_attached or bool(re.match(r"\s*(seeds?|whole|pods?|sticks?)\b", after, re.I))
            prev = out.get(spice)
            if not prev or (whole and not prev["whole"]):
                out[spice] = {"snippet": win, "whole": whole}
    return out


def product_evidence(product: str, text: str, source_kind: str = "catalogue") -> dict | None:
    """Turn page/shipment text into a ProductRelevance-like dict, or None if the
    product is not mentioned. source_kind: catalogue | shipment_description."""
    if product in ("turmeric_powder", "turmeric_fingers"):
        tf = turmeric_forms(text)
        key = "powder" if product == "turmeric_powder" else "fingers"
        if tf[key]:
            return {"product": product, "evidence": "catalogue_exact" if source_kind == "catalogue" else "shipment",
                    "form_evidence": source_kind, "forms": "Turmeric " + ("powder" if key == "powder" else "fingers/whole"),
                    "evidence_text": tf[key].strip()}
        if tf["unspecified"] or tf["powder"] or tf["fingers"]:
            return {"product": product, "evidence": "category_only" if source_kind == "catalogue" else "shipment_form_unknown",
                    "form_evidence": "", "forms": "Turmeric (form not stated)" if tf["unspecified"] else
                    "Only the other turmeric form is evidenced", "evidence_text": (TURMERIC_ANY.search(text) and
                                                                                  _window(text, TURMERIC_ANY.search(text)).strip()) or ""}
        return None
    if product == "cashew_kernels":
        ci = cashew_info(text)
        if not ci["mentioned"]:
            return None
        exact = bool(ci["kernel"] or ci["grades"])
        return {"product": product, "evidence": ("catalogue_exact" if exact else "category_only") if source_kind == "catalogue"
                else "shipment", "grades": ", ".join(ci["grades"]),
                "forms": "Cashew kernels" if exact else "Cashew (kernel/grade not stated)",
                "evidence_text": (ci["kernel"] or "").strip()}
    if product == "whole_spices":
        ws = whole_spices(text)
        if not ws:
            return None
        whole = [s for s, v in ws.items() if v["whole"]]
        named = sorted(ws)
        exact = bool(whole) or len(named) >= 3   # three or more named spices = a whole-spice range
        return {"product": product, "evidence": ("catalogue_exact" if exact else "category_only") if source_kind == "catalogue"
                else "shipment", "forms": ("Whole: " + ", ".join(whole) + "; " if whole else "") + "Named: " + ", ".join(named),
                "evidence_text": "; ".join(ws[s]["snippet"].strip() for s in (whole or named)[:3])}
    raise ValueError(product)


IMPORT_WORDS = re.compile(r"\b(import(er|ers|ing|s)?|sourc(e|ed|ing) from|origin(s)?)\b", re.I)
INDIA = re.compile(r"\b(india|indian|kerala|cochin|kochi|mumbai|chennai|gujarat|unjha|erode|nizamabad|sangli|"
                   r"kollam|mangalore|tuticorin)\b", re.I)
ROLE_WORDS = [("importer", r"\bimport(er|ers|ing)?\b"), ("wholesaler", r"\bwholesal(e|er|ers)\b"),
              ("distributor", r"\bdistribut(or|ors|ion)\b"), ("processor", r"\b(process(ing|or)|grinding|milling|cleaning\s+and\s+grading)\b"),
              ("packer", r"\b(pack(ing|er|ers)|repack)\b"), ("manufacturer", r"\bmanufactur(er|ing)\b"),
              ("retailer", r"\b(retail(er)?|hypermarket|supermarket)\b"), ("trader", r"\btrad(er|ing)\b")]


def roles(text: str) -> list[str]:
    return [r for r, rx in ROLE_WORDS if re.search(rx, text or "", re.I)]


def india_sourcing(text: str) -> str:
    """Snippet showing an import/sourcing link to India, else ''."""
    for m in IMPORT_WORDS.finditer(text or ""):
        win = _window(text, m, 120)
        if INDIA.search(win):
            return win.strip()
    return ""
