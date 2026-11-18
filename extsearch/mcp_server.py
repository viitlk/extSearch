"""MCP server: the same features as the Python API, exposed as tools.

Run:

    extsearch-mcp                       # stdio (default)
    EXTSEARCH_TRANSPORT=http extsearch-mcp --port 8765

Every tool returns a JSON-serialisable dict. Failures return
``{"error": "<code>", "message": "...", "hint": "..."}`` — never exceptions.
"""

from __future__ import annotations

import os
from typing import Any, Literal

try:  # mcp >= 2.0
    from mcp.server.mcpserver import MCPServer as _Server
except ImportError:  # pragma: no cover - mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Server

from .errors import ExtSearchError
from .media import FREELANCE_BOARDS, freelance_search as _freelance
from .media import mod_search as _mods
from .media import video_search as _video
from .proxies import get_pool, set_proxies
from .search import search as _search
from .search import select as _select
from .search import sources_catalog
from .web import web_get as _web_get
from .web import web_search as _web_search

mcp = _Server("extsearch")

SortKey = Literal["relevance", "newest", "cheapest", "expensive",
                  "rating", "rating_asc", "reviews", "reviews_asc", "discount"]


@mcp.tool()
async def marketplace_search(
    query: str,
    where: str = "all",
    limit: int = 10,
    page: int = 1,
    price_min: float | None = None,
    price_max: float | None = None,
    min_rating: float | None = None,
    sort: SortKey = "relevance",
    strict: bool = False,
    junk_filter: bool | None = None,
    include_urls: bool = True,
    details: int = 0,
    per_source_limit: int | None = None,
    free_only: bool = False,
    proxies: list[str] | None = None,
) -> dict[str, Any]:
    """Search products across marketplaces of many countries.

    Args:
        query: what to look for, e.g. "fifine k669".
        where: countries and/or marketplaces. "all" searches everything;
            "ua" — Ukraine only; "ua,ru" — two countries; "olx,ozon" — by name;
            "all,-ru" — everything except Russia.
            Countries: ua, pl, ro, bg, pt, kz, uz, ru, us, gb, de, fr, it, es,
            eu, jp, in, sg, global.
        limit: total number of items to return, 1..50.
        page: page number, starting at 1.
        price_min / price_max: price bounds in the item currency.
        min_rating: drop items without a rating or below it.
        sort: relevance | newest | cheapest | expensive | rating | rating_asc
            | reviews | reviews_asc | discount. Price sorts use price_usd, so
            items from different countries stay comparable.
        strict: keep only items whose title contains a query word.
        junk_filter: null (auto for price sorts) | true | false.
        include_urls: add the product URL to each item.
        details: fetch delivery/payment/reviews for the first N items.
        per_source_limit: items requested from each marketplace.
        free_only: OLX give-away category (Ukraine only).
        proxies: list of "TYPE://IP:PORT" (http, https, socks4, socks5) —
            replaces the process proxy pool. Some marketplaces (Ozon,
            Wildberries, Amazon.com, eBay, Walmart) only answer through a proxy.

    Item ids look like "amazon:B0ABC123", pass them to `marketplace_select`.
    The response also lists per-source status in `sources` and `notes`.
    """
    try:
        return await _search(
            query, where=where, limit=limit, page=page, price_min=price_min,
            price_max=price_max, min_rating=min_rating, sort=sort, strict=strict,
            junk_filter=junk_filter, include_urls=include_urls, details=details,
            per_source_limit=per_source_limit, free_only=free_only, proxies=proxies,
        )
    except ExtSearchError as exc:
        return exc.as_dict()


@mcp.tool()
async def marketplace_select(item_id: str, max_description_chars: int = 600) -> dict[str, Any]:
    """Product card by an id returned from `marketplace_search`.

    Args:
        item_id: "olx:940356105" or "amazon:B0ABC123"; a bare number means OLX.
        max_description_chars: truncate the description.

    Besides title, price and description the card may include:

        delivery[]  — {method, price, note, source}; price null means the shop
                      does not publish a fixed delivery price
        payment[]   — {method, source}
        reviews     — {rating, count, items[]} when available
        free_shipping_from — free-shipping threshold, if the shop states it
    """
    try:
        return await _select(item_id, max_description_chars)
    except ExtSearchError as exc:
        return exc.as_dict()


