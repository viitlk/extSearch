"""Delivery, payment and reviews: one parser for every marketplace.

The layout differs between shops, but the product page always mentions the
same entities: carrier, delivery price and payment method. So the logic is
text-based, and CSS selectors are used only where the block is marked up
reliably.

Three functions:

    extract_delivery(text, profile)  -> [{method, price, note, source}, ...]
    extract_payment(text, profile)   -> ["card online", ...]
    extract_reviews(soup, html)      -> {rating, count, items, source}

`profile` is the shop passport from SHOPS: known delivery and payment options
collected from official pages. It is only a supplement: live prices from the
product page always win.
"""

from __future__ import annotations

import re
from typing import Any

from bs4 import BeautifulSoup

_SPACES = "\u00a0\u202f "
_PRICE_RE = re.compile(
    r"(\d{1,3}(?:[" + _SPACES + r"]\d{3})+|\d+)\s*(?:₴|грн|UAH)", re.I
)
_FREE_RE = re.compile(r"\bбезкоштовно\b|\bбесплатно\b|0\s*₴|без оплати", re.I)

# Carrier -> canonical name and text anchors.
# Order matters: longer names are matched before shorter ones.
CARRIERS: list[tuple[str, tuple[str, ...]]] = [
    ("Nova Poshta", ("нова пошта", "новою поштою", "новой почтой", "нова поштою",
                    "нову пошту", "відділення нової пошти", "поштомат нової пошти")),
    ("Ukrposhta", ("укрпошта", "укрпоштою", "укрпошти")),
    ("Meest", ("meest", "мист", "міст експрес")),
    ("Justin", ("justin", "джастін")),
    ("Pickup", ("самовивіз", "самовивоз", "забрати з магазину",
                   "самовивіз з магазину", "з магазину")),
    ("Courier delivery", ("кур'єр", "курьер", "доставка за адресою",
                           "адресна доставка", "до адреси")),
    ("Rozetka", ("розетка", "rozetka")),
]

PAYMENT_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("card online", ("карткою онлайн", "картой онлайн", "оплата карткою онлайн",
                       "онлайн-оплата", "online payment")),
    ("card at branch", ("карткою у відділенні", "картой в отделении",
                             "карткою в відділенні", "термінал")),
    ("cash on delivery", ("післяплат", "послеплат", "накладений платіж",
                    "наложенный платеж", "оплата при отриманні")),
    ("cash", ("готівк", "наличн", "cash")),
    ("bank transfer", ("безготівк", "безналичн", "для юр. осіб",
                                  "для юридичних осіб", "рахунок-фактура")),
    ("installments", ("частинами", "розстрочк", "рассрочк", "кредит")),
    ("Apple Pay / Google Pay", ("apple pay", "google pay", "g pay")),
    ("crypto", ("bitcoin", "usdt", "крипт")),
]

_CLEAN_RE = re.compile(r"[ \t\r\n" + _SPACES + r"]+")
# Dates and times inside delivery blocks: "Pickup 15.11 149 UAH" would read
# as 11 149 UAH without cleaning — date digits glue to the price.
_DATETIME_RE = re.compile(r"\b\d{1,2}\.\d{1,2}(?:\.\d{2,4})?\b|\b\d{1,2}:\d{2}\b")


def _clean(text: str) -> str:
    return _CLEAN_RE.sub(" ", text or "")


def _segment_price(text: str) -> tuple[int | None, str | None]:
    """Price inside one delivery segment.

    A segment is the text from one carrier name to the next one, so a trailing
    "free delivery with card payment" line on Kasta does not become a zero
    price of the previous carrier.
    """
    text = _DATETIME_RE.sub(" ", text)
    m = _PRICE_RE.search(text)
    if m:
        digits = re.sub(r"\D", "", m.group(1))
        if digits:
            note = None
            tail = text[m.end(): m.end() + 90].lower()
            if "при оплаті карткою" in tail or "при оплате картой" in tail:
                note = "free when paying by card"
            elif _FREE_RE.search(tail):
                note = "free delivery condition applies"
            return int(digits), note
    if _FREE_RE.search(text):
        return 0, "free"
    return None, None


_VARIANT_RULES: list[tuple[str, tuple[str, ...]]] = [
    (" (parcel locker)", ("поштомат", "почтомат")),
    (" (branch)", ("відділенн", "отделени")),
]

# The parcel-locker/branch refinement makes sense for Nova Poshta only.
_VARIANT_ONLY = ("Nova Poshta",)


def _variant(text: str, idx: int, anchor_len: int) -> str:
    """Refine the method: parcel locker or branch.

    The window is deliberately short (18 chars before the name, 40 after):
    otherwise "branch Nova Poshta and Meest" attaches "branch" to Meest.
    """
    around = (text[max(0, idx - 18): idx] + " " + text[idx: idx + anchor_len + 40]).lower()
    for suffix, keys in _VARIANT_RULES:
        if any(k in around for k in keys):
            return suffix
    return ""


