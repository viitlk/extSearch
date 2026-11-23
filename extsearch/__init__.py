"""extsearch — product search across marketplaces of many countries.

Quick start:

    import asyncio
    from extsearch import search, select, set_proxies

    set_proxies("socks5://1.2.3.4:1080")          # optional
    res = asyncio.run(search("fifine k669", where="ua", limit=5))
    card = asyncio.run(select(res["items"][0]["id"]))

Entry points (see pyproject): ``extsearch`` (CLI), ``extsearch-mcp``
(MCP server), ``extsearch-api`` (HTTP API).
"""

from __future__ import annotations

from .compare import compare
from .config import __version__
from .errors import ExtSearchError, OlxError
from .marketplaces import REGISTRY, by_country, catalog, resolve
from .media import FREELANCE_BOARDS, freelance_search, mod_search, video_search
from .proxies import POOL, get_pool, parse_proxies, set_proxies
from .search import search, select, sources_catalog
from .web import web_get, web_search

__all__ = [
    "FREELANCE_BOARDS",
    "POOL",
    "REGISTRY",
    "ExtSearchError",
    "OlxError",
    "__version__",
    "by_country",
    "catalog",
    "compare",
    "freelance_search",
    "get_pool",
    "mod_search",
    "parse_proxies",
    "resolve",
    "search",
    "select",
    "set_proxies",
    "sources_catalog",
    "video_search",
    "web_get",
    "web_search",
]
