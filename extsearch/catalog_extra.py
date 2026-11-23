"""More Ukrainian marketplaces: Bigl, Epicentr, Makeup, AUTO.RIA.

The module reuses the HTTP layer from ``catalog`` (same throttling, cache,
proxy pool and id -> url map), so it imports its private helpers. That way a
product card opens by the id from search results without a second search.
"""

from __future__ import annotations

import re
import urllib.parse
import zlib
from typing import Any

from bs4 import BeautifulSoup

from .catalog import _fetch, _finish, _int_from, _price_from_text, _relevance, _remember
from .errors import ExtSearchError as OlxError

_SPACES = re.compile(r"[ \t\u00a0\u202f]+")


def _squash(value: str | None) -> str:
    return _SPACES.sub(" ", (value or "").replace("\n", " ")).strip()


def _url_id(url: str) -> int:
    """Stable numeric id derived from the page URL.

    Epicentr cards carry no numeric identifier, only a slug, so crc32 is used:
    it is identical between runs and keeps the id -> url map working.
    """
    return zlib.crc32(url.encode("utf-8")) & 0x7FFFFFFF


# --------------------------------------------------------------------------
# Bigl.ua — Prom group marketplace, stable data-qaid anchors
# --------------------------------------------------------------------------

BIGL_SEARCH = "https://bigl.ua/search"
_BIGL_ID = re.compile(r"product-item-(\d+)$")


async def bigl_search(query: str, limit: int = 10, page: int = 1) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"search_term": query}
    if page > 1:
        params["page"] = page
    html = await _fetch(BIGL_SEARCH, params)
    soup = BeautifulSoup(html, "html.parser")
    rows: list[dict[str, Any]] = []

    for card in soup.select('[data-qaid^="product-item-"]'):
        link = card.select_one('a[data-qaid="product-name"]')
        if link is None:
            continue
        match = _BIGL_ID.search(card.get("data-qaid") or "")
        href = link.get("href") or ""
        if match is None:
            match = re.search(r"/p(\d{5,})", href)
        if match is None:
            continue
        offer_id = int(match.group(1))
        title = _squash(link.get("title") or link.get_text())
        url = href if href.startswith("http") else f"https://bigl.ua{href}"
        _remember("bigl", offer_id, url)

        price_el = (card.select_one('[data-qaid="product-price-new"]')
                    or card.select_one('[data-qaid="product-price"][title]')
                    or card.select_one('[data-qaid="product-price"]'))
        price = _price_from_text(price_el.get("title") or price_el.get_text(" ", strip=True)
                                 if price_el else None)
        row: dict[str, Any] = {
            "id": offer_id,
            "title": title,
            "price": price,
            "currency": "UAH",
            "state": None,
            "availability": None,
            "city": None,
            "business": True,
            "relevance": _relevance(query, title),
            "url": url,
        }
        old_el = card.select_one('[data-qaid="unit-old-price"]')
        if old_el is not None:
            row["old_price"] = _price_from_text(old_el.get_text(" ", strip=True))
        rows.append(row)

    return rows[: max(limit * 2, 20)]


async def bigl_get(offer_id: int, max_description_chars: int = 600) -> dict[str, Any]:
    return await _product_page(offer_id, "bigl", max_description_chars,
                               title_sel="h1",
                               price_sel='[data-qaid="product-price"][title], [data-qaid="product-price"], .b-product-gallery__price')


# --------------------------------------------------------------------------
# Epicentr — DIY hypermarket, Nuxt + data-product-* attributes
# --------------------------------------------------------------------------

EPICENTR_SEARCH = "https://epicentrk.ua/ua/search/"