def _carrier_hits(low: str) -> list[tuple[int, str, int]]:
    """All carrier mentions: (position, name, anchor length), in order."""
    hits: list[tuple[int, str, int]] = []
    for name, anchors in CARRIERS:
        for anchor in anchors:
            start = 0
            while True:
                idx = low.find(anchor, start)
                if idx < 0:
                    break
                hits.append((idx, name, len(anchor)))
                start = idx + len(anchor)
    hits.sort(key=lambda h: h[0])
    # Collapse neighbouring overlapping mentions ("nova poshta" twice in one
    # name): only the first one matters.
    deduped: list[tuple[int, str, int]] = []
    for hit in hits:
        if deduped and hit[0] < deduped[-1][0] + deduped[-1][2]:
            continue
        deduped.append(hit)
    return deduped


_DELIVERY_ANCHORS = (
    "доставка і оплата", "доставка та оплата", "доставка і оплата",
    "умови доставки", "способи доставки", "доставка:", "доставка по україні",
    "доставка й оплата",
)
_DELIVERY_STOPS = ("оплата", "гарантія", "повернення", "отзывы", "відгуки")


def _delivery_block(text: str) -> str:
    """Cut the page region that lists delivery options.

    Without it a whole-page search catches the menu and the footer, where a
    carrier name sits next to anything — including the product price, which
    then sticks to the carrier.
    """
    low = text.lower()
    best: tuple[int, str] | None = None

    for anchor in _DELIVERY_ANCHORS:
        start = 0
        while True:
            idx = low.find(anchor, start)
            if idx < 0:
                break
            start = idx + len(anchor)
            tail = text[idx: idx + 1200]
            score = sum(1 for name, keys in CARRIERS if any(k in tail.lower() for k in keys))
            if best is None or score > best[0]:
                best = (score, tail)

    if best is None or best[0] < 2:
        return text

    block = best[1]
    block_low = block.lower()
    for stop in _DELIVERY_STOPS:
        pos = block_low.find(stop, 60)
        if pos > 0:
            block = block[:pos]
            block_low = block.lower()
    return block


def extract_delivery(text: str, profile: list[dict[str, Any]] | None = None,
                     limit: int = 14) -> list[dict[str, Any]]:
    """Delivery options with prices, when the page text shows them."""
    clean = _clean(_delivery_block(text))
    low = clean.lower()
    hits = _carrier_hits(low)
    found: dict[str, dict[str, Any]] = {}

    for pos, (idx, name, anchor_len) in enumerate(hits):
        segment_end = hits[pos + 1][0] if pos + 1 < len(hits) else min(len(clean), idx + 220)
        segment = clean[idx + anchor_len: max(segment_end, idx + anchor_len)]
        price, note = _segment_price(segment[:220])
        method = name + (_variant(clean, idx, anchor_len) if name in _VARIANT_ONLY else "")
        if note is None:
            if price == 0:
                note = "free"
            elif price is None:
                note = ("check with the store" if name == "Pickup"
                        else "carrier rate, not fixed")
        entry = found.get(method)
        if entry is None or (entry.get("price") is None and price is not None):
            found[method] = {"method": method, "price": price, "note": note,
                             "source": "page"}

    # Паспорт магазина дополняет только те способы, которых на странице нет.
    for item in profile or []:
        name = item["method"]
        if name in found:
            if found[name].get("price") is None and item.get("price") is not None:
                found[name]["price"] = item["price"]
                found[name]["note"] = item.get("note")
            continue
        same = next((existing for existing in found
                     if existing.split(" (")[0].startswith(name.split(" (")[0])
                     or name.split(" (")[0].startswith(existing.split(" (")[0])), None)
        if same is not None:
            if found[same].get("price") is None and item.get("price") is not None:
                found[same]["price"] = item["price"]
                found[same]["note"] = item.get("note")
            continue
        found[name] = {"method": name, "price": item.get("price"),
                       "note": item.get("note"), "source": "shop_profile"}

    rows = list(found.values())[:limit]
    for row in rows:
        if row["note"] is None and row["price"] == 0:
            row["note"] = "free"
    return rows


def extract_payment(text: str, profile: list[str] | None = None) -> list[dict[str, Any]]:
    """Payment methods: found in the text plus the shop passport."""
    low = _clean(text).lower()
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()

    for name, anchors in PAYMENT_RULES:
        hit = next((a for a in anchors if a in low), None)
        if hit:
            rows.append({"method": name, "source": "page"})
            seen.add(name)

    for name in profile or []:
        if name not in seen:
            rows.append({"method": name, "source": "shop_profile"})
            seen.add(name)

    return rows


