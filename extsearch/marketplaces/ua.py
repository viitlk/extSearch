"""Ukraine: 14 marketplaces."""

from __future__ import annotations

from typing import Any

from .. import catalog, catalog_extra
from . import Marketplace, register
from .adapters import olx_adapter

olx_search, olx_get = olx_adapter("www.olx.ua", "uk", "UAH")
register(Marketplace(
    name="olx", title="OLX Україна", country="ua", currency="UAH",
    search=olx_search, get=olx_get,
    note="classifieds; free_only=true searches the give-away category",
))


def _catalog_search(name: str):
    async def search(query: str, *, limit: int = 10, page: int = 1,
                     price_min: int | None = None, price_max: int | None = None,
                     sort: str | None = None, strict: bool = False,
                     include_urls: bool = False, **_: Any) -> dict[str, Any]:
        return await catalog.market_search(
            name, query, limit=limit, page=page, price_min=price_min,
            price_max=price_max, sort=sort, strict=strict,
            include_urls=include_urls)
    return search


def _catalog_get(name: str):
    async def get(offer_id: Any, max_description_chars: int = 600) -> dict[str, Any]:
        return await catalog.market_get(name, int(offer_id),
                                        max_description_chars=max_description_chars)
    return get


def _extra_search(name: str):
    async def search(query: str, *, limit: int = 10, page: int = 1,
                     price_min: int | None = None, price_max: int | None = None,
                     sort: str | None = None, strict: bool = False,
                     include_urls: bool = False, **_: Any) -> dict[str, Any]:
        return await catalog_extra.extra_search(
            name, query, limit=limit, page=page, price_min=price_min,
            price_max=price_max, sort=sort, strict=strict,
            include_urls=include_urls)
    return search


def _extra_get(name: str):
    async def get(offer_id: Any, max_description_chars: int = 600) -> dict[str, Any]:
        return await catalog_extra.extra_get(name, int(offer_id),
                                             max_description_chars=max_description_chars)
    return get


_UA_STORES = [
    ("prom", "Prom.ua", "seller marketplace"),
    ("foxtrot", "Foxtrot", "electronics and home appliances"),
    ("rozetka", "Rozetka", "search needs a warmed browser profile"),
    ("comfy", "Comfy", "electronics"),
    ("allo", "Allo", "electronics"),
    ("moyo", "Moyo", "electronics"),
    ("kasta", "Kasta", "fashion, beauty, electronics"),
    ("brain", "Brain", "electronics"),
    ("ctrs", "Citrus", "electronics"),
    ("bigl", "Bigl", "Prom group marketplace"),
    ("epicentr", "Epicentr", "DIY, building, home"),
    ("makeup", "Makeup", "cosmetics and perfume"),
    ("shafa", "Shafa", "second-hand fashion"),
    ("auto", "AUTO.RIA", "cars; price in USD"),
]

for _name, _title, _note in _UA_STORES:
    _is_extra = _name in catalog_extra.SEARCHERS
    register(Marketplace(
        name=_name, title=_title, country="ua",
        currency="USD" if _name == "auto" else "UAH",
        default=_name != "auto",
        search=(_extra_search(_name) if _is_extra else _catalog_search(_name)),
        get=(_extra_get(_name) if _is_extra else _catalog_get(_name)),
        note=_note,
    ))
