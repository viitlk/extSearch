"""Asia: Rakuten (JP), Flipkart (IN), Shopee (SG/MY)."""

from __future__ import annotations

from . import Marketplace, register
from .adapters import css_search, rakuten_get, rakuten_search, rows_search, simple_get

register(Marketplace(
    name="rakuten", title="Rakuten Ichiba", country="jp", currency="JPY",
    search=rows_search("rakuten", rakuten_search),
    get=simple_get(rakuten_get),
    note="price from data-track-price, yen",
))

register(Marketplace(
    name="flipkart", title="Flipkart", country="in", currency="INR",
    search=rows_search(
        "flipkart",
        lambda q, limit, page: css_search(
            "flipkart", "https://www.flipkart.com/search?q={q}",
            query=q, limit=limit, page=page, currency="INR",
            item_sel="div[data-id]",
            title_sel="a[title]",
            link_sel="a[href*='/p/']",
            price_sel="div._30jeq3",
            id_regex=r"pid=([A-Z0-9]{8,})",
            page_param="page",
        ),
    ),
    verified=False, needs_proxy=True,
    note="HTTP 403 without a proxy in India",
))

register(Marketplace(
    name="shopee_sg", title="Shopee SG", country="sg", currency="SGD",
    search=rows_search(
        "shopee_sg",
        lambda q, limit, page: css_search(
            "shopee_sg", "https://shopee.sg/search?keyword={q}",
            query=q, limit=limit, page=page, currency="SGD",
            item_sel="div[data-sqe='item']",
            title_sel="div[data-sqe='name']",
            link_sel="a[href*='-i.']",
            id_regex=r"-i\.(\d+)\.(\d+)",
            page_param="page",
        ),
    ),
    verified=False, needs_proxy=True,
    note="needs a proxy in the region",
))