async def epicentr_search(query: str, limit: int = 10, page: int = 1) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"q": query}
    if page > 1:
        params["page"] = page
    html = await _fetch(EPICENTR_SEARCH, params)
    soup = BeautifulSoup(html, "html.parser")
    rows: list[dict[str, Any]] = []

    for card in soup.select("li[data-test-small-card]"):
        title_el = card.select_one("p[data-product-card-name] a") or card.select_one("a[data-product-picture]")
        if title_el is None:
            continue
        href = title_el.get("href") or ""
        if not href:
            continue
        url = href if href.startswith("http") else f"https://epicentrk.ua{href}"
        offer_id = _url_id(url)
        _remember("epicentr", offer_id, url)
        title = _squash(title_el.get("title") or title_el.get_text())
        price_el = card.select_one("[data-product-price-main]")
        price = None
        if price_el is not None:
            price = _price_from_text(price_el.get("title") or price_el.get_text(" ", strip=True))
            if price is None:
                data_el = price_el.select_one("data[value]")
                price = _int_from(data_el.get("value")) if data_el is not None else None
        row: dict[str, Any] = {
            "id": offer_id,
            "title": title,
            "price": price,
            "currency": "UAH",
            "state": None,
            "availability": None,
            "city": None,
            "business": True,
            "relevance": _relevance(query, title),
            "url": url,
        }
        old_el = card.select_one("[data-product-price-old]")
        if old_el is not None:
            row["old_price"] = _price_from_text(old_el.get_text(" ", strip=True))
        rows.append(row)

    return rows[: max(limit * 2, 20)]


async def epicentr_get(offer_id: int, max_description_chars: int = 600) -> dict[str, Any]:
    return await _product_page(offer_id, "epicentr", max_description_chars,
                               title_sel="h1",
                               price_sel="[data-product-price-main], [itemprop='price']")


# --------------------------------------------------------------------------
# Makeup — cosmetics; CSS modules, the semantic part of a class is stable
# --------------------------------------------------------------------------

MAKEUP_SEARCH = "https://makeup.com.ua/ua/search/"
_MAKEUP_ID = re.compile(r"/product/(\d+)")


async def makeup_search(query: str, limit: int = 10, page: int = 1) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"q": query}
    if page > 1:
        params["page"] = page
    html = await _fetch(MAKEUP_SEARCH, params)
    soup = BeautifulSoup(html, "html.parser")
    rows: list[dict[str, Any]] = []
    seen: set[int] = set()

    for link in soup.select('a.ProductCard__title[href*="/product/"]'):
        match = _MAKEUP_ID.search(link.get("href") or "")
        if match is None:
            continue
        offer_id = int(match.group(1))
        if offer_id in seen:
            continue
        seen.add(offer_id)
        url = urllib.parse.urljoin("https://makeup.com.ua", link.get("href") or "")
        _remember("makeup", offer_id, url)
        title = _squash(link.get_text())
        card = link.find_parent(class_=re.compile("ProductCard__cardContainer")) or link.parent
        price_el = card.select_one(".Price__priceCurrent") if card else None
        row: dict[str, Any] = {
            "id": offer_id,
            "title": title,
            "price": _price_from_text(price_el.get_text(" ", strip=True)) if price_el else None,
            "currency": "UAH",
            "state": None,
            "availability": None,
            "city": None,
            "business": True,
            "relevance": _relevance(query, title),
            "url": url,
        }
        if card is not None:
            old_el = card.select_one(".Price__priceOld")
            if old_el is not None:
                row["old_price"] = _price_from_text(old_el.get_text(" ", strip=True))
            stars = card.select(".Rating__rating .Rating__star")
            if stars:
                row["rating"] = float(len(stars))
            reviews_el = card.select_one(".Rating__reviews")
            if reviews_el is not None:
                count = _int_from(reviews_el.get_text(" ", strip=True))
                if count is not None:
                    row["reviews_count"] = count
        rows.append(row)

    return rows[: max(limit * 2, 20)]


async def makeup_get(offer_id: int, max_description_chars: int = 600) -> dict[str, Any]:
    return await _product_page(offer_id, "makeup", max_description_chars,
                               title_sel="h1",
                               price_sel=".Price__priceCurrent, [itemprop='price']")


