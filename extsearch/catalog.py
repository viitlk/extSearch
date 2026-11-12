"""Ukrainian shops: Prom, Foxtrot, Rozetka, Comfy, Allo, Moyo, Kasta, Brain, Citrus.

Anti-bot rules (verified empirically on 2026-11-09):

    site      | plain curl/libcurl | impersonate=chrome
    ----------|--------------------|-------------------
    Prom.ua   | 200, full results  | 200 but an empty placeholder
    Foxtrot   | 200, full results  | not tested
    Rozetka   | 403                | product API works

That is why there are two separate HTTP clients: plain (Prom, Foxtrot) and
chrome (Rozetka API). Mixing them breaks everything.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import urllib.parse
from typing import Any

from bs4 import BeautifulSoup
from curl_cffi import requests as ccreq
from curl_cffi.requests.exceptions import RequestException

from . import details
from .errors import ExtSearchError as OlxError
from .proxies import for_request

TIMEOUT = 30.0
MIN_INTERVAL = 0.5
CACHE_TTL = 300

_plain: "ccreq.AsyncSession | None" = None
_chrome: "ccreq.AsyncSession | None" = None
_safari: "ccreq.AsyncSession | None" = None
_lock: asyncio.Lock | None = None
_last = 0.0
_cache: dict[str, tuple[float, Any]] = {}

# id -> url so market_get can open a card without a second search
_URLS: dict[tuple[str, int], str] = {}
# id -> search row: some shops render the price only via JS, and then it is
# more honest to reuse the search price than to return null
_ROWS: dict[tuple[str, int], dict[str, Any]] = {}


def _client(chrome: bool) -> "ccreq.AsyncSession":
    global _plain, _chrome
    if chrome:
        if _chrome is None:
            _chrome = ccreq.AsyncSession(impersonate="chrome", timeout=TIMEOUT)
        return _chrome
    if _plain is None:
        _plain = ccreq.AsyncSession(timeout=TIMEOUT)
    return _plain


def _safari_client() -> "ccreq.AsyncSession":
    """Fallback TLS profile.

    Comfy (and sometimes other Cloudflare sites) started answering 403 to the
    chrome fingerprint while Safari passes. A separate session keeps the main
    client untouched.
    """
    global _safari
    if _safari is None:
        _safari = ccreq.AsyncSession(impersonate="safari", timeout=TIMEOUT)
    return _safari


async def _throttle() -> None:
    global _lock, _last
    if _lock is None:
        _lock = asyncio.Lock()
    async with _lock:
        wait = MIN_INTERVAL - (time.monotonic() - _last)
        if wait > 0:
            await asyncio.sleep(wait)
        _last = time.monotonic()


async def _fetch(url: str, params: dict[str, Any] | None = None, *,
                 chrome: bool = False, as_json: bool = False,
                 retries: int = 2) -> Any:
    key = f"{'c' if chrome else 'p'}:{url}:{json.dumps(params or {}, sort_keys=True)}"
    hit = _cache.get(key)
    if hit and time.monotonic() < hit[0]:
        return hit[1]

    client = _client(chrome)
    last_err: OlxError | None = None

    for attempt in range(retries + 1):
        await _throttle()
        try:
            resp = await client.get(url, params=params,
                                     proxies=for_request())
        except RequestException as exc:
            last_err = OlxError("network", f"network request failed: {exc}")
            await asyncio.sleep(1.5 * (attempt + 1))
            continue

        if resp.status_code in (401, 403) and chrome:
            # The TLS fingerprint is the first suspect. One Safari attempt is
            # cheaper than reporting the whole shop as blocked.
            await _throttle()
            try:
                resp = await _safari_client().get(url, params=params,
                                                  proxies=for_request())
            except RequestException as exc:
                last_err = OlxError("network", f"network request failed: {exc}")
                await asyncio.sleep(1.5 * (attempt + 1))
                continue

        if resp.status_code == 404:
            raise OlxError("not_found", "page not found")
        if resp.status_code in (401, 403):
            raise OlxError(
                "blocked",
                f"{urllib.parse.urlsplit(url).hostname} blocked the request (HTTP 403). "
                "Rozetka needs a warmed browser profile; the other shops usually "
                "fail when impersonate is enabled.",
            )
        if resp.status_code == 429:
            last_err = OlxError("rate_limited", "HTTP 429: rate limited")
            await asyncio.sleep(4 * (attempt + 1))
            continue
        if resp.status_code >= 500:
            last_err = OlxError("server_error", f"HTTP {resp.status_code}")
            await asyncio.sleep(2 * (attempt + 1))
            continue

        value: Any = resp.json() if as_json else resp.text
        if len(_cache) > 256:
            _cache.clear()
        _cache[key] = (time.monotonic() + CACHE_TTL, value)
        return value

    raise last_err or OlxError("unknown", "could not fetch the page")


# --------------------------------------------------------------------------
# Shared helpers
# --------------------------------------------------------------------------

_WORD_RE = re.compile(r"[0-9a-zа-яіїєґё]+", re.IGNORECASE)
_SPACES_RE = re.compile(r"[ \t\u00a0]+")


def _relevance(query: str, title: str) -> float:
    want = {w for w in _WORD_RE.findall(query.lower()) if len(w) > 1}
    if not want:
        return 1.0
    have = set(_WORD_RE.findall(title.lower()))
    return round(len(want & have) / len(want), 3)


def _price_from_text(raw: str | None) -> int | None:
    if not raw:
        return None
    digits = re.sub(r"[^\d]", "", _SPACES_RE.sub(" ", raw).split("₴")[0].split("грн")[0])
    return int(digits) if digits else None


def _remember(source: str, offer_id: Any, url: str | None,
              row: dict[str, Any] | None = None) -> None:
    if not offer_id:
        return
    if len(_URLS) > 2048:
        _URLS.clear()
        _ROWS.clear()
    if url:
        _URLS[(source, str(offer_id))] = url
    if row is not None:
        _ROWS[(source, str(offer_id))] = dict(row)


def _cached_row(source: str, offer_id: int) -> dict[str, Any]:
    return _ROWS.get((source, str(offer_id))) or {}


def _fetch_url(source: str, offer_id: int) -> str:
    url = _URLS.get((source, str(offer_id)))
    if not url:
        raise OlxError(
            "no_url",
            f"no known URL for {source}:{offer_id}; run a search on this "
            "marketplace first, item URLs are remembered from search results.",
        )
    return url


def _longest_link(card: Any, needle: str) -> Any:
    """Longest-text link: separates the product card from icon links."""
    best = None
    best_len = -1
    for a in card.select("a[href]"):
        href = a.get("href") or ""
        if needle not in href:
            continue
        size = len(a.get_text(strip=True))
        if size > best_len:
            best, best_len = a, size
    return best


def _int_from(text: str | None) -> int | None:
    if not text:
        return None
    digits = re.sub(r"\D", "", text)
    return int(digits) if digits else None


def _rating(row: dict[str, Any], card: Any, *, rating_sel: str | None,
            reviews_sel: str | None) -> None:
    """Add rating/reviews to a search row when the markup provides them."""
    if rating_sel:
        el = card.select_one(rating_sel)
        if el is not None:
            try:
                row["rating"] = float(el.get_text(strip=True).replace(",", ".")[:4])
            except ValueError:
                pass
    if reviews_sel:
        el = card.select_one(reviews_sel)
        if el is not None:
            count = _int_from(el.get_text(strip=True))
            if count is not None:
                row["reviews_count"] = count


def _finish(rows: list[dict[str, Any]], *, total: int, page: int, limit: int,
            price_min: int | None, price_max: int | None, sort: str | None,
            strict: bool, query: str, source: str,
            include_urls: bool = False) -> dict[str, Any]:
    """Shared post-processing: price filter, strict mode, sorting."""
    out = []
    for row in rows:
        if price_min is not None and (row["price"] is None or row["price"] < price_min):
            continue
        if price_max is not None and (row["price"] is None or row["price"] > price_max):
            continue
        if strict and row["relevance"] <= 0:
            continue
        if include_urls:
            row["url"] = _URLS.get((source, str(row["id"])))
        out.append(row)

    # Remember search rows: some shops (Allo) render the price only via JS and
    # the product card has no price to read.
    for row in out:
        try:
            _ROWS[(source, str(row["id"]))] = dict(row)
        except (KeyError, TypeError, ValueError):
            continue

    if sort == "cheapest":
        out.sort(key=lambda r: (r["price"] is None, r["price"] or 0))
    elif sort == "expensive":
        out.sort(key=lambda r: (r["price"] is None, -(r["price"] or 0)))
    elif sort in ("rating", "rating_asc"):
        # Items without a rating go last, so "best reviews" does not start with
        # empty cards.
        out.sort(key=lambda r: (r.get("rating") is None,
                                -r.get("rating", 0) if sort == "rating"
                                else r.get("rating", 0)))
    elif sort in (None, "relevance", ""):
        out.sort(key=lambda r: -r["relevance"])

    return {
        "source": source,
        "query": query,
        "page": page,
        "limit": limit,
        "returned": len(out),
        "total": total,
        "items": out[:limit],
    }


# --------------------------------------------------------------------------
# Prom.ua — HTML, stable data-qaid anchors
# --------------------------------------------------------------------------

PROM_SEARCH = "https://prom.ua/ua/search"
_PROM_ID_RE = re.compile(r"/m-?(\d+)-")  # some products use a negative id: /ua/m-4104...


async def prom_search(query: str, limit: int = 10, page: int = 1) -> list[dict[str, Any]]:
    html = await _fetch(PROM_SEARCH, {"search_term": query, "page": page})
    soup = BeautifulSoup(html, "html.parser")
    rows: list[dict[str, Any]] = []

    for link in soup.select('a[data-qaid="product_link"]'):
        href = link.get("href") or ""
        m = _PROM_ID_RE.search(href)
        if not m:
            continue
        title_el = link.select_one('[data-qaid="product_name"]')
        title = (title_el.get_text(strip=True) if title_el else "") or link.get("title", "")

        # price/presence live in the card ancestor, not inside the link
        node = link.parent
        price_raw = None
        presence = None
        for _ in range(6):
            if node is None:
                break
            pe = node.select_one('[data-qaid="product_price"]')
            if pe is not None:
                price_raw = pe.get("data-qaprice") or pe.get_text(strip=True)
                pr = node.select_one('[data-qaid="product_presence"]')
                presence = pr.get_text(strip=True) if pr else None
                break
            node = node.parent

        offer_id = int(m.group(1))
        url = href if href.startswith("http") else f"https://prom.ua{href}"
        _remember("prom", offer_id, url)
        rows.append({
            "id": offer_id,
            "title": title,
            "price": _price_from_text(price_raw),
            "currency": "UAH",
            "state": None,
            "availability": presence,
            "city": None,
            "business": True,
            "relevance": _relevance(query, title),
        })

    return rows


async def prom_get(offer_id: int, max_description_chars: int = 600) -> dict[str, Any]:
    url = _fetch_url("prom", offer_id)
    html = await _fetch(url)
    soup = BeautifulSoup(html, "html.parser")

    h1 = soup.select_one("h1")
    title = h1.get_text(strip=True) if h1 else None
    if not title:
        og = soup.select_one("meta[property='og:title']")
        if og is not None and og.get("content"):
            title = og["content"].strip()
        elif soup.title is not None:
            title = soup.title.get_text(strip=True) or None

    price = None
    for sel in ('[data-qaid="product_price"]', ".b-product-price__value",
                '[itemprop="price"]'):
        el = soup.select_one(sel)
        if el is not None:
            price = _price_from_text(el.get("content") or el.get("data-qaprice")
                                     or el.get_text(strip=True))
            if price is not None:
                break

    desc_el = soup.select_one('[data-qaid="descriptions"], [data-qaid="product_description"], #description')
    description = desc_el.get_text(" ", strip=True) if desc_el else ""
    if max_description_chars and len(description) > max_description_chars:
        description = description[:max_description_chars].rstrip() + "…"

    seller = None
    s_el = soup.select_one('[data-qaid="company_name"], .b-seller__title')
    if s_el:
        seller = s_el.get_text(strip=True)

    result = {
        "source": "prom",
        "id": offer_id,
        "title": title,
        "price": price,
        "currency": "UAH",
        "description": description,
        "seller": seller,
        "url": url,
    }
    result.update(details.enrich(html, details.SHOPS["prom"], soup=soup))
    return result


# --------------------------------------------------------------------------
# Foxtrot — HTML, stable product-card__* classes
# --------------------------------------------------------------------------

FOXTROT_SEARCH = "https://www.foxtrot.com.ua/uk/search"
_FX_CODE_RE = re.compile(r"(\d{5,9})")


async def foxtrot_search(query: str, limit: int = 10, page: int = 1) -> list[dict[str, Any]]:
    params = {"query": query}
    if page > 1:
        params["page"] = page
    html = await _fetch(FOXTROT_SEARCH, params)
    soup = BeautifulSoup(html, "html.parser")
    rows: list[dict[str, Any]] = []
    seen: set[int] = set()

    for card in soup.select("div.product-card"):
        title_el = card.select_one(".product-card__title")
        link_el = card.select_one("a[data-ecom]") or card.select_one("a[href*='/shop/']")
        if not (title_el and link_el):
            continue
        href = link_el.get("href") or ""
        code_el = card.select_one(".info-article")
        code = None
        if code_el:
            m = _FX_CODE_RE.search(code_el.get_text())
            code = int(m.group(1)) if m else None
        if code is None:
            m = _FX_CODE_RE.search(href.replace(".html", "")[::-1])
            code = int(m.group(1)[::-1]) if m else None
        if code is None or code in seen:
            continue
        seen.add(code)

        price_el = card.select_one(".price--current") or card.select_one(".product-prices")
        out_of_stock = card.select_one(".product-card__button[disabled]") is not None
        title = title_el.get_text(strip=True)
        url = href if href.startswith("http") else f"https://www.foxtrot.com.ua{href}"
        _remember("foxtrot", code, url)
        rows.append({
            "id": code,
            "title": title,
            "price": _price_from_text(price_el.get_text() if price_el else None),
            "currency": "UAH",
            "state": None,
            "availability": "out of stock" if out_of_stock else "in stock",
            "city": None,
            "business": True,
            "relevance": _relevance(query, title),
        })

    return rows[: max(limit * 2, 20)]


async def foxtrot_get(code: int, max_description_chars: int = 600) -> dict[str, Any]:
    url = _fetch_url("foxtrot", code)
    html = await _fetch(url)
    soup = BeautifulSoup(html, "html.parser")

    h1 = soup.select_one("h1")
    title = h1.get_text(strip=True) if h1 else None
    price_el = soup.select_one(".price--current, .product-prices__price, [itemprop='price']")
    price = _price_from_text(price_el.get("content") or price_el.get_text()
                             if price_el else None)

    desc_el = soup.select_one(".pdp-description, .pdp-short-description, "
                              "[itemprop='description'], .product-about__text")
    description = desc_el.get_text(" ", strip=True) if desc_el else ""
    if max_description_chars and len(description) > max_description_chars:
        description = description[:max_description_chars].rstrip() + "…"

    result = {
        "source": "foxtrot",
        "id": code,
        "title": title,
        "price": price,
        "currency": "UAH",
        "description": description,
        "url": url,
    }
    result.update(details.enrich(html, details.SHOPS["foxtrot"], soup=soup))
    return result


# --------------------------------------------------------------------------
# Rozetka — the product API works, search needs a live browser session
# --------------------------------------------------------------------------

ROZETKA_GOODS = "https://rozetka.com.ua/api/product-api/v4/goods/get-main"
ROZETKA_SEARCH_PAGE = "https://rozetka.com.ua/ua/search/"


async def rozetka_get(goods_id: int) -> dict[str, Any]:
    payload = await _fetch(
        ROZETKA_GOODS,
        {"front-type": "xl", "country": "UA", "lang": "ua", "goodsId": int(goods_id)},
        chrome=True, as_json=True,
    )
    item = (payload or {}).get("data")
    if not isinstance(item, dict):
        raise OlxError("not_found", f"Rozetka: product {goods_id} not found")

    result = {
        "source": "rozetka",
        "id": item.get("id"),
        "title": item.get("title"),
        "price": int(item["price"]) if isinstance(item.get("price"), (int, float)) else None,
        "old_price": item.get("old_price") or None,
        "currency": "UAH",
        "status": item.get("status"),
        "sell_status": item.get("sell_status"),
        "rating": item.get("comments_mark"),
        "reviews": item.get("comments_amount"),
        "category_id": item.get("category_id"),
        "url": item.get("href"),
    }
    # The product page is behind Cloudflare, so delivery/payment come from
    # the shop passport and the rating from the same API response.
    result.update(details.enrich("", details.SHOPS["rozetka"]))
    if item.get("comments_mark") is not None or item.get("comments_amount"):
        result["reviews_info"] = {
            "rating": item.get("comments_mark"),
            "count": item.get("comments_amount"),
        }
        result["reviews"] = result.pop("reviews")
    return result


async def rozetka_search(query: str, limit: int = 10, page: int = 1,
                         profile_dir: str | None = None,
                         timeout_ms: int = 25000) -> list[dict[str, Any]]:
    """Rozetka search through a real browser.

    Rozetka sits behind a Cloudflare challenge: neither curl nor headless
    without cookies gets through. A persistent profile with the challenge
    already solved is required (open the site once in that profile by hand).

    Data is not taken from the DOM (unstable classes): the page is rendered,
    goodsId values are extracted from links like /123456789/p123456789/, and the
    working get-main API enriches them. Parsing then survives layout changes.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise OlxError(
            "browser_unavailable",
            f"Playwright is not available ({exc}). Install the browser "
            "extra: pip install playwright && playwright install chromium",
        )

    import os
    from pathlib import Path

    profile = Path(profile_dir or os.environ.get(
        "ROZETKA_PROFILE", Path.home() / ".local/share/ua-marketplace/rz-profile"))
    profile.mkdir(parents=True, exist_ok=True)

    params = {"text": query}
    if page > 1:
        params["page"] = page
    url = ROZETKA_SEARCH_PAGE + "?" + urllib.parse.urlencode(params)

    try:
        async with async_playwright() as p:
            ctx = await p.chromium.launch_persistent_context(
                str(profile), headless=os.environ.get("ROZETKA_HEADFUL") != "1",
                locale="uk-UA", viewport={"width": 1440, "height": 900},
            )
            try:
                page_obj = ctx.pages[0] if ctx.pages else await ctx.new_page()
                await page_obj.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                try:
                    await page_obj.wait_for_selector(
                        'a[href*="/p"]:has(img), rz-product-catalog, .catalog-grid',
                        timeout=timeout_ms,
                    )
                except Exception:
                    pass
                html = await page_obj.content()
            finally:
                await ctx.close()
    except OlxError:
        raise
    except Exception as exc:  # noqa: BLE001
        # Playwright raises its own Error when the browser is not installed.
        raise OlxError(
            "browser_unavailable",
            f"browser engine is unavailable ({exc}). "
            "Run: python -m playwright install chromium",
        )

    ids: list[int] = []
    for m in re.finditer(r"/(\d{6,10})/p\d{6,10}/", html):
        gid = int(m.group(1))
        if gid not in ids:
            ids.append(gid)

    if not ids:
        raise OlxError(
            "needs_session",
            "Rozetka returned a page without products — the Cloudflare challenge was not passed. "
            "Run tools/warmup_rozetka.py once from a desktop terminal: a window "
            "opens, pass the challenge, and the cookies stay in the profile.",
        )

    rows: list[dict[str, Any]] = []
    for gid in ids[:limit]:
        try:
            data = await rozetka_get(gid)
        except OlxError:
            continue
        rows.append({
            "id": gid,
            "title": data.get("title"),
            "price": data.get("price"),
            "currency": "UAH",
            "state": data.get("status"),
            "availability": data.get("sell_status"),
            "city": None,
            "business": True,
            "relevance": _relevance(query, data.get("title") or ""),
            "url": data.get("url"),
        })
    return rows


