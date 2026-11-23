"""Search across every marketplace at once, plus product cards.

This module knows nothing about MCP or HTTP: mcp_server.py, api.py and cli.py
call it. Failures are raised as ExtSearchError and turned into JSON or HTTP
status codes by the facades.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any, Literal

from .config import to_usd
from .errors import ExtSearchError
from .marketplaces import REGISTRY, Marketplace, catalog, resolve
from .olx import _is_junk, _query_is_accessory
from .proxies import set_proxies

SortKey = Literal["relevance", "newest", "cheapest", "expensive",
                  "rating", "rating_asc", "reviews", "reviews_asc", "discount"]

SORTS = ("relevance", "newest", "cheapest", "expensive",
         "rating", "rating_asc", "reviews", "reviews_asc", "discount")

LOCAL_SORTS = ("rating", "rating_asc", "reviews", "reviews_asc", "discount")

# Words that appear in titles of every category: a boom arm "for a microphone"
# is not a microphone. Price sorting must not treat them as a match.
GENERIC_WORDS = {
    "для", "под", "про", "the", "and", "with", "for",
    "microphone", "мікрофон", "микрофон", "mic",
    "phone", "телефон", "смартфон", "камера", "camera",
    "навушники", "наушники", "headphones", "кабель", "cable",
}


def _core_tokens(query: str) -> list[str]:
    """Distinctive words of the query: long enough and not a category name."""
    words = re.findall(r"[0-9a-zа-яіїєґё]{4,}", (query or "").lower())
    return [word for word in words if word not in GENERIC_WORDS]


def _rank(rows: list[dict[str, Any]], sort: str) -> list[dict[str, Any]]:
    if sort == "cheapest":
        return sorted(rows, key=lambda r: (r.get("price_usd") is None, r.get("price_usd") or 0))
    if sort == "expensive":
        return sorted(rows, key=lambda r: (r.get("price_usd") is None, -(r.get("price_usd") or 0)))
    if sort == "rating":
        return sorted(rows, key=lambda r: (r.get("rating") is None,
                                           -(r.get("rating") or 0),
                                           -(r.get("reviews_count") or 0)))
    if sort == "rating_asc":
        return sorted(rows, key=lambda r: (r.get("rating") is None, r.get("rating") or 0))
    if sort == "reviews":
        return sorted(rows, key=lambda r: (r.get("reviews_count") is None,
                                           -(r.get("reviews_count") or 0)))
    if sort == "reviews_asc":
        return sorted(rows, key=lambda r: (r.get("reviews_count") is None,
                                           r.get("reviews_count") or 0))
    if sort == "discount":
        def cut(row: dict[str, Any]) -> float:
            price, old = row.get("price"), row.get("old_price")
            if not price or not old or old <= price:
                return -1.0
            return (old - price) / old
        return sorted(rows, key=lambda r: -cut(r))
    return sorted(rows, key=lambda r: (-(r.get("relevance") or 0),
                                       r.get("price_usd") is None,
                                       r.get("price_usd") or 0))


async def search(
    query: str,
    *,
    where: str = "all",
    limit: int = 10,
    page: int = 1,
    price_min: float | None = None,
    price_max: float | None = None,
    min_rating: float | None = None,
    sort: SortKey | str = "relevance",
    strict: bool = False,
    junk_filter: bool | None = None,
    include_urls: bool = True,
    details: int = 0,
    per_source_limit: int | None = None,
    free_only: bool = False,
    proxies: str | list[str] | None = None,
    timeout: float | None = None,
) -> dict[str, Any]:
    """Find a product across the marketplaces of the selected countries.

    Args:
        query: search phrase.
        where: "all", "ua", "ua,ru", "olx,ozon", "all,-ru" — countries and/or
            marketplace names, "-" excludes.
        limit: how many items to return in total (1..50).
        page: page number, starting at 1.
        price_min / price_max: price bounds in the item currency.
        min_rating: drop items without a rating (or below it).
        sort: relevance | newest | cheapest | expensive | rating | rating_asc
            | reviews | reviews_asc | discount. Price sorting uses price_usd,
            so items from different countries are comparable.
        strict: drop items whose title has none of the query words.
        junk_filter: None (auto for price sorts) | True | False.
        include_urls: add the product URL to every item.
        details: fetch delivery/payment/reviews for the first N items.
        per_source_limit: how many items to request from each marketplace.
        free_only: OLX "give away for free" category (UA only).
        proxies: list of "TYPE://IP:PORT"; replaces the process proxy pool.
        timeout: per-marketplace timeout in seconds.
    """
    if proxies is not None:
        set_proxies(proxies)
    query = (query or "").strip()
    if not query and not free_only:
        raise ExtSearchError("bad_argument", "query must not be empty")
    if sort not in SORTS:
        raise ExtSearchError("bad_argument", f"sort must be one of: {', '.join(SORTS)}")
    limit = max(1, min(int(limit), 50))
    per_source = max(1, min(int(per_source_limit or limit), 50))
    page = max(1, int(page))

    try:
        wanted, excluded, notes = resolve(where)
    except ValueError as exc:
        raise ExtSearchError("bad_argument", str(exc)) from None

    if free_only and "olx" not in wanted:
        raise ExtSearchError("bad_argument",
                             "free_only works with OLX only: add olx to where")

    if junk_filter is None and sort in ("cheapest", "expensive", "discount"):
        junk_filter = not _query_is_accessory(query)

    async def run_one(
        mp: Marketplace,
    ) -> tuple[str, list[dict[str, Any]], dict[str, Any] | None, ExtSearchError | None]:
        try:
            coro = mp.search(
                query, limit=per_source, page=page,
                price_min=int(price_min) if price_min is not None else None,
                price_max=int(price_max) if price_max is not None else None,
                sort=sort, strict=strict, include_urls=include_urls,
                free_only=free_only,
            )
            res = await asyncio.wait_for(coro, timeout) if timeout else await coro
        except asyncio.TimeoutError:
            err = ExtSearchError("timeout", f"{mp.name}: no answer within {timeout}s",
                                 source=mp.name, country=mp.country)
            return mp.name, [], {"returned": 0, "error": "timeout", "hint": err.message}, err
        except ExtSearchError as exc:
            exc.source = exc.source or mp.name
            exc.country = exc.country or mp.country
            info: dict[str, Any] = {"returned": 0, "error": exc.code}
            if exc.hint:
                info["hint"] = exc.hint
            return mp.name, [], info, exc
        except Exception as exc:
            err = ExtSearchError(type(exc).__name__, f"{mp.name}: {exc}",
                                 source=mp.name, country=mp.country)
            return mp.name, [], {"returned": 0, "error": type(exc).__name__}, err

        items = []
        for row in res.get("items") or []:
            row = dict(row)
            row["source"] = mp.name
            row["country"] = mp.country
            row["id"] = f"{mp.name}:{row['id']}"
            price_usd = to_usd(row.get("price"), row.get("currency"))
            if price_usd is not None:
                row["price_usd"] = price_usd
            items.append(row)
        info = {"returned": len(items), "total": res.get("total")}
        if res.get("notes"):
            info["notes"] = res["notes"]
        return mp.name, items, info, None

    outcomes = await asyncio.gather(*(run_one(REGISTRY[name]) for name in wanted))

    by_source: dict[str, list[dict[str, Any]]] = {}
    sources: dict[str, Any] = {}
    failures: list[ExtSearchError] = []
    for name, items, info, err in outcomes:
        if info is not None:
            sources[name] = info
        if err is not None:
            failures.append(err)
        if items:
            by_source[name] = items

    failed = [name for name, info in sources.items()
              if info.get("error") and not by_source.get(name)]
    if not by_source:
        # A single requested marketplace should report its own error (blocked,
        # rate_limited, ...) instead of a generic "no results".
        if len(wanted) == 1 and failures:
            raise failures[0]
        raise ExtSearchError(
            "no_results",
            "No marketplace returned items. " + (
                "; ".join(f"{n}: {sources[n].get('error')}" for n in failed)
                or "empty result set"),
            hint="try another country (where=...) or add proxies",
            details={"sources": sources},
        )

    if min_rating is not None:
        before = sum(len(rows) for rows in by_source.values())
        for name in list(by_source):
            by_source[name] = [row for row in by_source[name]
                               if (row.get("rating") or 0) >= float(min_rating)]
            if not by_source[name]:
                by_source.pop(name)
        dropped = before - sum(len(rows) for rows in by_source.values())
        if dropped:
            notes.append(f"min_rating={min_rating}: dropped {dropped} items without a rating")

    for name in by_source:
        by_source[name] = _rank(by_source[name], sort)

    mixed = len(by_source) > 1
    currencies = {row.get("currency") for rows in by_source.values() for row in rows}
    if len(currencies) > 1:
        notes.append("mixed currencies (" + ", ".join(sorted(c for c in currencies if c)) +
                     "); prices are compared via the approximate price_usd")

    if mixed and sort in ("relevance", "newest"):
        merged: list[dict[str, Any]] = []
        queues = {name: list(by_source[name]) for name in wanted if name in by_source}
        while any(queues.values()):
            for name in list(queues):
                if queues[name]:
                    merged.append(queues[name].pop(0))
    elif sort in ("cheapest", "expensive"):
        merged = [row for name in wanted for row in by_source.get(name, [])]
        merged.sort(key=(lambda r: (r.get("price_usd") is None, r.get("price_usd") or 0))
                   if sort == "cheapest"
                   else (lambda r: (r.get("price_usd") is None, -(r.get("price_usd") or 0))))
    elif sort in LOCAL_SORTS:
        merged = [row for name in wanted for row in by_source.get(name, [])]
        merged = _rank(merged, sort)
    else:
        merged = [row for name in wanted for row in by_source.get(name, [])]

    if junk_filter:
        before = len(merged)
        merged = [row for row in merged if not _is_junk(row.get("title") or "")]
        if before != len(merged):
            notes.append(f"junk_filter: removed {before - len(merged)} accessories")

    # Price sorting looks at the number only, so anything that merely shares a
    # generic word with the query wins: a boom arm "for a microphone", a webcam
    # "with a microphone", a cosmetic sponge from a shop whose own search matched
    # something else. Keep rows that mention a distinctive query token (brand or
    # model); fall back to the plain relevance floor when the query has none.
    if sort in ("cheapest", "expensive", "discount"):
        core = _core_tokens(query)
        if core:
            kept = [row for row in merged
                    if any(token in (row.get("title") or "").lower() for token in core)]
            if kept:
                dropped = len(merged) - len(kept)
                if dropped:
                    notes.append(f"sort={sort}: dropped {dropped} rows without "
                                 f"{', '.join(core[:2])!r} in the title")
                merged = kept
        else:
            kept = [row for row in merged if (row.get("relevance") or 0) > 0]
            dropped = len(merged) - len(kept)
            if dropped:
                notes.append(f"sort={sort}: dropped {dropped} rows with no query word "
                             "in the title")
            merged = kept

    result: dict[str, Any] = {
        "query": query,
        "where": wanted,
        "excluded": excluded,
        "countries": sorted({REGISTRY[n].country for n in wanted}),
        "returned": min(len(merged), limit),
        "per_source_limit": per_source,
        "mixed": mixed,
        "sort": sort,
        "sources": sources,
        "items": merged[:limit],
    }

    if details and details > 0:
        await _attach_details(result["items"], min(int(details), len(result["items"])))
        result["details_loaded"] = sum(
            1 for row in result["items"] if "delivery" in row or "payment" in row)
    if failed:
        notes.append("no answer: " + ", ".join(failed))
    if notes:
        result["notes"] = notes
    return result


async def select(item_id: str, max_description_chars: int = 600) -> dict[str, Any]:
    """Product card by an id from search results: "ozon:12345", "amazon:B0ABC…"."""
    raw = str(item_id).strip()
    source, _, offer_id = raw.partition(":")
    source = source.strip().lower()
    if not offer_id:
        source, offer_id = "olx", raw
    if source not in REGISTRY:
        raise ExtSearchError(
            "bad_argument",
            f"unknown source {source!r}. Available: {', '.join(sorted(REGISTRY))}",
        )
    mp = REGISTRY[source]
    if mp.get is None:
        raise ExtSearchError("unavailable",
                             f"{source} has no product-card parser, search only")
    data = await mp.get(offer_id.strip(), max_description_chars)
    data.setdefault("source", source)
    data.setdefault("country", mp.country)
    return data


async def _load_details(item_id: str, max_description_chars: int) -> dict[str, Any]:
    return await select(item_id, max_description_chars)


async def _attach_details(rows: list[dict[str, Any]], count: int) -> None:
    sem = asyncio.Semaphore(2)

    async def one(row: dict[str, Any]) -> None:
        async with sem:
            try:
                data = await _load_details(row["id"], 200)
            except ExtSearchError as exc:
                row["details_error"] = exc.code
                return
            except Exception as exc:
                row["details_error"] = type(exc).__name__
                return
        for key in ("delivery", "payment", "reviews", "free_shipping_from"):
            if data.get(key):
                row[key] = data[key]
        if data.get("price") is not None and row.get("price") is None:
            row["price"] = data["price"]

    await asyncio.gather(*(one(row) for row in rows[:count]))


def sources_catalog() -> dict[str, Any]:
    """What is available: countries, marketplaces, verification status."""
    return catalog()
