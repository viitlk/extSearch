"""CIS: OLX Kazakhstan and Uzbekistan, Kaspi (KZ)."""

from __future__ import annotations

from . import Marketplace, register
from .adapters import css_search, olx_adapter, rows_search

for _code, _domain, _lang, _currency, _title in [
    ("kz", "www.olx.kz", "kz", "KZT", "OLX Қазақстан"),
    ("uz", "www.olx.uz", "uz", "UZS", "OLX Oʻzbekiston"),
]:
    _search, _get = olx_adapter(_domain, _lang, _currency)
    register(Marketplace(
        name=f"olx_{_code}", title=_title, country=_code, currency=_currency,
        search=_search, get=_get, note="OLX classifieds",
    ))

register(Marketplace(
    name="kaspi", title="Kaspi.kz", country="kz", currency="KZT",
    search=rows_search(
        "kaspi",
        lambda q, limit, page: css_search(
            "kaspi", "https://kaspi.kz/shop/search/?text={q}",
            query=q, limit=limit, page=page, currency="KZT",
            item_sel="div.item-card",
            title_sel="a.item-card__name",
            link_sel="a.item-card__name",
            price_sel="div.item-card__prices-price",
            id_regex=r"/shop/p/(\d+)",
        ),
    ),
    verified=False,
    note="results are rendered by JS; markup not verified",
))
