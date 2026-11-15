"""Europe: OLX in four countries, Amazon in four, Allegro (PL)."""

from __future__ import annotations

from . import Marketplace, register
from .adapters import (amazon_get, amazon_search, css_search, olx_adapter,
                       rows_search, simple_get)

# --- OLX: same code, different domain ---------------------------------------

_OLX = [
    ("pl", "www.olx.pl", "pl", "PLN", "OLX Polska"),
    ("ro", "www.olx.ro", "ro", "RON", "OLX România"),
    ("bg", "www.olx.bg", "bg", "BGN", "OLX България"),
    ("pt", "www.olx.pt", "pt", "EUR", "OLX Portugal"),
]

for _code, _domain, _lang, _currency, _title in _OLX:
    _search, _get = olx_adapter(_domain, _lang, _currency)
    register(Marketplace(
        name=f"olx_{_code}", title=_title, country=_code, currency=_currency,
        search=_search, get=_get, note="OLX classifieds",
    ))

# --- Amazon: same markup, only domain and currency change -------------------

_AMAZON = [
    ("de", "de", "EUR", "Amazon.de"),
    ("gb", "co.uk", "GBP", "Amazon.co.uk"),
    ("fr", "fr", "EUR", "Amazon.fr"),
    ("it", "it", "EUR", "Amazon.it"),
    ("es", "es", "EUR", "Amazon.es"),
]

for _code, _domain, _currency, _title in _AMAZON:
    register(Marketplace(
        name=f"amazon_{_code}", title=_title, country=_code, currency=_currency,
        search=rows_search(
            f"amazon.{_domain}",
            lambda q, limit, page, d=_domain: amazon_search(d, q, limit=limit, page=page),
        ),
        get=simple_get(lambda oid, max_description_chars=600, d=_domain:
                       amazon_get(d, str(oid), max_description_chars)),
        verified=_domain == "de",
        needs_proxy=_domain != "de",
        note="Amazon; some domains answer with a captcha unless a proxy is set",
    ))

# --- Allegro: markup not verified (HTTP 403), works with a proxy ------------

register(Marketplace(
    name="allegro", title="Allegro", country="pl", currency="PLN",
    search=rows_search(
        "allegro",
        lambda q, limit, page: css_search(
            "allegro", "https://allegro.pl/listing?string={q}",
            query=q, limit=limit, page=page, currency="PLN",
            item_sel="article[data-analytics-view-custom-id]",
            title_sel="h2 a", link_sel="a[href*='/oferta/']",
            id_regex=r"-(\d{6,})(?:\?|$)",
        ),
    ),
    verified=False, needs_proxy=True,
    note="needs a proxy in a supported region",
))
