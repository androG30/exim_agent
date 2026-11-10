"""Extract public business facts from HTML: title, text, emails, phones, links."""
from __future__ import annotations

import html as htmllib
import re
import urllib.parse

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,24}")
PHONE_RE = re.compile(r"(?:\+|00)\d{1,3}[\s.\-()]*\d(?:[\s.\-()]*\d){6,12}")
FREE_MAIL = {"gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "live.com", "icloud.com", "aol.com",
             "yahoo.co.in", "rediffmail.com", "eim.ae", "emirates.net.ae"}
IGNORE_EMAIL = re.compile(r"(example\.|sentry|wixpress|domain\.com|yourdomain|email\.com|\.(png|jpg|gif|svg|webp)$)", re.I)


def strip_tags(html: str) -> str:
    html = re.sub(r"(?is)<(script|style|noscript|svg)[^>]*>.*?</\1>", " ", html)
    html = re.sub(r"(?s)<!--.*?-->", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", htmllib.unescape(text)).strip()


def title(html: str) -> str:
    m = re.search(r"(?is)<title[^>]*>(.*?)</title>", html)
    return re.sub(r"\s+", " ", htmllib.unescape(m.group(1))).strip() if m else ""


def og_site_name(html: str) -> str:
    m = re.search(r'(?is)<meta[^>]+property=["\']og:site_name["\'][^>]+content=["\']([^"\']+)', html)
    return htmllib.unescape(m.group(1)).strip() if m else ""


def links(html: str, base: str) -> list[tuple[str, str]]:
    out = []
    for m in re.finditer(r'(?is)<a\b[^>]*href=["\']([^"\'#]+)["\'][^>]*>(.*?)</a>', html):
        href, label = m.group(1).strip(), strip_tags(m.group(2))[:80]
        if href.startswith(("javascript:", "tel:")):
            continue
        out.append((urllib.parse.urljoin(base, href), label))
    return out


def emails(html: str, own_domain: str = "") -> list[str]:
    """Emails printed on the page (mailto or text). Never constructs addresses."""
    found = set(m.lower() for m in EMAIL_RE.findall(htmllib.unescape(html)))
    found = {e.rstrip(".") for e in found if not IGNORE_EMAIL.search(e)}
    own = own_domain.lower().removeprefix("www.")

    def rank(e):
        d = e.split("@")[1]
        return (0 if own and d.endswith(own) else 1 if d in FREE_MAIL else 2, e)
    return sorted(found, key=rank)


def phones(text: str) -> list[str]:
    out = []
    for m in PHONE_RE.findall(text):
        p = re.sub(r"[^\d+]", "", m.replace("00", "+", 1) if m.startswith("00") else m)
        if 9 <= len(p.lstrip("+")) <= 15 and p not in out:
            out.append(p)
    return out


def linkedin_company(link_list) -> str:
    for url, _ in link_list:
        if re.search(r"linkedin\.com/company/[^/?#]+", url):
            return re.search(r"https?://[^?#]*linkedin\.com/company/[^/?#]+", url).group(0)
    return ""


def classify_page_links(link_list, own_domain: str) -> dict:
    """Pick same-site contact / about / products pages to visit next."""
    picks = {}
    for url, label in link_list:
        host = urllib.parse.urlsplit(url).netloc.lower().removeprefix("www.")
        if own_domain and host and not host.endswith(own_domain):
            continue
        key = (url + " " + label).lower()
        for kind, words in (("contact", ("contact",)), ("about", ("about", "who-we-are", "company profile")),
                            ("products", ("product", "spice", "cashew", "nuts", "catalog", "catalogue", "range"))):
            if kind not in picks and any(w in key for w in words):
                picks[kind] = url
    return picks
