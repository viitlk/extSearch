"""Adapters for foreign marketplaces.

Every parser returns the same raw row shape as the Ukrainian sources and goes
through ``catalog._finish``, so filters, sorting and the id -> url map behave
the same in every country.

Status flags (see README):
  verified=True    — checked from this host, the markup parses;
  needs_proxy=True — the marketplace answers 403/429 without a proxy.
"""

from __future__ import annotations

import html as html_lib
import json
import re
import urllib.parse
from typing import Any

from bs4 import BeautifulSoup

from ..catalog import _finish, _int_from, _relevance, _remember
from ..errors import ExtSearchError
from ..net import fetch_json, fetch_text

_MONEY_AFTER = re.compile(
    r"(\d[\d\s.,\u00a0]*)\s*(?:([$€£¥₽₴]|USD|EUR|GBP|JPY|RUB|UAH|PLN|zł|грн)|([$€£¥₽₴]))")
_MONEY_BEFORE = re.compile(
    r"([$€£¥₽₴]|USD|EUR|GBP|JPY|RUB|UAH|PLN|zł|грн)\s*(\d[\d\s.,\u00a0]*)")

SYMBOL_TO_CODE = {
    "$": "USD", "€": "EUR", "£": "GBP", "¥": "JPY", "₽": "RUB",
    "₴": "UAH", "zł": "PLN", "грн": "UAH", "USD": "USD", "EUR": "EUR",
    "GBP": "GBP", "JPY": "JPY", "RUB": "RUB", "UAH": "UAH", "PLN": "PLN",
}


def money(text: str | None) -> tuple[int | None, str | None]:
    """'1 234,56 €' -> (1234, 'EUR'); thousands/decimal separators vary."""
    if not text:
        return None, None
    flat = text.replace("\u00a0", " ")
    match = _MONEY_AFTER.search(flat)
    if match:
        raw = match.group(1).strip()
        code = SYMBOL_TO_CODE.get((match.group(2) or match.group(3) or "").strip())
    else:
        # symbol-first formats: "€47.48", "$29.99", "₴1 234"
        before = _MONEY_BEFORE.search(flat)
        if not before:
            return None, None
        raw = before.group(2).strip()
        code = SYMBOL_TO_CODE.get(before.group(1).strip())
    # 1.234,56 -> 1234.56 ; 1,234.56 -> 1234.56 ; 1 234 -> 1234
    if "," in raw and "." in raw:
        if raw.rfind(",") > raw.rfind("."):
            raw = raw.replace(".", "").replace(",", ".")
        else:
            raw = raw.replace(",", "")
    elif "," in raw:
        head, _, tail = raw.rpartition(",")
        raw = head.replace(",", "") + ("." + tail if len(tail) <= 2 else tail)
    elif "." in raw:
        head, _, tail = raw.rpartition(".")
        raw = head.replace(".", "") + ("." + tail if len(tail) <= 2 else tail)
    raw = re.sub(r"[^\d.]", "", raw)
    try:
        return int(round(float(raw))), code
    except ValueError:
        return None, code


# --------------------------------------------------------------------------
# Amazon (com / de / co.uk / fr / it / es): one markup for every domain
# --------------------------------------------------------------------------

AMAZON_CURRENCY = {
    "com": "USD", "de": "EUR", "co.uk": "GBP", "fr": "EUR",
    "it": "EUR", "es": "EUR", "ca": "CAD", "co.jp": "JPY",
}


