"""Marketplace registry grouped by country.

Marketplace names are latin (``olx_pl``, ``wildberries``); countries use
ISO 3166-1 alpha-2 codes plus the pseudo-country ``global``.

    from extsearch.marketplaces import REGISTRY, resolve, catalog
    names, excluded, notes = resolve("ua,-olx")
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

SearchFn = Callable[..., Awaitable[dict[str, Any]]]
GetFn = Callable[..., Awaitable[dict[str, Any]]]


@dataclass(slots=True)
class Marketplace:
    name: str
    title: str
    country: str
    currency: str
    search: SearchFn
    get: GetFn | None = None
    verified: bool = True
    needs_proxy: bool = False
    default: bool = True   # included in "all" and in country-wide searches
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "title": self.title,
            "country": self.country,
            "currency": self.currency,
            "verified": self.verified,
            "needs_proxy": self.needs_proxy,
            "default": self.default,
            "can_get": self.get is not None,
            "note": self.note,
        }


REGISTRY: dict[str, Marketplace] = {}

COUNTRY_ORDER = (
    "ua", "pl", "ro", "bg", "pt", "kz", "uz",
    "ru", "us", "gb", "de", "fr", "it", "es", "eu", "jp", "in", "sg", "global",
)

COUNTRY_TITLES = {
    "ua": "Ukraine", "pl": "Poland", "ro": "Romania", "bg": "Bulgaria",
    "pt": "Portugal", "kz": "Kazakhstan", "uz": "Uzbekistan", "ru": "Russia",
    "us": "USA", "gb": "United Kingdom", "de": "Germany", "fr": "France",
    "it": "Italy", "es": "Spain", "eu": "Europe", "jp": "Japan",
    "in": "India", "sg": "Singapore", "global": "Global",
}

# Country groups: one token that expands to several countries
GROUPS = {
    "eu": ("pl", "ro", "bg", "pt", "de", "gb", "fr", "it", "es"),
    "europe": ("pl", "ro", "bg", "pt", "de", "gb", "fr", "it", "es"),
    "cis": ("kz", "uz"),
}

# Aliases people (and models) type by hand
ALIASES = {
    "украина": "ua", "україна": "ua", "ukraine": "ua", "ua": "ua",
    "польша": "pl", "poland": "pl", "pl": "pl",
    "румыния": "ro", "romania": "ro", "ro": "ro",
    "болгария": "bg", "bulgaria": "bg", "bg": "bg",
    "португалия": "pt", "portugal": "pt", "pt": "pt",
    "казахстан": "kz", "kazakhstan": "kz", "kz": "kz",
    "узбекистан": "uz", "uzbekistan": "uz", "uz": "uz",
    "россия": "ru", "рф": "ru", "russia": "ru", "ru": "ru",
    "сша": "us", "usa": "us", "america": "us", "us": "us",
    "великобритания": "gb", "англия": "gb", "britain": "gb", "uk": "gb", "gb": "gb",
    "германия": "de", "germany": "de", "de": "de",
    "франция": "fr", "france": "fr", "fr": "fr",
    "италия": "it", "italy": "it", "it": "it",
    "испания": "es", "spain": "es", "es": "es",
    "европа": "eu", "europe": "eu", "eu": "eu",
    "япония": "jp", "japan": "jp", "jp": "jp",
    "индия": "in", "india": "in", "in": "in",
    "сингапур": "sg", "singapore": "sg", "sg": "sg",
    "глобально": "global", "мир": "global", "world": "global", "intl": "global",
    "global": "global",
}


def register(marketplace: Marketplace) -> Marketplace:
    REGISTRY[marketplace.name] = marketplace
    return marketplace


def resolve(where: str | None = None) -> tuple[list[str], list[str], list[str]]:
    """Turn ``where`` into a list of marketplace names.

    Forms: "all" / "*" / "ua" / "ua,ru" / "olx,ozon" / "all,-ru,-olx".
    A leading "-", "!" or "~" excludes the token.
    """
    raw = (where or "all").strip().lower()
    notes: list[str] = []
    tokens = [t.strip() for t in raw.replace(";", ",").split(",") if t.strip()]
    if not tokens or any(t in ("all", "*", "everything") for t in tokens):
        picked: list[str] = []
        excluded: list[str] = []
    else:
        picked, excluded = [], []

    # Expand tokens into marketplace names
    def expand(token: str) -> list[str]:
        if token in REGISTRY:
            return [token]
        if token in GROUPS:
            countries = set(GROUPS[token])
            return [mp.name for mp in REGISTRY.values()
                    if mp.country in countries and mp.default]
        country = ALIASES.get(token, token)
        return [mp.name for mp in REGISTRY.values()
                if mp.country == country and mp.default]

    for token in tokens:
        if token in ("all", "*", "everything"):
            continue
        negative = token[0] in "-!~"
        value = token[1:] if negative else token
        names = expand(value)
        if not names:
            raise ValueError(f"unknown marketplace or country: {value!r}")
        target = excluded if negative else picked
        for name in names:
            if name not in target:
                target.append(name)

    if not picked:
        picked = [mp.name for mp in _ordered() if mp.default]
    else:
        picked = [mp.name for mp in _ordered() if mp.name in picked]

    excluded = [name for name in excluded if name in REGISTRY]
    wanted = [name for name in picked if name not in excluded]
    if not wanted:
        raise ValueError("no marketplaces left after exclusions")
    if excluded:
        notes.append("excluded: " + ", ".join(excluded))
    return wanted, excluded, notes


def _ordered() -> list[Marketplace]:
    def key(mp: Marketplace) -> tuple[int, str]:
        try:
            return COUNTRY_ORDER.index(mp.country), mp.name
        except ValueError:
            return len(COUNTRY_ORDER), mp.name
    return sorted(REGISTRY.values(), key=key)


def all_marketplaces() -> list[Marketplace]:
    return _ordered()


def by_country() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for mp in _ordered():
        out.setdefault(mp.country, []).append(mp.name)
    return out


def catalog() -> dict[str, Any]:
    """Summary for /sources and for models: what is available."""
    return {
        "countries": {
            code: {"title": COUNTRY_TITLES.get(code, code), "marketplaces": names}
            for code, names in by_country().items()
        },
        "marketplaces": [mp.as_dict() for mp in all_marketplaces()],
        "total": len(REGISTRY),
    }


def get(name: str) -> Marketplace:
    try:
        return REGISTRY[name]
    except KeyError:
        raise KeyError(f"marketplace {name!r} is not registered") from None


# Country modules are imported at the end: they register themselves
from . import asia, cis, eu, global_, ru, ua, us  # noqa: E402,F401