def free_shipping_threshold(text: str) -> int | None:
    """Free-shipping threshold: "free delivery from 2 000 UAH" -> 2000."""
    m = re.search(r"безкоштовн\w*[^.]{0,60}?від\s*(\d[\d\s" + _SPACES + r"]{0,8})\s*₴",
                  _clean(text), re.I)
    if not m:
        return None
    digits = re.sub(r"\D", "", m.group(1))
    return int(digits) if digits else None


def extract_reviews(soup: BeautifulSoup | None, html: str = "",
                    limit: int = 3) -> dict[str, Any]:
    """Rating, review count and a couple of review texts, if present."""
    out: dict[str, Any] = {"rating": None, "count": None, "items": []}
    if soup is None:
        return out

    # 1. schema.org microdata — the most reliable source.
    agg = soup.select_one("[itemprop='aggregateRating'], [itemtype*='AggregateRating']")
    if agg is not None:
        rv = agg.select_one("[itemprop='ratingValue']")
        cr = agg.select_one("[itemprop='reviewCount'], [itemprop='ratingCount']")
        if rv is not None:
            try:
                out["rating"] = float((rv.get("content") or rv.get_text(strip=True)).replace(",", "."))
            except ValueError:
                pass
        if cr is not None:
            digits = re.sub(r"\D", "", cr.get("content") or cr.get_text(strip=True))
            out["count"] = int(digits) if digits else None

    # 2. Class names such as rating / reviews / відгуки.
    if out["rating"] is None:
        for sel in (".rating-number-box__value", ".product-rating__value",
                    "[class*='Rating-module__rating']",
                    "[class*='rating'][class*='value']", "[class*='ratingValue']"):
            el = soup.select_one(sel)
            if el is None:
                continue
            try:
                out["rating"] = float(el.get_text(strip=True).replace(",", ".")[:4])
                break
            except ValueError:
                continue

    if out["rating"] is None:
        # Brain draws the rating as star images: grey = empty, coloured = filled.
        # There is no number in the markup and the page has several blocks
        # (product, series, similar) — take the first one only.
        block = soup.select_one(".main-comments-block .comments-average-rating-stars") \
            or soup.select_one(".comments-average-rating-stars")
        if block is not None:
            filled = sum(1 for img in block.select("img")
                         if "gray" not in (img.get("src") or img.get("data-observe-src") or ""))
            if filled:
                out["rating"] = float(min(filled, 5))

    if out["count"] is None:
        for sel in (".product-tile-feedback__reviews", ".product-rating__count",
                    "[class*='reviews-count']", "[class*='comments-amount']"):
            el = soup.select_one(sel)
            if el is None:
                continue
            digits = re.sub(r"\D", "", el.get_text(strip=True))
            if digits:
                out["count"] = int(digits)
                break

    # 3. Review texts. Only explicitly marked blocks, so product description
    # fragments do not leak into the answer.
    for sel in (".comment__body", ".review__text", "[class*='review-text']",
                "[itemprop='reviewBody']"):
        for el in soup.select(sel)[:limit]:
            text = _clean(el.get_text(" ", strip=True))
            if len(text) > 30:
                out["items"].append(text[:400])
        if out["items"]:
            break

    if out["rating"] is not None or out["count"] is not None or out["items"]:
        out["source"] = "page"
    return out


def enrich(html_or_text: Any, profile: dict[str, Any] | None = None,
           soup: BeautifulSoup | None = None) -> dict[str, Any]:
    """Collect all three blocks in one call."""
    soup = soup if soup is not None else BeautifulSoup(str(html_or_text), "html.parser")
    text = soup.get_text(" ", strip=True)
    profile = profile or {}
    result: dict[str, Any] = {
        "delivery": extract_delivery(text, profile.get("delivery")),
        "payment": extract_payment(text, profile.get("payment")),
    }
    reviews = extract_reviews(soup)
    if reviews.get("rating") is not None or reviews.get("count") or reviews.get("items"):
        result["reviews"] = reviews
    threshold = free_shipping_threshold(text)
    if threshold:
        result["free_shipping_from"] = threshold
    return result


# --------------------------------------------------------------------------
# Shop passports: collected from official pages (November 2026).
# Only prices the shop publishes as fixed are numbers; everything else is
# marked as a carrier rate instead of inventing digits.
# --------------------------------------------------------------------------