# --------------------------------------------------------------------------
# Shafa — second-hand fashion; hashed CSS classes, so structure is used instead
# --------------------------------------------------------------------------

SHAFA_SEARCH = "https://shafa.ua/uk"
_SHAFA_ID = re.compile(r"/item/(\d+)")
_SHAFA_PRICE = re.compile(r"(\d[\d\s\u00a0]*)\s*грн")


def _shafa_card(link: Any) -> Any:
    """Walk up until the ancestor that also holds the price.

    Shafa uses CSS modules with hashed class names, so no class selector is
    stable. The structure is: the product link, then a sibling footer with the
    price; their common ancestor is the card.
    """
    node = link
    for _ in range(6):
        node = node.parent
        if node is None:
            return None
        if _SHAFA_PRICE.search(node.get_text(" ", strip=True)):
            return node
    return None


async def shafa_search(query: str, limit: int = 10, page: int = 1) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"search": query}
    if page > 1:
        params["page"] = page
    html = await _fetch(SHAFA_SEARCH, params, chrome=True)
    soup = BeautifulSoup(html, "html.parser")
    rows: list[dict[str, Any]] = []
    seen: set[int] = set()

    for link in soup.select('a[href*="/uk/item/"]'):
        href = link.get("href") or ""
        match = _SHAFA_ID.search(href)
        if match is None:
            continue
        offer_id = int(match.group(1))
        if offer_id in seen:
            continue
        card = _shafa_card(link)
        if card is None:
            continue
        image = card.select_one("img[alt]")
        title = _squash(image.get("alt") if image else "")
        if not title:
            continue
        seen.add(offer_id)
        url = href if href.startswith("http") else f"https://shafa.ua{href}"
        _remember("shafa", offer_id, url)
        price_match = _SHAFA_PRICE.search(card.get_text(" ", strip=True))
        rows.append({
            "id": offer_id,
            "title": title,
            "price": _int_from(price_match.group(1)) if price_match else None,
            "currency": "UAH",
            "state": "used",
            "availability": None,
            "city": None,
            "business": False,
            "relevance": _relevance(query, title),
            "url": url,
        })

    return rows[: max(limit * 2, 20)]


async def shafa_get(offer_id: int, max_description_chars: int = 600) -> dict[str, Any]:
    return await _product_page(offer_id, "shafa", max_description_chars,
                               title_sel="h1",
                               price_sel="[itemprop='price'], .product-price")


# --------------------------------------------------------------------------
# AUTO.RIA — cars; price is given in USD and in hryvnia
# --------------------------------------------------------------------------

AUTO_SEARCH = "https://auto.ria.com/uk/search/"
_AUTO_USD = re.compile(r"([\d\s\u00a0]{3,})\s*\$")
_AUTO_UAH = re.compile(r"([\d\s\u00a0]{4,})\s*грн")


async def auto_search(query: str, limit: int = 10, page: int = 1) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"q": query}
    if page > 1:
        params["page"] = page
    html = await _fetch(AUTO_SEARCH, params)
    soup = BeautifulSoup(html, "html.parser")
    rows: list[dict[str, Any]] = []

    for card in soup.select("a.product-card[data-car-id]"):
        raw_id = card.get("data-car-id") or ""
        if not raw_id.isdigit():
            continue
        offer_id = int(raw_id)
        href = card.get("href") or ""
        url = href if href.startswith("http") else f"https://auto.ria.com{href}"
        _remember("auto", offer_id, url)

        image = card.select_one("img[title]")
        title = _squash(image.get("title") if image else "")
        if not title:
            title = _squash(card.get_text(" ", strip=True))[:80]

        text = card.get_text(" ", strip=True)
        usd = _AUTO_USD.search(text)
        uah = _AUTO_UAH.search(text)
        price_usd = _int_from(usd.group(1)) if usd else None
        price_uah = _int_from(uah.group(1)) if uah else None
        row: dict[str, Any] = {
            "id": offer_id,
            "title": title,
            "price": price_usd,
            "currency": "USD",
            "state": None,
            "availability": None,
            "city": None,
            "business": True,
            "relevance": _relevance(query, title),
            "url": url,
        }
        if price_uah is not None:
            row["price_uah"] = price_uah
        mileage = re.search(r"(\d{1,3})\s*тис\.\s*км", text)
        if mileage:
            row["mileage_km"] = int(mileage.group(1)) * 1000
        year = re.search(r"\b(19[5-9]\d|20[0-4]\d)\b", title)
        if year:
            row["year"] = int(year.group(1))
        rows.append(row)

    return rows[: max(limit * 2, 20)]