# --------------------------------------------------------------------------
# Comfy — electronics, HTML + chrome impersonate (plain gets 403)
# --------------------------------------------------------------------------

COMFY_SEARCH = "https://comfy.ua/ua/search/"


async def comfy_search(query: str, limit: int = 10, page: int = 1) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"q": query}
    if page > 1:
        params["page"] = page
    html = await _fetch(COMFY_SEARCH, params, chrome=True)
    soup = BeautifulSoup(html, "html.parser")
    rows: list[dict[str, Any]] = []

    for card in soup.select("div.product-tile-catalog"):
        title_el = card.select_one(".product-tile-title__name")
        link = _longest_link(card, ".html")
        if title_el is None or link is None:
            continue
        title = title_el.get_text(strip=True)
        code = _int_from((card.select_one(".product-tile-preview__sku") or card).get_text()) \
            if card.select_one(".product-tile-preview__sku") else None
        href = link.get("href") or ""
        url = href if href.startswith("http") else f"https://comfy.ua{href}"
        if code is None:
            code = _int_from(url.rsplit("/", 1)[-1])
        if code is None:
            continue
        _remember("comfy", code, url)
        row = {
            "id": code,
            "title": title,
            "price": _price_from_text((card.select_one(".product-tile-price__current") or card).get_text()
                                       if card.select_one(".product-tile-price__current") else None),
            "currency": "UAH",
            "state": None,
            "availability": None,
            "city": None,
            "business": True,
            "relevance": _relevance(query, title),
        }
        old_el = card.select_one(".product-tile-price__old")
        if old_el is not None:
            row["old_price"] = _price_from_text(old_el.get_text())
        _rating(row, card, rating_sel=".rating-number-box__value",
                reviews_sel=".product-tile-feedback__reviews")
        rows.append(row)

    return rows[: max(limit * 2, 20)]