async def amazon_search(domain: str, query: str, limit: int = 10,
                        page: int = 1) -> list[dict[str, Any]]:
    currency = AMAZON_CURRENCY.get(domain, "USD")
    url = f"https://www.amazon.{domain}/s"
    html = await fetch_text(url, {"k": query, "page": page}, profile="chrome",
                            source=f"amazon.{domain}")
    soup = BeautifulSoup(html, "html.parser")
    rows: list[dict[str, Any]] = []

    for card in soup.select('div[data-component-type="s-search-result"][data-asin]'):
        asin = card.get("data-asin") or ""
        if not asin:
            continue
        link = card.select_one('h2 a[href*="/dp/"]') or card.select_one('a[href*="/dp/"]')
        # Amazon renders two headings per card: the brand and the product name.
        # The longest one is the product name.
        headings = [h.get("aria-label") or h.get_text(" ", strip=True)
                    for h in card.select("h2")]
        title = max((h for h in headings if h), key=len, default="")
        if not title and link is not None:
            title = link.get_text(" ", strip=True)
        if not title:
            continue
        href = (link.get("href") if link else "") or f"/dp/{asin}"
        full = href if href.startswith("http") else f"https://www.amazon.{domain}{href}"
        _remember(f"amazon.{domain}", asin, full)

        price_el = card.select_one(".a-price .a-offscreen") or card.select_one(".a-price")
        price, cur = money(price_el.get_text(" ", strip=True) if price_el else None)
        row: dict[str, Any] = {
            "id": asin,
            "title": title.strip(),
            "price": price,
            "currency": cur or currency,
            "state": None,
            "availability": None,
            "city": None,
            "business": True,
            "relevance": _relevance(query, title),
            "url": full,
        }
        old_el = card.select_one(".a-price.a-text-price .a-offscreen")
        if old_el is not None:
            old, _ = money(old_el.get_text(" ", strip=True))
            if old and price and old > price:
                row["old_price"] = old
        stars = card.select_one("i.a-icon-star-small span.a-icon-alt") \
            or card.select_one("span.a-icon-alt")
        if stars is not None:
            match = re.search(r"([\d.,]+)", stars.get_text(" ", strip=True))
            if match:
                try:
                    row["rating"] = float(match.group(1).replace(",", "."))
                except ValueError:
                    pass
        rows.append(row)

    return rows[: max(limit * 2, 20)]


async def amazon_get(domain: str, asin: str, max_description_chars: int = 600) -> dict[str, Any]:
    url = f"https://www.amazon.{domain}/dp/{asin}"
    html = await fetch_text(url, profile="chrome", source=f"amazon.{domain}")
    soup = BeautifulSoup(html, "html.parser")
    title_el = soup.select_one("#productTitle")
    price_el = soup.select_one(".a-price .a-offscreen")
    price, cur = money(price_el.get_text(" ", strip=True) if price_el else None)
    bullets = soup.select("#feature-bullets li span.a-list-item")
    description = " ".join(el.get_text(" ", strip=True) for el in bullets).strip()
    if max_description_chars and len(description) > max_description_chars:
        description = description[:max_description_chars].rstrip() + "…"
    return {
        "source": f"amazon.{domain}",
        "id": asin,
        "title": title_el.get_text(" ", strip=True) if title_el else None,
        "price": price,
        "currency": cur or AMAZON_CURRENCY.get(domain),
        "description": description,
        "url": url,
    }


# --------------------------------------------------------------------------
# eBay — li.s-item, id from /itm/<digits>
# --------------------------------------------------------------------------

_EBAY_ID = re.compile(r"/itm/(?:[^/]*/)?(\d{9,15})")


async def ebay_search(domain: str, currency: str, query: str, limit: int = 10,
                      page: int = 1) -> list[dict[str, Any]]:
    url = f"https://www.{domain}/sch/i.html"
    params = {"_nkw": query, "_pgn": page}
    html = await fetch_text(url, params, profile="chrome", source=f"ebay.{domain}")
    soup = BeautifulSoup(html, "html.parser")
    rows: list[dict[str, Any]] = []

    for card in soup.select("li.s-item"):
        link = card.select_one("a.s-item__link[href]")
        title_el = card.select_one(".s-item__title")
        if link is None or title_el is None:
            continue
        match = _EBAY_ID.search(link.get("href") or "")
        if match is None:
            continue
        item_id = match.group(1)
        title = title_el.get_text(" ", strip=True)
        if not title or title.lower().startswith(("новый", "new listing")):
            continue
        href = link.get("href") or ""
        _remember(f"ebay.{domain}", item_id, href)
        price_el = card.select_one(".s-item__price")
        price, cur = money(price_el.get_text(" ", strip=True) if price_el else None)
        rows.append({
            "id": item_id,
            "title": title,
            "price": price,
            "currency": cur or currency,
            "state": None,
            "availability": None,
            "city": None,
            "business": False,
            "relevance": _relevance(query, title),
            "url": href,
        })

    return rows[: max(limit * 2, 20)]