async def auto_get(offer_id: int, max_description_chars: int = 600) -> dict[str, Any]:
    return await _product_page(offer_id, "auto", max_description_chars,
                               title_sel="h1",
                               price_sel=".price_value, [itemprop='price'], .common-text.body.bold")


# --------------------------------------------------------------------------
# Shared product card for the new sources
# --------------------------------------------------------------------------

async def _product_page(offer_id: int, source: str, max_description_chars: int, *,
                        title_sel: str, price_sel: str) -> dict[str, Any]:
    """Own copy of catalog._product_page without the forced chrome profile.

    Bigl/Epicentr/Makeup serve plain HTML and dislike impersonate, while
    AUTO.RIA requires a browser TLS fingerprint.
    """
    from .catalog import _fetch_url
    url = _fetch_url(source, int(offer_id))
    html = await _fetch(url, chrome=source == "auto")
    soup = BeautifulSoup(html, "html.parser")

    title_el = soup.select_one(title_sel)
    title = _squash(title_el.get_text()) if title_el else None

    price = None
    for sel in price_sel.split(","):
        el = soup.select_one(sel.strip())
        if el is None:
            continue
        price = _price_from_text(el.get("content") or el.get_text(" ", strip=True))
        if price is not None:
            break

    desc_el = soup.select_one("[itemprop='description'], .product-description, "
                              ".b-product-description, .product-about")
    description = _squash(desc_el.get_text(" ") if desc_el else "")
    if max_description_chars and len(description) > max_description_chars:
        description = description[:max_description_chars].rstrip() + "…"

    result: dict[str, Any] = {
        "source": source,
        "id": int(offer_id),
        "title": title,
        "price": price,
        "currency": "USD" if source == "auto" else "UAH",
        "description": description,
        "url": url,
    }
    if soup.select_one("h1") is None and title is None:
        raise OlxError("not_found", f"{source}: could not parse the card {offer_id}")
    return result


SEARCHERS = {
    "bigl": bigl_search,
    "shafa": shafa_search,
    "epicentr": epicentr_search,
    "makeup": makeup_search,
    "auto": auto_search,
}

GETTERS = {
    "bigl": bigl_get,
    "shafa": shafa_get,
    "epicentr": epicentr_get,
    "makeup": makeup_get,
    "auto": auto_get,
}


async def extra_search(source: str, query: str, *, limit: int = 10, page: int = 1,
                       price_min: int | None = None, price_max: int | None = None,
                       sort: str | None = None, strict: bool = False,
                       include_urls: bool = False) -> dict[str, Any]:
    searcher = SEARCHERS.get(source)
    if searcher is None:
        raise OlxError("bad_argument", f"source must be one of: {', '.join(SEARCHERS)}")
    page = max(1, int(page))
    limit = max(1, min(int(limit), 50))
    rows = await searcher(query, limit=limit, page=page)
    return _finish(rows, total=len(rows), page=page, limit=limit,
                   price_min=price_min, price_max=price_max, sort=sort,
                   strict=strict, query=query, source=source,
                   include_urls=include_urls)


async def extra_get(source: str, offer_id: int, *,
                    max_description_chars: int = 600) -> dict[str, Any]:
    getter = GETTERS.get(source)
    if getter is None:
        raise OlxError("bad_argument", f"source must be one of: {', '.join(GETTERS)}")
    return await getter(int(offer_id), max_description_chars=max_description_chars)