@mcp.tool()
def marketplace_catalog() -> dict[str, Any]:
    """List supported countries and marketplaces with verification status.

    Each marketplace entry has: name, title, country, currency, verified
    (markup checked from this host), needs_proxy (answers 403 without a proxy),
    can_get (product cards supported).
    """
    return sources_catalog()


@mcp.tool()
def configure_proxies(proxies: list[str] | str | None = None) -> dict[str, Any]:
    """Set or clear the proxy pool used by every request.

    Args:
        proxies: list or comma-separated string of "TYPE://IP:PORT", where TYPE
            is http, https, socks4 or socks5; credentials are allowed
            ("socks5://user:pass@1.2.3.4:1080"). Empty or null clears the pool.

    Returns the masked pool state. Proxies rotate: a failing one is skipped for
    a while, and the next request uses the next proxy.
    """
    try:
        pool = set_proxies(proxies)
    except ValueError as exc:
        return ExtSearchError("bad_argument", str(exc)).as_dict()
    return {"proxies": pool.stats(), "count": len(pool)}


@mcp.tool()
async def web_search(query: str, limit: int = 8, fetch_top: int = 0,
                     max_page_chars: int = 2500, region: str | None = None) -> dict[str, Any]:
    """Web search (DuckDuckGo, Bing fallback) with optional full page text.

    Args:
        query: search phrase; engine operators like site: and filetype: work.
        limit: results to return, 1..30.
        fetch_top: download the top N pages and attach their text; this is what
            makes reranking possible, a 150-character snippet rarely suffices.
        max_page_chars: characters of text per page.
        region: DuckDuckGo region code, e.g. "ua-uk"; global by default.
    """
    try:
        return await _web_search(query, limit=limit, fetch_top=fetch_top,
                                 max_page_chars=max_page_chars, region=region)
    except ExtSearchError as exc:
        return exc.as_dict()


@mcp.tool()
async def web_get(url: str, max_chars: int = 4000, offset: int = 0) -> dict[str, Any]:
    """Download a page and return clean text without navigation or scripts.

    Args:
        url: page address.
        max_chars: slice size, 1..20000.
        offset: character to start from, for long pages.
    """
    try:
        return await _web_get(url, max_chars=max_chars, offset=offset)
    except ExtSearchError as exc:
        return exc.as_dict()


@mcp.tool()
async def video_search(query: str, limit: int = 8) -> dict[str, Any]:
    """Search YouTube videos (no API key: data comes from ytInitialData).

    Returns items with id, title, channel, url, duration, published, views.
    """
    try:
        return await _video(query, limit=limit)
    except ExtSearchError as exc:
        return exc.as_dict()


@mcp.tool()
async def mod_search(
    query: str,
    where: Literal["all", "modrinth", "curseforge"] = "all",
    limit: int = 10,
    project_type: Literal["mod", "modpack", "resourcepack", "shader", "datapack"] | None = None,
    sort: Literal["relevance", "downloads", "newest", "updated", "follows"] = "relevance",
) -> dict[str, Any]:
    """Search Minecraft mods, modpacks, resource packs and shaders.

    Modrinth uses the official API (downloads, follows, versions), CurseForge is
    parsed from HTML (title, author, type). Results are interleaved.
    """
    try:
        return await _mods(query, where=where, limit=limit,
                           project_type=project_type, sort=sort)
    except ExtSearchError as exc:
        return exc.as_dict()


@mcp.tool()
async def freelance_search(query: str, boards: str = "all", limit: int = 10) -> dict[str, Any]:
    """Search freelance job boards: Kwork, Freelancehunt, Freelancer, Upwork, Fiverr.

    Args:
        boards: "all" or a comma-separated subset.

    This is meta-search through a search engine with site: operators, not the
    native board APIs (they render results with JS), and the response says so in
    the `mode` field.
    """
    try:
        return await _freelance(query, boards=boards, limit=limit)
    except ExtSearchError as exc:
        return exc.as_dict()


def main() -> None:  # pragma: no cover - entry point
    transport = os.environ.get("EXTSEARCH_TRANSPORT", "stdio").strip().lower()
    if transport in {"http", "streamable-http", "sse"}:
        host = os.environ.get("EXTSEARCH_HOST", "127.0.0.1")
        port = int(os.environ.get("EXTSEARCH_PORT", "8765"))
        mcp.run(transport="streamable-http" if transport == "http" else transport,
                host=host, port=port)
        return
    mcp.run()


__all__ = ["mcp", "main", "FREELANCE_BOARDS", "get_pool"]
