"""OLX classifieds: one codebase for every country (ua, pl, ro, bg, pt, kz, uz).

Data comes from the public JSON API (``/api/v1/offers``): same items as the
website, no Cloudflare challenge, no headless browser, ~0.3 s instead of ~8 s.
HTML parsing inside ``window.__PRERENDERED_STATE__`` is kept as a fallback, and
the old Playwright path remains as ``parse_olx_browser`` for emergencies.

All country instances share the schema; only ``domain`` and ``lang`` change.

    from extsearch.olx import search_offers
    res = await search_offers("iphone 11", domain="www.olx.pl", lang="pl")
"""

from __future__ import annotations

import asyncio
import json
import math
import re
import sys
import time
import urllib.parse
from typing import Any, Literal

try:
    from curl_cffi import requests as cffi_requests
    from curl_cffi.requests.exceptions import RequestException as CffiRequestError
except ImportError:  # pragma: no cover
    sys.exit(
        "curl_cffi is missing. Install dependencies: pip install -r requirements.txt\n"
        "Note: plain httpx/requests gets HTTP 403 from OLX (TLS fingerprint check)."
    )

from .config import JUNK_FILE
from .errors import ExtSearchError as OlxError
from .proxies import for_request

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

DEFAULT_DOMAIN = "www.olx.ua"
DEFAULT_LANG = "uk"
API_PATH = "/api/v1/offers"

# "Give away for free" is a separate OLX category.
# The API filters it by category id, HTML by its own path (/uk/otdam-darom/).
FREE_CATEGORY_ID = 1151
FREE_PATH = "/{lang}/otdam-darom/"
FREE_QUERY_PATH = "/{lang}/otdam-darom/q-{slug}/"
LIST_PATH = "/{lang}/list/q-{slug}/"

# OLX embeds the whole result JSON in window.__PRERENDERED_STATE__.
# That path is independent from the API: when /api returns 403, HTML still works.
STATE_MARKER = "window.__PRERENDERED_STATE__"
HTML_PAGE_SIZE = 40  # listings per HTML result page

MAX_LIMIT = 50      # the API silently returns nothing when limit > 50
MAX_RESULTS = 1000  # OLX does not page deeper than 1000 items (offset + limit)
MIN_INTERVAL = 0.4  # seconds between requests to avoid HTTP 429
TIMEOUT = 25.0

CACHE_TTL_SEARCH = 300  # 5 minutes
CACHE_TTL_ITEM = 120    # 2 minutes


# Anti-bot note (verified empirically on 2026-11-09):
#   OLX CloudFront-WAF answers 403 to Chrome/Firefox User-Agents, especially on
#   /api/v1/offers/{id} where any browser UA is rejected. The default libcurl UA
#   (what curl_cffi sends without impersonate) passes both search and item pages.
#   So the UA is NOT overridden and impersonate stays OFF. If 403 appears one day,
#   check this first.
HEADERS = {
    "Accept": "application/json",
    "Accept-Language": "uk-UA,uk;q=0.9,en;q=0.8",
}

# Sort names exposed to callers -> sort_by values understood by OLX
SortKey = Literal["relevance", "newest", "cheapest", "expensive",
                  "rating", "rating_asc", "reviews", "reviews_asc", "discount"]

SORTS: dict[str, str | None] = {
    "relevance": None,
    "newest": "created_at:desc",
    "cheapest": "filter_float_price:asc",
    "expensive": "filter_float_price:desc",
    # OLX cannot sort by rating or review count, so nothing is sent and the
    # order is applied locally after merging.
    "rating": None,
    "rating_asc": None,
    "reviews": None,
    "reviews_asc": None,
    "discount": None,
}

# Sorts computed from the item fields instead of the marketplace side.
LOCAL_SORTS = ("rating", "rating_asc", "reviews", "reviews_asc", "discount")