async def ebay_get(domain: str, item_id: str, max_description_chars: int = 600) -> dict[str, Any]:
    url = f"https://www.{domain}/itm/{int(item_id)}"
    html = await fetch_text(url, profile="chrome", source=f"ebay.{domain}")
    soup = BeautifulSoup(html, "html.parser")
    title_el = soup.select_one("h1.x-item-title__mainTitle")
    price_el = soup.select_one(".x-price-primary span")
    price, cur = money(price_el.get_text(" ", strip=True) if price_el else None)
    desc_el = soup.select_one(".ux-layout-section-evo__item--body")
    description = desc_el.get_text(" ", strip=True) if desc_el else ""
    if max_description_chars and len(description) > max_description_chars:
        description = description[:max_description_chars].rstrip() + "…"
    return {
        "source": f"ebay.{domain}",
        "id": item_id,
        "title": title_el.get_text(" ", strip=True) if title_el else None,
        "price": price,
        "currency": cur or "USD",
        "description": description,
        "url": url,
    }


# --------------------------------------------------------------------------
# AliExpress — prices live right in the link parameters (pdp_npi)
# --------------------------------------------------------------------------

ALI_SEARCH = "https://www.aliexpress.com/w/wholesale-{slug}.html"
_ALI_ID = re.compile(r"/item/(\d{6,})\.html")
_ALI_NPI = re.compile(r"!([A-Z]{3})!([\d.]+)!([\d.]+)!")


async def aliexpress_search(query: str, limit: int = 10,
                            page: int = 1) -> list[dict[str, Any]]:
    slug = urllib.parse.quote_plus(query.strip()).replace("%20", "-").replace("+", "-")
    url = ALI_SEARCH.format(slug=slug)
    params = {"page": page} if page > 1 else None
    html = await fetch_text(url, params, source="aliexpress")
    soup = BeautifulSoup(html, "html.parser")
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()

    for card in soup.select("a.search-card-item[href]"):
        href = card.get("href") or ""
        match = _ALI_ID.search(href)
        if match is None:
            continue
        item_id = match.group(1)
        if item_id in seen:
            continue
        seen.add(item_id)
        image = card.select_one("img.product-img")
        title = (image.get("alt") if image else "") or ""
        title = html_lib.unescape(title).strip()
        if not title:
            title_el = card.select_one("[title]")
            title = html_lib.unescape(title_el.get("title") or "") if title_el else ""
        if not title:
            continue
        full = f"https:{href}" if href.startswith("//") else href
        _remember("aliexpress", item_id, full)

        price, old_price, currency = None, None, "USD"
        npi = _ALI_NPI.search(urllib.parse.unquote(href))
        if npi:
            currency = npi.group(1)
            old_price = int(round(float(npi.group(2))))
            price = int(round(float(npi.group(3))))
        else:
            price_el = card.select_one(".us--price-sale, [class*='price-sale']")
            price, currency = money(price_el.get_text(" ", strip=True) if price_el else None)
            currency = currency or "USD"

        row: dict[str, Any] = {
            "id": item_id,
            "title": title,
            "price": price,
            "currency": currency,
            "state": None,
            "availability": None,
            "city": None,
            "business": True,
            "relevance": _relevance(query, title),
            "url": full,
        }
        if old_price and price and old_price > price:
            row["old_price"] = old_price
        rows.append(row)

    return rows[: max(limit * 2, 20)]