async def comfy_get(code: int, max_description_chars: int = 600) -> dict[str, Any]:
    return await _product_page(code, "comfy", max_description_chars,
                               title_sel="h1",
                               price_sel=".price__current, .product-tile-price__current, [itemprop='price']",
                               desc_sel=".product-description, .pdp-description, [itemprop='description']")


# --------------------------------------------------------------------------
# Allo — electronics, HTML + chrome
# --------------------------------------------------------------------------

ALLO_SEARCH = "https://allo.ua/ua/catalogsearch/result/"


async def allo_search(query: str, limit: int = 10, page: int = 1) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"q": query}
    if page > 1:
        params["page"] = page
    html = await _fetch(ALLO_SEARCH, params, chrome=True)
    soup = BeautifulSoup(html, "html.parser")
    rows: list[dict[str, Any]] = []

    for card in soup.select(".product-card"):
        title_el = card.select_one(".product-card__title")
        link = _longest_link(card, "allo.ua")
        if title_el is None or link is None:
            continue
        title = title_el.get_text(strip=True)
        href = link.get("href") or ""
        url = href if href.startswith("http") else f"https://allo.ua{href}"
        sku = card.select_one(".product-sku__value")
        offer_id = _int_from(sku.get_text() if sku else None)
        if offer_id is None:
            continue
        _remember("allo", offer_id, url)
        row = {
            "id": offer_id,
            "title": title,
            "price": _price_from_text((card.select_one(".v-pb__cur") or card).get_text()
                                       if card.select_one(".v-pb__cur") else None),
            "currency": "UAH",
            "state": None,
            "availability": None,
            "city": None,
            "business": True,
            "relevance": _relevance(query, title),
        }
        old_el = card.select_one(".v-pb__old")
        if old_el is not None:
            row["old_price"] = _price_from_text(old_el.get_text())
        rows.append(row)

    return rows[: max(limit * 2, 20)]


