"""Group the same product across marketplaces and show the best price.

A search returns one long list; the same product appears several times, once
per shop. ``compare`` normalises titles, groups the matches and shows which
source is cheapest for each product.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from .search import _core_tokens, search

_NONWORD = re.compile(r"[()\[\]{}«»\"']")
_CLEAN = re.compile(r"[^0-9a-zа-яіїєґё]+")
# Suffixes sellers append to titles: article codes and "бывший в употреблении".
_TAIL = re.compile(r"\s+(б/у|б\/в|бу|ref|restored|новинка)$", re.IGNORECASE)


def _key(title: str) -> str:
    """Group key from the title: lowercase words, no seller article, no noise."""
    text = _NONWORD.sub(" ", title.lower())
    text = _CLEAN.sub(" ", text)
    words = [w for w in text.split() if len(w) > 1 and not _TAIL.match(w)]
    return " ".join(words[:4])


def _group(items: list[dict[str, Any]]) -> dict[str, Any] | None:
    rows = [row for row in items if row.get("price") is not None]
    if not rows:
        return None
    rows.sort(key=lambda r: (r.get("price_usd") or r["price"]))
    best = rows[0]
    worst = rows[-1]
    out: dict[str, Any] = {
        "key": _key(rows[0].get("title") or ""),
        "title": best["title"],
        "offers": rows,
        "best": best,
        "sources": len({row["source"] for row in rows}),
    }
    if worst["price"] and best["price"] and worst["price"] > best["price"]:
        out["savings"] = worst["price"] - best["price"]
    return out


async def compare(
    query: str,
    *,
    where: str = "all",
    limit: int = 12,
    page: int = 1,
    price_min: float | None = None,
    price_max: float | None = None,
    min_rating: float | None = None,
    sort: str = "relevance",
    strict: bool = False,
    proxies: str | list[str] | None = None,
    timeout: float | None = None,
    progress: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Group equal products across sources and rank them by the best price.

    Args mirror ``search``; ``limit`` limits groups, not items. A group needs a
    price on at least one offer; groups without any price are dropped.
    """
    result = await search(
        # Comparison is about the product itself, not its accessories.
        query, where=where, limit=50, page=page, price_min=price_min,
        price_max=price_max, min_rating=min_rating, sort=sort, strict=strict,
        junk_filter=True,
        include_urls=True, proxies=proxies, timeout=timeout, progress=progress,
    )
    items = result.get("items") or []

    # The same token rule as price sorts: a sponge for an iPhone query is not a
    # phone just because a shop's own search matched something.
    core = _core_tokens(query)
    if core:
        kept = [item for item in items
                if any(token in (item.get("title") or "").lower() for token in core)]
        if kept:
            items = kept

    buckets: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        key = _key(item.get("title") or "")
        if not key:
            continue
        buckets.setdefault(key, []).append(item)

    groups = [g for g in (_group(rows) for rows in buckets.values()) if g]
    groups.sort(key=lambda g: g["best"].get("price_usd") or g["best"]["price"])

    return {
        "query": query,
        "where": result["where"],
        "groups": groups[:limit],
        "grouped": len(groups),
        "items": len(items),
        "sources": result["sources"],
        **({"notes": result["notes"]} if result.get("notes") else {}),
    }