async def aliexpress_get(item_id: str, max_description_chars: int = 600) -> dict[str, Any]:
    url = f"https://www.aliexpress.com/item/{int(item_id)}.html"
    html = await fetch_text(url, source="aliexpress")
    soup = BeautifulSoup(html, "html.parser")
    title_el = soup.select_one("h1") or soup.select_one("[data-pl='product-title']")
    price_el = soup.select_one("[class*='price-sale']") or soup.select_one(".product-price-value")
    price, cur = money(price_el.get_text(" ", strip=True) if price_el else None)
    desc_el = soup.select_one("[class*='detail-desc'], #product-description")
    description = desc_el.get_text(" ", strip=True) if desc_el else ""
    if max_description_chars and len(description) > max_description_chars:
        description = description[:max_description_chars].rstrip() + "…"
    return {
        "source": "aliexpress",
        "id": item_id,
        "title": title_el.get_text(" ", strip=True) if title_el else None,
        "price": price,
        "currency": cur or "USD",
        "description": description,
        "url": url,
    }


# --------------------------------------------------------------------------
# Rakuten (JP) — div.searchresultitem with data-track-price
# --------------------------------------------------------------------------


async def rakuten_search(query: str, limit: int = 10, page: int = 1) -> list[dict[str, Any]]:
    url = f"https://search.rakuten.co.jp/search/mall/{urllib.parse.quote(query)}/"
    params = {"p": page} if page > 1 else None
    html = await fetch_text(url, params, profile="chrome", source="rakuten")
    soup = BeautifulSoup(html, "html.parser")
    rows: list[dict[str, Any]] = []

    for card in soup.select("div.searchresultitem[data-id]"):
        title_link = card.select_one("h2 a[href]") or card.select_one("a[href*='item.rakuten.co.jp']")
        if title_link is None:
            continue
        item_id = card.get("data-id") or ""
        title = title_link.get_text(" ", strip=True)
        href = title_link.get("href") or ""
        _remember("rakuten", item_id, href)
        price = _int_from(card.get("data-track-price"))
        rows.append({
            "id": item_id,
            "title": title,
            "price": price,
            "currency": "JPY",
            "state": None,
            "availability": None,
            "city": None,
            "business": True,
            "relevance": _relevance(query, title),
            "url": href,
        })

    return rows[: max(limit * 2, 20)]


async def rakuten_get(item_id: str, max_description_chars: int = 600) -> dict[str, Any]:
    rows = _remembered_url("rakuten", item_id)
    if not rows:
        raise ExtSearchError("no_url", "run a search first: item URLs are remembered from results")
    html = await fetch_text(rows, profile="chrome", source="rakuten")
    soup = BeautifulSoup(html, "html.parser")
    title_el = soup.select_one("h1") or soup.select_one("[itemprop='name']")
    price_el = soup.select_one("[itemprop='price']") or soup.select_one(".price--ox_YS")
    price, cur = money(price_el.get_text(" ", strip=True) if price_el else None)
    return {
        "source": "rakuten",
        "id": item_id,
        "title": title_el.get_text(" ", strip=True) if title_el else None,
        "price": price,
        "currency": cur or "JPY",
        "description": "",
        "url": rows,
    }


def _remembered_url(source: str, offer_id: str) -> str | None:
    from ..catalog import _URLS
    return _URLS.get((source, offer_id)) or _URLS.get((source, _int_from(offer_id) or -1))


# --------------------------------------------------------------------------
# Yandex Market — cards with data-zone-name="productSnippet"
# --------------------------------------------------------------------------