async def allo_get(offer_id: int, max_description_chars: int = 600) -> dict[str, Any]:
    return await _product_page(offer_id, "allo", max_description_chars,
                               title_sel="h1",
                               price_sel=".v-pb__cur, .p-price, [itemprop='price']",
                               desc_sel=".p-description, [itemprop='description']")


# --------------------------------------------------------------------------
# Moyo — electronics and home goods, HTML + chrome
# --------------------------------------------------------------------------

MOYO_SEARCH = "https://www.moyo.ua/ua/search/new/"


async def moyo_search(query: str, limit: int = 10, page: int = 1) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"q": query}
    if page > 1:
        params["page"] = page
    html = await _fetch(MOYO_SEARCH, params, chrome=True)
    soup = BeautifulSoup(html, "html.parser")
    rows: list[dict[str, Any]] = []

    for card in soup.select(".product-card"):
        title_el = card.select_one(".product-card_title")
        link = _longest_link(card, ".html")
        if title_el is None or link is None:
            continue
        title = title_el.get_text(strip=True)
        href = link.get("href") or ""
        url = href if href.startswith("http") else f"https://www.moyo.ua{href}"
        offer_id = _int_from(url.rsplit("/", 1)[-1])
        if offer_id is None or offer_id <= 0:
            continue
        _remember("moyo", offer_id, url)
        row = {
            "id": offer_id,
            "title": title,
            "price": _price_from_text((card.select_one(".product-card_price_current") or card).get_text()
                                       if card.select_one(".product-card_price_current") else None),
            "currency": "UAH",
            "state": None,
            "availability": None,
            "city": None,
            "business": True,
            "relevance": _relevance(query, title),
        }
        old_el = card.select_one(".product-card_price_oldprice")
        if old_el is not None:
            row["old_price"] = _price_from_text(old_el.get_text())
        rows.append(row)

    return rows[: max(limit * 2, 20)]