SHOPS: dict[str, dict[str, Any]] = {
    "comfy": {
        "name": "Comfy",
        "delivery": [
            {"method": "Pickup at a Comfy store", "price": 0},
            {"method": "Nova Poshta", "price": None, "note": "carrier rate, not fixed"},
            {"method": "Meest", "price": None, "note": "carrier rate, not fixed"},
            {"method": "Courier delivery", "price": None, "note": "carrier rate, not fixed"},
        ],
        "payment": ["cash", "card online", "bank transfer",
                    "installments"],
        "info_url": "https://comfy.ua/ua/",
    },
    "allo": {
        "name": "Allo",
        "delivery": [
            {"method": "Pickup at an Allo store", "price": 0},
            {"method": "Nova Poshta", "price": None, "note": "carrier rate, not fixed"},
            {"method": "Ukrposhta", "price": None, "note": "carrier rate, not fixed"},
            {"method": "Courier delivery", "price": None, "note": "carrier rate, not fixed"},
        ],
        "payment": ["card online", "cash", "cash on delivery",
                    "installments", "bank transfer"],
        "info_url": "https://allo.ua/ua/",
    },
    "moyo": {
        "name": "Moyo",
        "delivery": [
            {"method": "Pickup", "price": 0},
            {"method": "Nova Poshta", "price": None},
            {"method": "Ukrposhta", "price": None},
            {"method": "Courier delivery", "price": None},
        ],
        "payment": ["cash", "card online", "installments",
                    "bank transfer"],
        "info_url": "https://www.moyo.ua/ua/",
    },
    "kasta": {
        "name": "Kasta",
        "delivery": [
            {"method": "Nova Poshta", "price": None, "note": "carrier rate, not fixed"},
            {"method": "Ukrposhta", "price": None, "note": "carrier rate, not fixed"},
            {"method": "Courier delivery", "price": None, "note": "carrier rate, not fixed"},
        ],
        "payment": ["card online", "card at branch", "cash",
                    "cash on delivery", "installments"],
        "info_url": "https://kasta.ua/uk/",
    },
    "rozetka": {
        "name": "Rozetka",
        "delivery": [
            {"method": "Pickup at a Rozetka branch", "price": 0},
            {"method": "Nova Poshta", "price": None, "note": "carrier rate, not fixed"},
            {"method": "Rozetka courier", "price": None, "note": "carrier rate"},
        ],
        "payment": ["card online", "cash on delivery", "installments",
                    "bank transfer"],
        "info_url": "https://rozetka.com.ua/ua/",
    },
    "foxtrot": {
        "name": "Foxtrot",
        "delivery": [
            {"method": "Pickup at a Foxtrot store", "price": 0},
            {"method": "Nova Poshta", "price": None, "note": "carrier rate, not fixed"},
            {"method": "Courier delivery", "price": None, "note": "carrier rate"},
        ],
        "payment": ["card online", "cash", "installments",
                    "bank transfer"],
        "info_url": "https://www.foxtrot.com.ua/uk/",
    },
    "prom": {
        "name": "Prom.ua",
        "delivery": [
            {"method": "Nova Poshta", "price": None, "note": "carrier rate, not fixed"},
            {"method": "Ukrposhta", "price": None, "note": "carrier rate, not fixed"},
            {"method": "Pickup", "price": None, "note": "at the seller"},
        ],
        "payment": ["card online", "cash on delivery", "bank transfer"],
        "info_url": "https://prom.ua/",
    },
    "brain": {
        "name": "Brain",
        "delivery": [
            {"method": "Pickup at a Brain store", "price": 0},
            {"method": "Nova Poshta", "price": 35, "note": "fixed Brain price"},
            {"method": "Ukrposhta", "price": 35, "note": "fixed Brain price"},
            {"method": "Courier delivery", "price": 35,
             "note": "free from 250 UAH"},
        ],
        "payment": ["cash", "card online", "bank transfer",
                    "installments"],
        "info_url": "https://brain.com.ua/ukr/",
    },
    "ctrs": {
        "name": "Citrus",
        "delivery": [
            {"method": "Pickup at a Citrus store", "price": 0},
            {"method": "Nova Poshta", "price": None, "note": "carrier rate, not fixed"},
            {"method": "Ukrposhta", "price": None, "note": "carrier rate, not fixed"},
            {"method": "Courier delivery", "price": None, "note": "carrier rate"},
        ],
        "payment": ["cash", "card online", "bank transfer",
                    "installments"],
        "info_url": "https://ctrs.com.ua/",
    },
    "olx": {
        "name": "OLX",
        "delivery": [
            {"method": "OLX Delivery (Nova Poshta)", "price": None,
             "note": "carrier rate, not fixed"},
            {"method": "OLX Delivery (Ukrposhta)", "price": None,
             "note": "carrier rate, not fixed"},
            {"method": "Pickup", "price": None, "note": "at the seller"},
        ],
        "payment": ["cash on delivery", "card online", "cash"],
        "info_url": "https://www.olx.ua/",
    },
}