async def yandex_search(query: str, limit: int = 10, page: int = 1) -> list[dict[str, Any]]:
    url = "https://market.yandex.ru/search"
    params = {"text": query}
    if page > 1:
        params["page"] = page
    html = await fetch_text(url, params, source="yandex_market")
    soup = BeautifulSoup(html, "html.parser")
    rows: list[dict[str, Any]] = []

    for card in soup.select('div[data-zone-name="productSnippet"]'):
        raw = card.get("data-zone-data")
        data: dict[str, Any] = {}
        if raw:
            try:
                data = json.loads(html_lib.unescape(raw))
            except ValueError:
                data = {}
        title_el = card.select_one('[data-baobab-name="title"] a') or card.select_one("h3 a")
        if title_el is None:
            continue
        title = title_el.get_text(" ", strip=True)
        href = title_el.get("href") or ""
        full = href if href.startswith("http") else f"https://market.yandex.ru{href}"
        offer_id = data.get("modelId") or data.get("marketSku") or data.get("sku")
        if not offer_id:
            match = re.search(r"/(\d{5,})", href)
            offer_id = match.group(1) if match else None
        if not offer_id:
            continue
        _remember("yandex_market", offer_id, full)
        price = data.get("price")
        if isinstance(price, dict):
            price = price.get("value")
        rows.append({
            "id": offer_id,
            "title": title,
            "price": int(price) if isinstance(price, (int, float)) else None,
            "currency": "RUB",
            "state": None,
            "availability": None,
            "city": None,
            "business": True,
            "relevance": _relevance(query, title),
            "url": full,
        })

    return rows[: max(limit * 2, 20)]


async def yandex_get(offer_id: Any, max_description_chars: int = 600) -> dict[str, Any]:
    url = _remembered_url("yandex_market", str(offer_id))
    if not url:
        raise ExtSearchError("no_url", "run a search first: item URLs are remembered from results")
    html = await fetch_text(url, source="yandex_market")
    soup = BeautifulSoup(html, "html.parser")
    title_el = soup.select_one("h1")
    price_el = soup.select_one("[data-auto='mainPrice']") or soup.select_one("[itemprop='price']")
    price, cur = money(price_el.get_text(" ", strip=True) if price_el else None)
    return {
        "source": "yandex_market",
        "id": offer_id,
        "title": title_el.get_text(" ", strip=True) if title_el else None,
        "price": price,
        "currency": cur or "RUB",
        "description": "",
        "url": url,
    }


# --------------------------------------------------------------------------
# Wildberries — public JSON API (needs a proxy in the region)
# --------------------------------------------------------------------------

WB_SEARCH = "https://search.wb.ru/exactmatch/ru/common/v5/search"


async def wildberries_search(query: str, limit: int = 10,
                             page: int = 1) -> list[dict[str, Any]]:
    params = {
        "appType": 1, "curr": "rub", "dest": -1257786, "query": query,
        "resultset": "catalog", "sort": "popular", "spp": 30,
        "page": page,
    }
    payload = await fetch_json(WB_SEARCH, params, source="wildberries")
    products = ((payload or {}).get("data") or {}).get("products") or []
    rows: list[dict[str, Any]] = []

    for item in products:
        name = item.get("name") or ""
        brand = item.get("brand") or ""
        title = f"{brand} {name}".strip() if brand else name
        if not title:
            continue
        price = item.get("salePriceU") or item.get("priceU")
        if price is None:
            sizes = item.get("sizes") or []
            if sizes:
                price = (((sizes[0] or {}).get("price") or {}).get("product"))
        rows.append({
            "id": item.get("id"),
            "title": title,
            "price": int(price) // 100 if isinstance(price, (int, float)) else None,
            "currency": "RUB",
            "state": None,
            "availability": None,
            "city": None,
            "business": True,
            "relevance": _relevance(query, title),
            "url": f"https://www.wildberries.ru/catalog/{item.get('id')}/detail.aspx",
            "rating": item.get("reviewRating"),
            "reviews_count": item.get("feedbacks"),
        })

    return rows[: max(limit * 2, 20)]


# --------------------------------------------------------------------------
# Ozon — composer API (experimental: widgetStates layout changes)
# --------------------------------------------------------------------------

OZON_API = "https://www.ozon.ru/api/composer-api.bx/page/json/v2"


