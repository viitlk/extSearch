"""USA: Amazon, eBay, Walmart."""

from __future__ import annotations

from . import Marketplace, register
from .adapters import (
                       amazon_get,
                       amazon_search,
                       css_search,
                       ebay_get,
                       ebay_search,
                       rows_search,
                       simple_get,
)

register(Marketplace(
    name="amazon", title="Amazon.com", country="us", currency="USD",
    search=rows_search("amazon.com",
                       lambda q, limit, page: amazon_search("com", q, limit=limit, page=page)),
    get=simple_get(lambda oid, max_description_chars=600:
                   amazon_get("com", str(oid), max_description_chars)),
    verified=True, needs_proxy=False,
    note="parser verified on amazon.com; EU domains answer with an AWS WAF challenge",
))

register(Marketplace(
    name="ebay", title="eBay", country="us", currency="USD",
    search=rows_search("ebay.com",
                       lambda q, limit, page: ebay_search("ebay.com", "USD", q,
                                                          limit=limit, page=page)),
    get=simple_get(lambda oid, max_description_chars=600:
                   ebay_get("ebay.com", str(oid), max_description_chars)),
    verified=False, needs_proxy=True,
    note="HTTP 403 without a proxy",
))

register(Marketplace(
    name="walmart", title="Walmart", country="us", currency="USD",
    search=rows_search(
        "walmart",
        lambda q, limit, page: css_search(
            "walmart", "https://www.walmart.com/search?q={q}",
            query=q, limit=limit, page=page, currency="USD",
            item_sel="div[data-item-id]",
            title_sel="[data-automation-id='product-title']",
            link_sel="a[href*='/ip/']",
            price_sel="[data-automation-id='product-price']",
            id_regex=r"/ip/(?:[^/]+/)?(\d{6,})",
        ),
    ),
    verified=False, needs_proxy=True,
    note="Akamai returns 444 without a proxy; markup not verified",
))
