"""Smoke tests: unit checks plus a few real marketplace calls.

    python tests/smoke_test.py            # unit + network
    EXTSEARCH_OFFLINE=1 python tests/smoke_test.py   # unit only
    pytest tests/ -q                       # same via pytest

No mocking: the network part hits real marketplaces, which is exactly what
catches layout changes.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from extsearch import REGISTRY, ExtSearchError, parse_proxies, resolve  # noqa: E402
from extsearch.marketplaces.adapters import money  # noqa: E402
from extsearch.search import search, select, sources_catalog  # noqa: E402

OFFLINE = os.environ.get("EXTSEARCH_OFFLINE") == "1"
FAILED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    status = "ok  " if condition else "FAIL"
    print(f"[{status}] {name}{(' — ' + detail) if detail else ''}")
    if not condition:
        FAILED.append(name)


def test_units() -> None:
    check("registry has many countries", len(sources_catalog()["countries"]) >= 12,
          f'{len(sources_catalog()["countries"])} countries, {len(REGISTRY)} marketplaces')

    proxies = parse_proxies("socks5://1.2.3.4:1080, http://user:pass@5.6.7.8:3128")
    check("proxy parsing", proxies == [
        "socks5://1.2.3.4:1080", "http://user:pass@5.6.7.8:3128"], str(proxies))

    for bad in ("ftp://1.2.3.4:21", "1.2.3.4"):
        try:
            parse_proxies(bad)
            check(f"reject {bad}", False)
        except ValueError:
            check(f"reject {bad}", True)

    check("money: symbol after", money("1 234,56 €") == (1235, "EUR"), str(money("1 234,56 €")))
    check("money: symbol before", money("$29.99") == (30, "USD"), str(money("$29.99")))

    wanted, excluded, _ = resolve("ua")
    check("resolve country", "olx" in wanted and "auto" not in wanted)
    wanted, excluded, _ = resolve("all,-ru")
    check("resolve exclusion", "ozon" not in wanted and "olx" in wanted)
    wanted, _, _ = resolve("ozon")
    check("resolve marketplace", wanted == ["ozon"])


async def test_network() -> None:
    result = await search("fifine k669", where="ua", limit=5, per_source_limit=3)
    check("search ua", bool(result["items"]), f'{len(result["items"])} items')

    item_id = result["items"][0]["id"]
    card = await select(item_id)
    check("select by id", card.get("title") is not None, f'{item_id} -> {str(card.get("title"))[:40]}')

    pl = await search("fifine k669", where="olx_pl", limit=3, per_source_limit=3)
    check("olx_pl", pl["items"][0]["currency"] == "PLN",
          f'{pl["items"][0]["price"]} {pl["items"][0]["currency"]}')

    try:
        await search("iphone", where="allegro", limit=2, per_source_limit=2)
        check("blocked error shape", False, "allegro answered without a proxy")
    except ExtSearchError as exc:
        payload = exc.as_dict()
        check("blocked error shape",
              payload["error"] in {"blocked", "needs_proxy", "rate_limited"}
              and "message" in payload and "retryable" in payload,
              f'{payload["error"]}: {payload["message"][:50]}')

    cheap = await search("fifine", where="ua", sort="cheapest", limit=8,
                         per_source_limit=10)
    zero = [row for row in cheap["items"]
            if (row.get("price") or 0) == 0 and not row.get("free")]
    check("price sort: no zero-price rows", not zero, f"{len(zero)} rows with price 0")

    off = [row for row in cheap["items"]
           if "fifine" not in (row.get("title") or "").lower()]
    check("price sort: brand in title", not off,
          f"{len(off)} rows without the brand")

    from extsearch.web import web_get, web_search

    docs = await web_search("python asyncio gather", limit=3)
    check("web_search", bool(docs.get("results")), f'{len(docs.get("results") or [])} results')
    page = await web_get("https://example.com", max_chars=200)
    check("web_get", "example" in (page.get("text") or "").lower(),
          f'{page.get("returned_chars")} chars')


def test_api() -> None:
    try:
        from fastapi.testclient import TestClient
    except ImportError:
        check("api import", True, "skipped: httpx not installed")
        return
    from extsearch.api import app

    with TestClient(app) as client:
        health = client.get("/health")
        check("api /health", health.status_code == 200 and health.json()["status"] == "ok")
        catalog = client.get("/sources")
        check("api /sources", catalog.status_code == 200 and "countries" in catalog.json())
        bad = client.post("/search", json={"query": "", "where": "ua"})
        check("api error json", bad.status_code == 400 and "error" in bad.json()["detail"])


def main() -> int:
    test_units()
    test_api()
    if OFFLINE:
        print("\noffline mode: network tests skipped")
    else:
        asyncio.run(test_network())
    print()
    if FAILED:
        print("FAILED:", ", ".join(FAILED))
        return 1
    print("ALL OK")
    return 0


# pytest entry points
def test_units_pytest() -> None:
    test_units()


def test_network_pytest() -> None:
    if not OFFLINE:
        asyncio.run(test_network())


def test_api_pytest() -> None:
    test_api()


if __name__ == "__main__":
    raise SystemExit(main())