async def ozon_search(query: str, limit: int = 10, page: int = 1) -> list[dict[str, Any]]:
    params = {"url": f"/search/?text={urllib.parse.quote(query)}&page={page}"}
    payload = await fetch_json(OZON_API, params, source="ozon")
    states = (payload or {}).get("widgetStates") or {}
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()

    for key, raw in states.items():
        if not (key.startswith("searchResultsV2") or key.startswith("searchResults")):
            continue
        try:
            data = json.loads(raw) if isinstance(raw, str) else raw
        except ValueError:
            continue
        for item in data.get("items") or []:
            sku = str(item.get("sku") or "")
            if not sku or sku in seen:
                continue
            seen.add(sku)
            title = _ozon_text(item.get("mainState") or []) or item.get("name") or ""
            if not title:
                continue
            link = ((item.get("action") or {}).get("link")) or f"/product/{sku}/"
            full = link if link.startswith("http") else f"https://www.ozon.ru{link}"
            price = None
            for other_key, other_raw in states.items():
                if not other_key.startswith("priceV2"):
                    continue
                try:
                    price_data = json.loads(other_raw) if isinstance(other_raw, str) else other_raw
                except ValueError:
                    continue
                if str(price_data.get("sku") or price_data.get("productId") or "") != sku:
                    continue
                price = _ozon_text(price_data.get("price") or [])
                break
            rows.append({
                "id": sku,
                "title": title,
                "price": price[0] if isinstance(price, tuple) else None,
                "currency": "RUB",
                "state": None,
                "availability": None,
                "city": None,
                "business": True,
                "relevance": _relevance(query, title),
                "url": full,
            })

    return rows[: max(limit * 2, 20)]


def _ozon_text(nodes: Any) -> "tuple[int, str] | str":
    """Extract text from the Ozon atom-node tree; a price becomes (number, text)."""
    texts: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key in ("text", "textAtom", "content") and isinstance(value, str):
                    texts.append(value)
                elif key == "textAtom" and isinstance(value, dict):
                    walk(value)
                else:
                    walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)
        elif isinstance(node, str):
            texts.append(node)

    walk(nodes)
    joined = " ".join(t.strip() for t in texts if t.strip())
    amount, _ = money(joined)
    if amount is not None:
        return amount, joined
    return joined


# --------------------------------------------------------------------------
# OLX factory: one codebase for every country where OLX operates
# --------------------------------------------------------------------------


def olx_adapter(domain: str, lang: str, currency: str):
    from ..olx import get_offer, search_offers

    async def search(query: str, *, limit: int = 10, page: int = 1,
                     price_min: int | None = None, price_max: int | None = None,
                     sort: str = "relevance", strict: bool = False,
                     include_urls: bool = False, free_only: bool = False,
                     **_: Any) -> dict[str, Any]:
        return await search_offers(
            query, limit=limit, offset=(max(1, page) - 1) * limit,
            price_min=price_min, price_max=price_max, sort=sort,
            strict=strict, include_urls=include_urls, free_only=free_only,
            domain=domain, lang=lang,
        )

    async def get(offer_id: Any, max_description_chars: int = 600) -> dict[str, Any]:
        data = await get_offer(int(offer_id), max_description_chars=max_description_chars,
                               domain=domain)
        data["transport"] = data.pop("source", None)
        data["source"] = f"olx_{domain.split('.')[-1]}"
        data["currency"] = data.get("currency") or currency
        return data

    return search, get


def raw_finish(rows: list[dict[str, Any]], *, source: str, query: str, limit: int,
               page: int, price_min: int | None, price_max: int | None,
               sort: str | None, strict: bool, include_urls: bool) -> dict[str, Any]:
    return _finish(rows, total=len(rows), page=page, limit=limit,
                   price_min=price_min, price_max=price_max, sort=sort,
                   strict=strict, query=query, source=source,
                   include_urls=include_urls)


def rows_search(source: str, loader):
    """Wrap a raw parser into a search function with filters and sorting."""

    async def search(query: str, *, limit: int = 10, page: int = 1,
                     price_min: int | None = None, price_max: int | None = None,
                     sort: str | None = None, strict: bool = False,
                     include_urls: bool = False, **_: Any) -> dict[str, Any]:
        rows = await loader(query, limit=limit, page=page)
        return raw_finish(rows, source=source, query=query, limit=limit, page=page,
                          price_min=price_min, price_max=price_max, sort=sort,
                          strict=strict, include_urls=include_urls)

    return search