async def moyo_get(offer_id: int, max_description_chars: int = 600) -> dict[str, Any]:
    return await _product_page(offer_id, "moyo", max_description_chars,
                               title_sel="h1",
                               price_sel=".product-card_price_current, .price--current, [itemprop='price']",
                               desc_sel=".product-description, .js-product-description, [itemprop='description']")


# --------------------------------------------------------------------------
# Kasta — fashion, beauty, electronics, HTML + chrome
# --------------------------------------------------------------------------

KASTA_SEARCH = "https://kasta.ua/uk/search/"


async def kasta_search(query: str, limit: int = 10, page: int = 1) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"q": query}
    if page > 1:
        params["page"] = page
    html = await _fetch(KASTA_SEARCH, params, chrome=True)
    soup = BeautifulSoup(html, "html.parser")
    rows: list[dict[str, Any]] = []

    for card in soup.select("article.p__item"):
        link = _longest_link(card, "/product/")
        if link is None:
            continue
        title = re.sub(r"^Акція:\s*(?:\d+\s*днів?,?\s*)?\d{1,2}:\d{2}:\d{2}\s*", "",
                       link.get_text(" ", strip=True)).strip()
        if not title:
            continue
        href = link.get("href") or ""
        url = href if href.startswith("http") else f"https://kasta.ua{href}"
        offer_id = _int_from(url.split("/product/")[-1].split(":")[0]) if "/product/" in url else None
        if offer_id is None:
            continue
        _remember("kasta", offer_id, url)
        row = {
            "id": offer_id,
            "title": title,
            "price": _price_from_text((card.select_one(".t-18") or card).get_text()
                                       if card.select_one(".t-18") else None),
            "currency": "UAH",
            "state": None,
            "availability": None,
            "city": None,
            "business": True,
            "relevance": _relevance(query, title),
        }
        old_el = card.select_one(".t-striked")
        if old_el is not None:
            row["old_price"] = _price_from_text(old_el.get_text())
        rev_el = card.select_one("a[href*='#reviews']")
        if rev_el is not None:
            count = _int_from(rev_el.get_text(strip=True))
            if count is not None:
                row["reviews_count"] = count
        rows.append(row)

    return rows[: max(limit * 2, 20)]