_WORD_RE = re.compile(r"[0-9a-zа-яіїєґё]+", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_BR_RE = re.compile(r"<\s*(br|/p|/div)\s*/?\s*>", re.IGNORECASE)

# --------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------



# --------------------------------------------------------------------------
# HTTP layer: throttling + cache + retry on 429
# --------------------------------------------------------------------------

_client: cffi_requests.AsyncSession | None = None
_throttle_lock: asyncio.Lock | None = None
_last_request_at = 0.0
_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def _get_client() -> cffi_requests.AsyncSession:
    """curl_cffi instead of httpx: OLX checks the TLS fingerprint (JA3/JA4),
    so plain requests/httpx gets 403 even with correct headers. impersonate is
    deliberately OFF — see the comment near HEADERS."""
    global _client
    if _client is None:
        _client = cffi_requests.AsyncSession(
            headers=HEADERS,
            timeout=TIMEOUT,
        )
    return _client


async def _throttle() -> None:
    global _throttle_lock, _last_request_at
    if _throttle_lock is None:
        _throttle_lock = asyncio.Lock()
    async with _throttle_lock:
        wait = MIN_INTERVAL - (time.monotonic() - _last_request_at)
        if wait > 0:
            await asyncio.sleep(wait)
        _last_request_at = time.monotonic()


async def _get_json(path: str, params: dict[str, Any] | None = None,
                    *, retries: int = 2,
                    domain: str = DEFAULT_DOMAIN) -> dict[str, Any]:
    """GET against the API with throttling, 429 retries and error mapping."""
    client = _get_client()
    url = f"https://{domain}{API_PATH}{path}"
    last_err: OlxError | None = None

    for attempt in range(retries + 1):
        await _throttle()
        try:
            resp = await client.get(url, params=params,
                                     proxies=for_request())
        except CffiRequestError as exc:
            last_err = OlxError("network", f"network request failed: {exc}")
            await asyncio.sleep(1.5 * (attempt + 1))
            continue

        if resp.status_code == 429:
            last_err = OlxError("rate_limited", "OLX rate limit hit (HTTP 429)")
            await asyncio.sleep(5 * (attempt + 1))
            continue
        if resp.status_code == 404:
            raise OlxError("not_found", "listing not found or removed")
        if resp.status_code in (401, 403, 423):
            raise OlxError(
                "blocked",
                "OLX blocked the request (HTTP 401/403). A browser User-Agent "
                "is the usual cause: keep impersonate off. The server will try "
                "the HTML fallback.",
            )
        if resp.status_code >= 500:
            last_err = OlxError("server_error", f"OLX returned HTTP {resp.status_code}")
            await asyncio.sleep(2 * (attempt + 1))
            continue
        if resp.status_code != 200:
            raise OlxError("http_error", f"OLX returned HTTP {resp.status_code}")

        try:
            return resp.json()
        except ValueError:
            raise OlxError("bad_response", "OLX response is not JSON") from None

    raise last_err or OlxError("unknown", "could not fetch data from OLX")


async def _get_text(url: str, params: dict[str, Any] | None = None,
                    *, retries: int = 2) -> str:
    """GET a regular HTML page. Same throttling and retries as the API path."""
    client = _get_client()
    last_err: OlxError | None = None

    for attempt in range(retries + 1):
        await _throttle()
        try:
            resp = await client.get(url, params=params,
                                     proxies=for_request())
        except CffiRequestError as exc:
            last_err = OlxError("network", f"network request failed: {exc}")
            await asyncio.sleep(1.5 * (attempt + 1))
            continue

        if resp.status_code == 429:
            last_err = OlxError("rate_limited", "OLX rate limit hit (HTTP 429)")
            await asyncio.sleep(5 * (attempt + 1))
            continue
        if resp.status_code == 404:
            raise OlxError("not_found", "page not found (listing removed?)")
        if resp.status_code in (401, 403, 423):
            raise OlxError("blocked", "OLX blocked the HTML page (HTTP 401/403)")
        if resp.status_code >= 500:
            last_err = OlxError("server_error", f"OLX returned HTTP {resp.status_code}")
            await asyncio.sleep(2 * (attempt + 1))
            continue
        if resp.status_code != 200:
            raise OlxError("http_error", f"OLX returned HTTP {resp.status_code}")

        return resp.text

    raise last_err or OlxError("unknown", "could not fetch the page")


def _cache_get(key: str) -> dict[str, Any] | None:
    hit = _cache.get(key)
    if not hit:
        return None
    expires_at, value = hit
    if time.monotonic() > expires_at:
        _cache.pop(key, None)
        return None
    return value


def _cache_put(key: str, value: dict[str, Any], ttl: float) -> None:
    if len(_cache) > 256:
        _cache.clear()
    _cache[key] = (time.monotonic() + ttl, value)


# id -> listing URL, needed by the HTML fallback of get_offer: callers only
# know the numeric id, while the listing page has to be fetched by URL.
_URL_BY_ID: dict[int, str] = {}


def _remember_url(offer_id: Any, url: str | None) -> None:
    if not offer_id or not url:
        return
    if len(_URL_BY_ID) > 2048:
        _URL_BY_ID.clear()
    _URL_BY_ID[int(offer_id)] = url


# --------------------------------------------------------------------------
# OLX response normalisation
# --------------------------------------------------------------------------


def _find_param(params: list[dict[str, Any]] | None,
                key: str) -> dict[str, Any] | None:
    for p in params or []:
        if p.get("key") == key:
            return p
    return None


def _clean_text(raw: str) -> str:
    """OLX returns descriptions with <br /> and HTML entities; strip them."""
    if not raw:
        return ""
    import html as _html

    text = _BR_RE.sub("\n", raw)
    text = _TAG_RE.sub("", text)
    text = _html.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _extract_price(item: dict[str, Any]) -> tuple[int | None, str | None, bool, bool]:
    """Price, currency, negotiable flag, give-away flag.

    In the give-away category OLX returns value=0 with type="free" and the label
    "Безкоштовно". It must be told apart from a plain zero so results show that
    the item is given away rather than sold.
    """
    raw = _find_param(item.get("params"), "price")
    value = (raw or {}).get("value")
    if not isinstance(value, dict):
        return None, None, False, False
    amount = value.get("value")
    if not isinstance(amount, (int, float)):
        amount = None
    is_free = value.get("type") == "free" or value.get("label") == "Безкоштовно"
    return (
        int(amount) if amount is not None else None,
        value.get("currency"),
        bool(value.get("negotiable")),
        bool(is_free),
    )


def _extract_state(item: dict[str, Any]) -> str | None:
    raw = _find_param(item.get("params"), "state")
    value = (raw or {}).get("value")
    if isinstance(value, dict):
        return value.get("label")
    return None


def _relevance(query: str, title: str) -> float:
    """Share of query words found in the title. 0.0 means no match at all."""
    wanted = {w for w in _WORD_RE.findall(query.lower()) if len(w) > 1}
    if not wanted:
        return 1.0
    have = set(_WORD_RE.findall(title.lower()))
    return round(len(wanted & have) / len(wanted), 3)


def _load_junk() -> list[str]:
    if not JUNK_FILE.exists():
        return []
    return [
        line.strip().lower()
        for line in JUNK_FILE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]


_JUNK: list[str] | None = None


def _is_junk(title: str, head_chars: int = 30) -> bool:
    """Is the title an accessory listing?

    Only the beginning of the title is checked: cases and screen protectors name
    the accessory first ("Case for iPhone 11"), while a real product mentions it
    as a bonus at the end ("AirPods 4 + case as a gift") — that must not be cut.
    """
    global _JUNK
    if _JUNK is None:
        _JUNK = _load_junk()
    head = title.lower()[:head_chars]
    return any(word in head for word in _JUNK)


def _query_is_accessory(query: str) -> bool:
    """Is the query itself about an accessory? Then the junk filter is unsafe:
    a search for "iphone case" would otherwise return nothing."""
    global _JUNK
    if _JUNK is None:
        _JUNK = _load_junk()
    low = query.lower()
    return any(word in low for word in _JUNK)


def _normalize_item(item: dict[str, Any], query: str,
                    include_urls: bool) -> dict[str, Any]:
    price, currency, negotiable, is_free = _extract_price(item)
    location = item.get("location") or {}
    city = (location.get("city") or {}).get("name")
    # A zero without the free marker means "negotiable": such rows must not
    # rank first in a cheapest sort.
    if price == 0 and not is_free:
        price = None
    out: dict[str, Any] = {
        "id": item.get("id"),
        "title": _clean_text(item.get("title") or ""),
        "price": price,
        "currency": currency,
        "negotiable": negotiable,
        "state": _extract_state(item),
        "city": city,
        "business": bool(item.get("business")),
        "relevance": _relevance(query, item.get("title") or ""),
    }
    if is_free:
        out["free"] = True
    if include_urls:
        out["url"] = item.get("url")
    out.update(_olx_details(item))
    _remember_url(out["id"], item.get("url"))
    return out


def _as_int(value: Any, name: str) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        raise OlxError("bad_argument", f"{name} must be an integer") from None


# --------------------------------------------------------------------------
# HTML path: JSON inside window.__PRERENDERED_STATE__
# --------------------------------------------------------------------------


def _extract_page_state(html: str) -> dict[str, Any]:
    """Extract window.__PRERENDERED_STATE__ from the HTML.

     It is a JSON string inside a JS literal: raw_decode the literal first, then
     json.loads its content. unquote_plus must not be used here — it turns
     "ANC+ENC" into "ANC ENC".
     """
    idx = html.find(STATE_MARKER)
    if idx < 0:
        raise OlxError("no_state", "__PRERENDERED_STATE__ is missing — page layout changed")
    pos = idx + len(STATE_MARKER)
    try:
        while html[pos] in " \t\r\n=":
            pos += 1
        outer, _ = json.JSONDecoder().raw_decode(html, pos)
        state = json.loads(outer) if isinstance(outer, str) else outer
    except (ValueError, IndexError) as exc:
        raise OlxError(
            "no_state", f"could not parse __PRERENDERED_STATE__: {exc}") from None
    if not isinstance(state, dict):
        raise OlxError("no_state", "unexpected __PRERENDERED_STATE__ format")
    return state


def _embedded_price(ad: dict[str, Any]) -> tuple[int | None, str | None, bool, str | None]:
    price = ad.get("price") or {}
    display = price.get("displayValue")
    if price.get("free"):
        return 0, "UAH", False, display
    regular = price.get("regularPrice") or {}
    amount = regular.get("value")
    amount = int(amount) if isinstance(amount, (int, float)) else None
    return amount, regular.get("currencyCode"), bool(regular.get("negotiable")), display


def _embedded_state(ad: dict[str, Any]) -> str | None:
    for p in ad.get("params") or []:
        if p.get("key") == "state":
            value = p.get("value")
            if isinstance(value, dict):
                return value.get("label")
            if isinstance(value, str):
                return value
    return None


def _normalize_embedded(ad: dict[str, Any], query: str,
                        include_urls: bool) -> dict[str, Any]:
    price, currency, negotiable, _ = _embedded_price(ad)
    # Same rule as the API path: a bare zero means "negotiable".
    if price == 0 and not (ad.get("price") or {}).get("free"):
        price = None
    location = ad.get("location") or {}
    title = _clean_text(ad.get("title") or "")
    out: dict[str, Any] = {
        "id": ad.get("id"),
        "title": title,
        "price": price,
        "currency": currency,
        "negotiable": negotiable,
        "state": _embedded_state(ad) or ad.get("itemCondition"),
        "city": location.get("cityName"),
        "business": bool(ad.get("isBusiness")),
        "relevance": _relevance(query, title),
    }
    if include_urls:
        out["url"] = ad.get("url")
    if out["id"]:
        _remember_url(out["id"], ad.get("url"))
    return out


async def _search_via_html(query: str, page: int,
                           price_min: int | None,
                           price_max: int | None,
                           free_only: bool = False,
                           domain: str = DEFAULT_DOMAIN,
                           lang: str = DEFAULT_LANG) -> dict[str, Any]:
    """Search via an HTML page. Returns ads, total, total_pages, page."""
    slug = urllib.parse.quote(query.strip().replace(" ", "-"))
    params: dict[str, Any] = {}
    if page > 1:
        params["page"] = page
    if price_min is not None:
        params["search[filter_float_price:from]"] = int(price_min)
    if price_max is not None:
        params["search[filter_float_price:to]"] = int(price_max)

    if free_only:
        url = (f"https://{domain}" + FREE_QUERY_PATH.format(lang=lang, slug=slug)
               if slug else f"https://{domain}" + FREE_PATH.format(lang=lang))
    else:
        url = f"https://{domain}" + LIST_PATH.format(lang=lang, slug=slug)

    html = await _get_text(url, params)
    state = _extract_page_state(html)
    listing = ((state.get("listing") or {}).get("listing")) or {}
    if not listing:
        raise OlxError("no_state", "page state has no listing block")

    return {
        "ads": listing.get("ads") or [],
        "total": listing.get("totalElements"),
        "total_pages": listing.get("totalPages"),
        "page": int(listing.get("pageNumber") or 0) + 1,
    }


# --------------------------------------------------------------------------
# Core: search and product card
# --------------------------------------------------------------------------


async def _search_api_rows(
    query: str, limit: int, offset: int,
    price_min: int | None, price_max: int | None, sort: str,
    city_id: int | None, region_id: int | None,
    include_urls: bool, free_only: bool = False,
    domain: str = DEFAULT_DOMAIN,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Search via the JSON API. Returns normalised rows plus metadata."""
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    if query:
        params["query"] = query
    if free_only:
        # Give-away category: without it query="" would return the whole site.
        params["category_id"] = FREE_CATEGORY_ID
    if SORTS[sort]:
        params["sort_by"] = SORTS[sort]
    if price_min is not None:
        params["filter_float_price:from"] = int(price_min)
    if price_max is not None:
        params["filter_float_price:to"] = int(price_max)
    if city_id:
        params["city_id"] = int(city_id)
    if region_id:
        params["region_id"] = int(region_id)

    cache_key = f"search:{domain}:" + json.dumps(params, sort_keys=True)
    cached = _cache_get(cache_key)
    if cached is not None:
        payload, from_cache = cached, True
    else:
        payload = await _get_json("", params, domain=domain)
        _cache_put(cache_key, payload, CACHE_TTL_SEARCH)
        from_cache = False

    items_raw = payload.get("data") or []
    metadata = payload.get("metadata") or {}
    if not items_raw and not metadata:
        raise OlxError("bad_request", "OLX rejected the query parameters")

    rows = [_normalize_item(raw, query, include_urls) for raw in items_raw[:limit]]
    total = int(metadata.get("visible_total_count") or 0)
    return rows, {
        "total": total,
        "accessible": min(total, MAX_RESULTS),
        "pages": None,
        "fetched": min(len(items_raw), limit),
        "from_cache": from_cache,
    }


async def _search_html_rows(
    query: str, limit: int, offset: int,
    price_min: int | None, price_max: int | None,
    include_urls: bool, free_only: bool = False,
    domain: str = DEFAULT_DOMAIN, lang: str = DEFAULT_LANG,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Fallback search via an HTML page.

    A page holds ~40 listings, so pages are fetched until offset+limit is
    collected. Duplicates (promoted inserts) are dropped by id.
    """
    wanted = offset + limit
    collected: list[dict[str, Any]] = []
    seen: set[Any] = set()
    page = offset // HTML_PAGE_SIZE + 1
    total = 0
    total_pages = 0

    while len(collected) < wanted and page <= 30:
        chunk = await _search_via_html(query, page, price_min, price_max,
                                     free_only, domain=domain, lang=lang)
        ads = chunk["ads"]
        total = int(chunk["total"] or 0)
        total_pages = int(chunk["total_pages"] or 0)
        if not ads:
            break
        for ad in ads:
            if ad.get("id") in seen:
                continue
            seen.add(ad.get("id"))
            collected.append(ad)
        if total_pages and page >= total_pages:
            break
        page += 1

    if not collected:
        raise OlxError("no_state", "HTML search returned nothing")

    rows = [
        _normalize_embedded(ad, query, include_urls)
        for ad in collected[offset:offset + limit]
    ]
    accessible = min(total, MAX_RESULTS) if total else len(collected)
    return rows, {
        "total": total or len(collected),
        "accessible": accessible,
        "pages": total_pages or None,
        "fetched": len(rows),
        "from_cache": False,
    }


async def search_offers(
    query: str,
    limit: int = 10,
    offset: int = 0,
    price_min: int | None = None,
    price_max: int | None = None,
    sort: str = "relevance",
    city_id: int | None = None,
    region_id: int | None = None,
    strict: bool = False,
    junk_filter: bool | None = None,
    include_urls: bool = False,
    source: Literal["auto", "api", "html"] = "auto",
    free_only: bool = False,
    domain: str = DEFAULT_DOMAIN,
    lang: str = DEFAULT_LANG,
) -> dict[str, Any]:
    query = (query or "").strip()
    if not query and not free_only:
        raise OlxError("bad_argument", "query must not be empty")

    limit = max(1, min(int(limit), MAX_LIMIT))
    offset = max(0, int(offset))
    if offset >= MAX_RESULTS:
        raise OlxError(
            "offset_too_large",
            f"OLX paginates {MAX_RESULTS} items at most, offset={offset} is out of range",
        )
    if offset + limit > MAX_RESULTS:
        limit = MAX_RESULTS - offset

    sort = (sort or "relevance").lower()
    if sort not in SORTS:
        raise OlxError("bad_argument", f"sort must be one of: {', '.join(SORTS)}")

    source = (source or "auto").lower()
    if source not in ("auto", "api", "html"):
        raise OlxError("bad_argument", "source must be one of: auto / api / html")

    # Price sorting always floats accessories up (an 8 UAH nozzle instead of a
    # printer), so the junk filter auto-enables for cheapest/expensive. It stays
    # off when the query itself is about an accessory, or the result is empty.
    junk_auto = False
    if junk_filter is None:
        junk_filter = sort in ("cheapest", "expensive") and not _query_is_accessory(query)
        junk_auto = bool(junk_filter)

    notes: list[str] = []
    rows: list[dict[str, Any]] = []
    meta: dict[str, Any] = {}
    used = "api"

    if source in ("auto", "api"):
        try:
            rows, meta = await _search_api_rows(
                query, limit, offset, price_min, price_max, sort,
                city_id, region_id, include_urls, free_only, domain=domain,
            )
        except OlxError as exc:
            if source == "api":
                raise
            notes.append(f"API failed ({exc.code}); fell back to HTML")

    if not meta:
        used = "html"
        rows, meta = await _search_html_rows(
            query, limit, offset, price_min, price_max, include_urls, free_only,
            domain=domain, lang=lang,
        )
        if city_id or region_id:
            notes.append("HTML mode does not filter by city or region")
        if sort != "newest":
            notes.append("HTML mode sorts locally only; pagination comes from OLX")

    items: list[dict[str, Any]] = []
    for row in rows:
        if junk_filter and _is_junk(row["title"]):
            continue
        if strict and row["relevance"] <= 0:
            continue
        items.append(row)

    # Order is applied locally: OLX mixes in promoted items and sometimes
    # ignores sort_by, so its ordering cannot be trusted.
    if sort == "relevance":
        items.sort(key=lambda r: -r["relevance"])
    elif sort == "cheapest":
        items.sort(key=lambda r: (r["price"] is None, r["price"] or 0))
    elif sort == "expensive":
        items.sort(key=lambda r: (r["price"] is None, -(r["price"] or 0)))
    elif sort in LOCAL_SORTS:
        # OLX results carry no rating or review count: nothing to sort by.
        notes.append(f"OLX search results carry no rating; sort={sort} is ignored "
                     "and relevance order is kept")

    accessible = int(meta.get("accessible") or 0)
    fetched = int(meta.get("fetched") or len(rows))

    result: dict[str, Any] = {
        "query": query,
        "source": used,
        "total": int(meta.get("total") or 0),
        "accessible": accessible,
        "pages": meta.get("pages") or (math.ceil(accessible / limit) if accessible else 0),
        "offset": offset,
        "limit": limit,
        "returned": len(items),
        "has_more": (offset + fetched) < accessible,
        "filtered_out": fetched - len(items),
        "from_cache": bool(meta.get("from_cache")),
        "items": items,
    }
    if junk_auto:
        notes.append(
            "junk_filter was enabled automatically: price sorting floats "
            "accessories up. Pass junk_filter=false to disable."
        )
        if price_min is None:
            notes.append(
                "Accessories are usually cheaper than the product itself — set "
                "price_min to cut them off completely."
            )
    if notes:
        result["notes"] = notes
    return result


def _olx_details(item: dict[str, Any]) -> dict[str, Any]:
    """OLX delivery and payment taken from API fields, not from the description.

    `safedeal` is OLX Safe Deal: payment goes through OLX and the seller is paid
    after confirmation. `delivery.rock` means OLX delivery (courier or branch),
    `contact.courier` means courier delivery is available.
    """
    delivery: list[dict[str, Any]] = []
    payment: list[dict[str, Any]] = []

    safedeal = (item.get("safedeal") or {}).get("status") == "active"
    rock = item.get("delivery") or {}
    rock = (rock.get("rock") or {}) if isinstance(rock, dict) else {}
    courier = bool((item.get("contact") or {}).get("courier"))

    if safedeal or rock.get("active"):
        delivery.append({
            "method": "OLX Delivery (Nova Poshta / Ukrposhta)",
            "price": None,
            "note": "carrier rate, payment protected by OLX",
            "source": "olx_api",
        })
    if courier:
        delivery.append({
            "method": "OLX courier",
            "price": None,
            "note": "carrier rate, stated in the listing",
            "source": "olx_api",
        })
    delivery.append({
        "method": "Pickup (meet the seller)",
        "price": 0,
        "note": "free, arrange directly with the seller",
        "source": "olx_api",
    })

    if safedeal:
        payment.append({"method": "payment via OLX (card / cash on delivery)",
                        "source": "olx_api"})
        payment.append({"method": "cash on delivery", "source": "olx_api"})
    payment.append({"method": "cash on meetup", "source": "olx_api"})
    payment.append({"method": "card transfer", "source": "olx_api"})

    out: dict[str, Any] = {"delivery": delivery, "payment": payment}
    if safedeal:
        out["safedeal"] = True
    return out


def _offer_from_api(item: dict[str, Any], max_description_chars: int,
                    include_urls: bool, from_cache: bool) -> dict[str, Any]:
    price, currency, negotiable, is_free = _extract_price(item)
    description = _clean_text(item.get("description") or "")
    if max_description_chars and len(description) > max_description_chars:
        description = description[:max_description_chars].rstrip() + "…"

    specs: dict[str, str] = {}
    for p in item.get("params") or []:
        name = p.get("name")
        value = p.get("value")
        if not name or not isinstance(value, dict):
            continue
        label = value.get("label")
        if label and p.get("key") != "price":
            specs[name] = str(label)

    user = item.get("user") or {}
    location = item.get("location") or {}

    out: dict[str, Any] = {
        "id": item.get("id"),
        "title": _clean_text(item.get("title") or ""),
        "price": price,
        "currency": currency,
        "negotiable": negotiable,
        "state": _extract_state(item),
        "status": item.get("status"),
        "description": description,
        "specs": specs,
        "seller": {
            "id": user.get("id"),
            "name": user.get("name"),
            "company": user.get("company_name") or None,
            "business": bool(item.get("business")),
        },
        "location": {
            "city": (location.get("city") or {}).get("name"),
            "region": (location.get("region") or {}).get("name"),
        },
        "photos": len(item.get("photos") or []),
        "created": item.get("created_time"),
        "refreshed": item.get("last_refresh_time"),
        "from_cache": from_cache,
        "source": "api",
    }
    if is_free:
        out["free"] = True
    if include_urls:
        out["url"] = item.get("url")
    out.update(_olx_details(item))
    _remember_url(out["id"], item.get("url"))
    return out


def _offer_from_embedded(ad: dict[str, Any], max_description_chars: int,
                         include_urls: bool) -> dict[str, Any]:
    price, currency, negotiable, _ = _embedded_price(ad)
    description = _clean_text(ad.get("description") or "")
    if max_description_chars and len(description) > max_description_chars:
        description = description[:max_description_chars].rstrip() + "…"

    specs: dict[str, str] = {}
    for p in ad.get("params") or []:
        name = p.get("name")
        value = p.get("value")
        if not name or value is None:
            continue
        label = value.get("label") if isinstance(value, dict) else value
        if label:
            specs[str(name)] = str(label)

    user = ad.get("user") or {}
    location = ad.get("location") or {}

    out: dict[str, Any] = {
        "id": ad.get("id"),
        "title": _clean_text(ad.get("title") or ""),
        "price": price,
        "currency": currency,
        "negotiable": negotiable,
        "state": _embedded_state(ad) or ad.get("itemCondition"),
        "status": ad.get("status"),
        "description": description,
        "specs": specs,
        "seller": {
            "id": user.get("id"),
            "name": user.get("name") or user.get("company_name"),
            "company": user.get("company_name") or None,
            "business": bool(ad.get("isBusiness")),
        },
        "location": {
            "city": location.get("cityName"),
            "region": location.get("regionName"),
        },
        "photos": len(ad.get("photos") or []),
        "created": ad.get("createdTime"),
        "refreshed": ad.get("lastRefreshTime"),
        "from_cache": False,
        "source": "html",
    }
    if include_urls:
        out["url"] = ad.get("url")
    # The embedded JSON names fields differently, so they are converted to the
    # API shape and go through the same delivery parsing.
    out.update(_olx_details({
        "safedeal": {"status": "active"} if ad.get("safedeal") or ad.get("isSafeDeal") else {},
        "delivery": ad.get("delivery") or {},
        "contact": {"courier": bool(ad.get("courier") or (ad.get("contact") or {}).get("courier"))},
    }))
    _remember_url(out["id"], ad.get("url"))
    return out


async def get_offer(offer_id: int, max_description_chars: int = 600,
                    include_urls: bool = True,
                    source: str = "auto",
                    domain: str = DEFAULT_DOMAIN) -> dict[str, Any]:
    try:
        offer_id = int(offer_id)
    except (TypeError, ValueError):
        raise OlxError("bad_argument",
                       "offer_id must be an integer, e.g. 940356105") from None

    source = (source or "auto").lower()
    if source not in ("auto", "api", "html"):
        raise OlxError("bad_argument", "source must be one of: auto / api / html")

    payload: dict[str, Any] | None = None
    from_cache = False
    api_error: OlxError | None = None

    if source in ("auto", "api"):
        cache_key = f"item:{domain}:{offer_id}"
        cached = _cache_get(cache_key)
        if cached is not None:
            payload, from_cache = cached, True
        else:
            try:
                payload = await _get_json(f"/{offer_id}", domain=domain)
                _cache_put(cache_key, payload, CACHE_TTL_ITEM)
            except OlxError as exc:
                if source == "api":
                    raise
                api_error = exc

    if payload is not None:
        item = payload.get("data")
        if not isinstance(item, dict):
            raise OlxError("not_found", "listing not found or removed")
        return _offer_from_api(item, max_description_chars, include_urls, from_cache)

    # Fallback path: the listing page plus its embedded JSON.
    url = _URL_BY_ID.get(offer_id)
    if not url:
        reason = api_error.code if api_error else "source=html"
        raise OlxError(
            "no_url",
            f"the API did not return the card ({reason}) and the listing URL is "
            "unknown; run a search first — URLs are remembered from results.",
        )

    html = await _get_text(url)
    state = _extract_page_state(html)
    ad = ((state.get("ad") or {}).get("ad")) or {}
    if not ad:
        raise OlxError("no_state", "listing HTML has no ad block")
    return _offer_from_embedded(ad, max_description_chars, include_urls)


# --------------------------------------------------------------------------
# Legacy browser parser (emergency path)
# --------------------------------------------------------------------------


async def get_max_pages(page_obj, base_url: str) -> int:
    """Kept from the original code. OLX paginator classes change, so page
    counts are taken from the JSON API instead (see search_offers)."""
    try:
        await page_obj.goto(base_url, wait_until="domcontentloaded", timeout=30000)
        html = await page_obj.content()
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        numbers = []
        for el in soup.select('[data-testid="pagination-list"] a, .FyiFX'):
            digits = re.sub(r"\D", "", el.get_text())
            if digits:
                numbers.append(int(digits))
        return max(numbers) if numbers else 1
    except Exception as exc:
        print(f"[legacy] pagination failed: {exc}", file=sys.stderr)
    return 1


async def parse_olx_browser(query: str, max_pages_to_scan: int = 2) -> list[dict]:
    """Third, heaviest path: a real browser (Playwright + Firefox).

    Only needed when both the API and the HTML-with-JSON path fail. Selectors
    come from the live OLX markup (2026-11-09): OLX uses stable `data-testid`
    attributes, while emotion classes like `css-1sw7q4x` change on every deploy.
    """
    try:
        from bs4 import BeautifulSoup
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise OlxError(
            "browser_unavailable",
            f"Playwright/BS4 are not installed ({exc}). Need the fallback? "
            "pip install playwright beautifulsoup4 && playwright install firefox",
        ) from None

    slug = urllib.parse.quote(query.strip().replace(" ", "-"))
    base_url = f"https://www.olx.ua/uk/list/q-{slug}/"
    listings: list[dict] = []

    async with async_playwright() as p:
        browser = await p.firefox.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()
        max_pages = await get_max_pages(page, base_url)
        pages = min(max_pages, max_pages_to_scan)

        for pagenum in range(1, pages + 1):
            url = base_url if pagenum == 1 else f"{base_url}?page={pagenum}"
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                await page.wait_for_selector('[data-testid="l-card"]', timeout=15000)
                soup = BeautifulSoup(await page.content(), "html.parser")
                for card in soup.select('[data-testid="l-card"]'):
                    title_el = card.select_one(
                        '[data-testid="ad-card-title"] h4, '
                        '[data-testid="ad-card-title"] h6, h4, h6'
                    )
                    price_el = card.select_one('[data-testid="ad-price"]')
                    loc_el = card.select_one('[data-testid="location-date"]')
                    link_el = card.select_one('[data-testid="card-title-link"]') \
                        or card.select_one('a[href*="/d/uk/obyavlenie/"]')
                    if not (title_el and link_el):
                        continue

                    # OLX puts the listing id right into the card id attribute
                    raw_id = card.get("id") or ""
                    offer_id = int(raw_id) if raw_id.isdigit() else None
                    href = link_el.get("href", "")
                    full_url = href if href.startswith("http") \
                        else f"https://www.olx.ua{href}"
                    _remember_url(offer_id, full_url)

                    listings.append({
                        "id": offer_id,
                        "name": title_el.get_text(strip=True),
                        "price": price_el.get_text(strip=True) if price_el else "",
                        "date": loc_el.get_text(strip=True) if loc_el else "",
                        "link": full_url,
                    })
            except Exception as exc:
                print(f"[legacy] page {pagenum}: {exc}", file=sys.stderr)
                break

        await browser.close()

    return listings