def simple_get(getter):
    """Wrap a card parser: drop the optional kwargs some of them reject."""
    if getter is None:
        return None

    async def get(offer_id: Any, max_description_chars: int = 600) -> dict[str, Any]:
        try:
            return await getter(offer_id, max_description_chars=max_description_chars)
        except TypeError:
            return await getter(offer_id)

    return get


# --------------------------------------------------------------------------
# Generic CSS adapter for marketplaces whose markup cannot be verified from
# here (403/429 without a proxy). Such configs are marked verified=False.
# --------------------------------------------------------------------------

import zlib as _zlib  # noqa: E402


def _crc_id(url: str) -> int:
    return _zlib.crc32(url.encode("utf-8")) & 0x7FFFFFFF


async def css_search(
    source: str,
    url_template: str,
    *,
    query: str,
    limit: int = 10,
    page: int = 1,
    currency: str = "USD",
    item_sel: str,
    title_sel: str,
    link_sel: str,
    price_sel: str | None = None,
    old_price_sel: str | None = None,
    rating_sel: str | None = None,
    id_regex: str | None = None,
    profile: str | None = "chrome",
    page_param: str = "page",
) -> list[dict[str, Any]]:
    """Config-driven search: card -> title -> link -> price.

    When ``price_sel`` is omitted the price is found in the card text with a
    currency-symbol regex, which survives class renames better than a selector.
    """
    url = url_template.format(q=urllib.parse.quote_plus(query),
                              qdash=urllib.parse.quote(query.replace(" ", "-")))
    params = {page_param: page} if page > 1 else None
    html = await fetch_text(url, params, profile=profile, source=source)
    soup = BeautifulSoup(html, "html.parser")
    rows: list[dict[str, Any]] = []
    pattern = re.compile(id_regex) if id_regex else None
    seen: set[str] = set()

    for card in soup.select(item_sel):
        title_el = card.select_one(title_sel)
        link_el = card.select_one(link_sel)
        if title_el is None or link_el is None:
            continue
        title = (title_el.get("title") or title_el.get_text(" ", strip=True) or "").strip()
        href = link_el.get("href") or ""
        if not title or not href:
            continue
        full = href if href.startswith("http") else urllib.parse.urljoin(url, href)
        match = pattern.search(href) if pattern else None
        offer_id: Any = match.group(1) if match else _crc_id(full)
        if str(offer_id) in seen:
            continue
        seen.add(str(offer_id))
        _remember(source, offer_id, full)

        price, cur = (None, None)
        if price_sel:
            price_el = card.select_one(price_sel)
            price, cur = money(price_el.get_text(" ", strip=True) if price_el else None)
        if price is None:
            price, cur = money(card.get_text(" ", strip=True)[:400])

        row: dict[str, Any] = {
            "id": offer_id,
            "title": title,
            "price": price,
            "currency": cur or currency,
            "state": None,
            "availability": None,
            "city": None,
            "business": True,
            "relevance": _relevance(query, title),
            "url": full,
        }
        if old_price_sel:
            old_el = card.select_one(old_price_sel)
            if old_el is not None:
                old, _ = money(old_el.get_text(" ", strip=True))
                if old and price and old > price:
                    row["old_price"] = old
        if rating_sel:
            rating_el = card.select_one(rating_sel)
            if rating_el is not None:
                value = rating_el.get("content") or rating_el.get("aria-label") or rating_el.get_text(" ", strip=True)
                match_r = re.search(r"([\d.,]+)", value or "")
                if match_r:
                    try:
                        row["rating"] = float(match_r.group(1).replace(",", "."))
                    except ValueError:
                        pass
        rows.append(row)

    return rows[: max(limit * 2, 20)]