async def kasta_get(offer_id: int, max_description_chars: int = 600) -> dict[str, Any]:
    return await _product_page(offer_id, "kasta", max_description_chars,
                               title_sel="h1",
                               price_sel=".t-18, [class*='price']",
                               desc_sel="[class*='description'], [itemprop='description']")


# --------------------------------------------------------------------------
# Brain — electronics, HTML + chrome (plain serves the same pages, slower)
# --------------------------------------------------------------------------

BRAIN_SEARCH = "https://brain.com.ua/ukr/search/"


async def brain_search(query: str, limit: int = 10, page: int = 1) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"Search": query}
    if page > 1:
        params["page"] = page
    html = await _fetch(BRAIN_SEARCH, params, chrome=True)
    soup = BeautifulSoup(html, "html.parser")
    rows: list[dict[str, Any]] = []

    for card in soup.select(".goods-block__item"):
        add = card.select_one("a.add")
        price_el = card.select_one(".br-pp-price")
        url = next((a.get("href") for a in card.select("a[href]")
                    if "-p" in (a.get("href") or "") and (a.get("href") or "").endswith(".html")), None)
        if url is None:
            continue
        offer_id = _int_from(add.get("data-pid") if add else None)
        if offer_id is None:
            offer_id = _int_from(url.rsplit("-p", 1)[-1])
        if offer_id is None:
            continue

        title = (add.get("data-name-ua") or add.get("data-name")) if add else None
        if not title:
            img = card.select_one("img[alt]")
            title = img.get("alt") if img is not None else ""
        title = (title or "").strip()
        if not title:
            continue

        price = _int_from(add.get("data-price") if add else None)
        if price is None and price_el is not None:
            for span in price_el.select("span"):
                if "data_io" in (span.get("class") or []):
                    continue  # service price in dollars / per unit
                price = _int_from(span.get_text(strip=True))
                if price is not None:
                    break

        _remember("brain", offer_id, url)
        row = {
            "id": offer_id,
            "title": title,
            "price": price,
            "currency": "UAH",
            "state": None,
            "availability": None,
            "city": None,
            "business": True,
            "relevance": _relevance(query, title),
        }
        # Brain keeps the rating in ".br-pp-r": star images plus a number.
        # 0.0 means "no reviews", such a rating is not returned.
        r_el = card.select_one(".br-pp-r")
        if r_el is not None:
            spans = r_el.select("span")
            rating_text = spans[-1].get_text(strip=True) if spans else ""
            try:
                rating = float(rating_text.replace(",", "."))
            except ValueError:
                rating = 0.0
            if rating > 0:
                row["rating"] = round(rating, 2)
        rows.append(row)

    return rows[: max(limit * 2, 20)]


