"""Global marketplaces: AliExpress and Alibaba."""

from __future__ import annotations

from . import Marketplace, register
from .adapters import aliexpress_get, aliexpress_search, css_search, rows_search, simple_get

register(Marketplace(
    name="aliexpress", title="AliExpress", country="global", currency="USD",
    search=rows_search("aliexpress", aliexpress_search),
    get=simple_get(aliexpress_get),
    note="price and currency are read from link parameters (pdp_npi)",
))

register(Marketplace(
    name="alibaba", title="Alibaba.com", country="global", currency="USD",
    search=rows_search(
        "alibaba",
        lambda q, limit, page: css_search(
            "alibaba", "https://www.alibaba.com/trade/search?SearchText={q}",
            query=q, limit=limit, page=page, currency="USD",
            item_sel="div.fy23-search-card",
            title_sel="h2.search-card-e-title",
            link_sel="a.search-card-e-slider__link",
            price_sel="div.search-card-e-price-main",
            id_regex=r"_(\d{8,})\.html",
        ),
    ),
    verified=False,
    note="wholesale lots; markup not verified",
))
