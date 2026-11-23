"""Minimal proof that the engine works: one screen of real output.

Run it, screenshot the result, paste it into the post. Everything printed is a
real request to a real marketplace: no mocks, no fixtures, no cached numbers
from a file.

    python tools/test.py                  # default query
    python tools/test.py "iphone 13"      # your own query

Exit code 0 when at least half of the probed marketplaces answered, 1 otherwise.
"""

from __future__ import annotations

import argparse
import asyncio
import platform
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from extsearch import REGISTRY, __version__, get_pool, search, select
from extsearch.errors import ExtSearchError

# One source per country, plus one that is known to need a proxy: reporting a
# failure is part of the proof.
DEFAULT_SOURCES = [
    "olx",          # ua  classifieds, JSON API
    "epicentr",     # ua  marketplace, Nuxt + data-product-* attributes
    "bigl",         # ua  marketplace, data-qaid anchors
    "olx_pl",       # pl  same OLX engine, different country
    "amazon_de",    # de  Amazon markup
    "yandex_market",  # ru  aggregator
    "aliexpress",   # global
    "rakuten",      # jp
    "ozon",         # ru  blocked without a proxy (expected failure on purpose)
]


def _short(text: str, width: int = 34) -> str:
    text = (text or "").replace("\n", " ").strip()
    return text if len(text) <= width else text[: width - 1] + "…"


async def probe(name: str, query: str, attempts: int = 2) -> dict:
    """One marketplace, one row. Retried once: a marketplace that is busy or
    throttles a burst of parallel requests should not fail the demo."""
    mp = REGISTRY[name]
    started = time.monotonic()
    error = "unknown"
    for attempt in range(attempts):
        try:
            res = await asyncio.wait_for(
                search(query, where=name, limit=3, per_source_limit=5), 60)
            items = res.get("items") or []
            if items:
                first = items[0]
                return {
                    "name": name,
                    "country": mp.country,
                    "ms": int((time.monotonic() - started) * 1000),
                    "n": len(items),
                    "sample": f'{first.get("price")} {first.get("currency") or ""} '
                              f'{_short(first.get("title") or "")}'.strip(),
                    "error": None,
                    "id": first.get("id"),
                }
            error = "no items"
        except ExtSearchError as exc:
            error = exc.code
        except Exception as exc:
            error = type(exc).__name__
        if attempt + 1 < attempts:
            await asyncio.sleep(1.5)
    return {
        "name": name, "country": mp.country,
        "ms": int((time.monotonic() - started) * 1000),
        "n": 0, "sample": "", "error": error, "id": None,
    }


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("query", nargs="?", default="fifine k669")
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args()

    verified = sum(1 for mp in REGISTRY.values() if mp.verified)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    print(f"extsearch {__version__} | python {platform.python_version()} "
          f"{platform.system().lower()} | {now}")
    print(f"registry: {len(REGISTRY)} marketplaces, {verified} verified | "
          f"proxies: {len(get_pool())} | query: {args.query!r}")
    print()

    started = time.monotonic()
    results = await asyncio.gather(*(probe(n, args.query) for n in DEFAULT_SOURCES))
    wall = int((time.monotonic() - started) * 1000)

    print(f'{"source":14}{"cc":4}{"ms":>6}{"n":>3}  {"first result":44}status')
    print("-" * 86)
    for r in results:
        status = "ok" if not r["error"] else r["error"]
        print(f'{r["name"]:14}{r["country"]:4}{r["ms"]:>6}{r["n"]:>3}  '
              f'{r["sample"][:44]:44}{status}')

    ok = [r for r in results if not r["error"]]
    failed = [r for r in results if r["error"]]
    print("-" * 86)
    print(f'{len(ok)}/{len(results)} marketplaces answered in {wall} ms; '
          f'{len(failed)} reported an error (expected for proxied sources)')

    # A product card from the first working source: shows select() and details.
    if ok:
        first_id = ok[0]["id"]
        try:
            card = await asyncio.wait_for(select(first_id, 200), 45)
            delivery = card.get("delivery") or []
            print(f'\nproduct card: {first_id} -> "{_short(card.get("title") or "", 40)}" '
                  f'| {card.get("price")} {card.get("currency")}')
            print(f'  delivery options parsed: {len(delivery)}'
                  + (f' (e.g. {delivery[0]["method"]})' if delivery else ""))
            print(f'  url: {card.get("url")}')
        except Exception as exc:
            print(f'\nproduct card: {first_id} -> {type(exc).__name__}: {exc}')

    return 0 if len(ok) * 2 >= len(results) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