async def brain_get(offer_id: int, max_description_chars: int = 600) -> dict[str, Any]:
    return await _product_page(offer_id, "brain", max_description_chars,
                               title_sel="h1",
                               price_sel=".br-pp-price [itemprop='price'], .br-main-price, "
                                         ".br-pp-price span, [itemprop='price']",
                               desc_sel=".br-characteristics, .product-description, "
                                        "[itemprop='description']")


# --------------------------------------------------------------------------
# Citrus (ctrs.com.ua) — electronics, Next.js HTML
# --------------------------------------------------------------------------

CTRS_SEARCH = "https://ctrs.com.ua/search"
_CTRS_ID_RE = re.compile(r"-(\d{4,})\.html$")
_CTRS_STAR_GREY = "221, 221, 221"


async def ctrs_search(query: str, limit: int = 10, page: int = 1) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"query": query}
    if page > 1:
        params["page"] = page
    html = await _fetch(CTRS_SEARCH, params, chrome=True)
    soup = BeautifulSoup(html, "html.parser")
    rows: list[dict[str, Any]] = []

    for card in soup.select("[class*='MainProductCard-module__root']"):
        link = card.select_one("a[href]")
        if link is None:
            continue
        href = link.get("href") or ""
        m = _CTRS_ID_RE.search(href)
        if not m:
            continue
        offer_id = int(m.group(1))
        title_el = card.select_one("[class*='MainProductCard-module__title']")
        title = (title_el.get_text(" ", strip=True) if title_el else "") or link.get("title", "")
        title = (title or "").strip()
        if not title:
            continue

        url = href if href.startswith("http") else f"https://ctrs.com.ua{href}"
        price_el = card.select_one("[data-price]")
        price = _int_from(price_el.get("data-price") if price_el is not None else None)
        _remember("ctrs", offer_id, url)
        row = {
            "id": offer_id,
            "title": title,
            "price": price,
            "currency": "UAH",
            "state": None,
            "availability": None,
            "city": None,
            "business": True,
            "relevance": _relevance(query, title),
        }
        old_el = card.select_one("[data-old-price]")
        if old_el is not None:
            old_price = _int_from(old_el.get("data-old-price"))
            if old_price:
                row["old_price"] = old_price

        # Citrus draws the rating as SVG stars without a number: count filled ones.
        rating_root = card.select_one("[class*='Rating-module__root']")
        if rating_root is not None:
            fills = re.findall(r'fill="rgb\(([^)]*)\)"', str(rating_root))
            filled = sum(1 for f in fills if f.strip() != _CTRS_STAR_GREY)
            if filled:
                row["rating"] = float(filled)
        rows.append(row)

    return rows[: max(limit * 2, 20)]


