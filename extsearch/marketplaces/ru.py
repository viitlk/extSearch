"""Russia: Yandex Market (works), Wildberries and Ozon (proxy required)."""

from __future__ import annotations

from . import Marketplace, register
from .adapters import (ozon_search, rows_search, simple_get, wildberries_search,
                       yandex_get, yandex_search)

register(Marketplace(
    name="yandex_market", title="Яндекс Маркет", country="ru", currency="RUB",
    search=rows_search("yandex_market", yandex_search),
    get=simple_get(yandex_get),
    note="shop aggregator; HTML results parse without a proxy",
))

register(Marketplace(
    name="wildberries", title="Wildberries", country="ru", currency="RUB",
    search=rows_search("wildberries", wildberries_search),
    verified=False, needs_proxy=True,
    note="public JSON API search.wb.ru; returns 403 from unsupported regions",
))

register(Marketplace(
    name="ozon", title="Ozon", country="ru", currency="RUB",
    search=rows_search("ozon", ozon_search),
    verified=False, needs_proxy=True,
    note="composer API; widgetStates layout changes, proxy required",
))