async def ctrs_get(offer_id: int, max_description_chars: int = 600) -> dict[str, Any]:
    return await _product_page(offer_id, "ctrs", max_description_chars,
                               title_sel="h1",
                               price_sel="[data-price], .price, [itemprop='price']",
                               desc_sel="[class*='description'], [itemprop='description']")


async def _product_page(offer_id: int, source: str, max_description_chars: int, *,
                        title_sel: str, price_sel: str, desc_sel: str) -> dict[str, Any]:
    """Shared product-card parser for the newer shops."""
    url = _fetch_url(source, int(offer_id))
    try:
        html = await _fetch(url, chrome=True)
    except OlxError as exc:
        if exc.code != "blocked":
            raise
        # Shops throttle frequent requests: a pause and one retry is enough.
        await asyncio.sleep(2.5)
        html = await _fetch(url, chrome=True)
    soup = BeautifulSoup(html, "html.parser")

    title_el = soup.select_one(title_sel)
    title = title_el.get_text(strip=True) if title_el else None

    price = None
    for sel in price_sel.split(","):
        el = soup.select_one(sel.strip())
        if el is None:
            continue
        price = _price_from_text(el.get("content") or el.get_text(" ", strip=True))
        if price is not None:
            break

    desc_el = soup.select_one(desc_sel)
    description = desc_el.get_text(" ", strip=True) if desc_el else ""
    if max_description_chars and len(description) > max_description_chars:
        description = description[:max_description_chars].rstrip() + "…"

    cached = _cached_row(source, int(offer_id))
    result: dict[str, Any] = {
        "source": source,
        "id": int(offer_id),
        "title": title or cached.get("title"),
        "price": price if price is not None else cached.get("price"),
        "currency": "UAH",
        "description": description,
        "url": url,
    }
    if price is None and cached.get("price") is not None:
        result["price_source"] = "search_card"
    if cached.get("old_price") is not None:
        result["old_price"] = cached["old_price"]
    if cached.get("rating") is not None:
        result.setdefault("rating", cached["rating"])
    if cached.get("reviews_count") is not None:
        result.setdefault("reviews_count", cached["reviews_count"])
    result.update(details.enrich(html, details.SHOPS.get(source), soup=soup))
    # The search rating is more precise: shops like Brain draw star images
    # and round 3.5 up to 4.0 on the card. So the search value wins and the
    # page value is kept in *_from_page.
    cached_rating = cached.get("rating")
    if cached_rating is not None:
        reviews = result.get("reviews") or {}
        if reviews.get("rating") is not None and reviews["rating"] != cached_rating:
            reviews["rating_from_page"] = reviews["rating"]
        reviews["rating"] = cached_rating
        reviews.setdefault("count", cached.get("reviews_count"))
        reviews.setdefault("items", [])
        reviews.setdefault("source", "search_card")
        result["reviews"] = reviews
    if "reviews" not in result and (cached.get("rating") is not None
                                   or cached.get("reviews_count") is not None):
        result["reviews"] = {"rating": cached.get("rating"),
                             "count": cached.get("reviews_count"),
                             "items": [], "source": "search_card"}
    return result

# --------------------------------------------------------------------------
# Generic wrappers
# --------------------------------------------------------------------------

SEARCHERS = {
    "prom": prom_search,
    "foxtrot": foxtrot_search,
    "rozetka": rozetka_search,
    "comfy": comfy_search,
    "allo": allo_search,
    "moyo": moyo_search,
    "kasta": kasta_search,
    "brain": brain_search,
    "ctrs": ctrs_search,
}


async def market_search(source: str, query: str, *, limit: int = 10, page: int = 1,
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


GETTERS = {
    "prom": prom_get,
    "foxtrot": foxtrot_get,
    "rozetka": rozetka_get,
    "comfy": comfy_get,
    "allo": allo_get,
    "moyo": moyo_get,
    "kasta": kasta_get,
    "brain": brain_get,
    "ctrs": ctrs_get,
}


async def market_get(source: str, offer_id: int, *,
                     max_description_chars: int = 600) -> dict[str, Any]:
    getter = GETTERS.get(source)
    if getter is None:
        raise OlxError("bad_argument",
                       f"source must be one of: {', '.join(GETTERS)}")
    if source == "rozetka":
        return await getter(int(offer_id))
    return await getter(int(offer_id), max_description_chars)
