"""Custom proxy pool for extsearch: collect public lists, measure them, use them.

Not part of the library on purpose — this is a policy experiment, not engine code.
The library only knows how to rotate a list you give it; where that list comes
from is your business.

    python custom/proxy_pool.py collect                 # just download and dedupe
    python custom/proxy_pool.py check --limit 300       # measure liveness + latency
    python custom/proxy_pool.py check --target olx.pl   # measure against a real site
    python custom/proxy_pool.py apply --file alive.txt "fifine k669"

Uses `import extsearch` for the HTTP layer, so it inherits the same TLS profiles
and throttling as the library. If extsearch is not installed, run from the repo
root: the path is added below.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import re
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from curl_cffi import requests as cffi

import extsearch
from extsearch.errors import ExtSearchError
from extsearch.net import fetch_text
from extsearch.proxies import _normalize, parse_proxies

SOURCES = (
    "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
    "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",
    "https://raw.githubusercontent.com/proxifly/free-proxy-list/main/proxies/protocols/http/data.txt",
)

# Cheap 204 endpoint: measures reachability, not anti-bot tolerance.
PING_URL = "http://cp.cloudflare.com/generate_204"
GEO_URL = "http://ip-api.com/json/{ip}?fields=status,countryCode"
# Real targets, when you want to know whether a proxy survives a marketplace.
TARGETS = {
    "olx.pl": "https://www.olx.pl/api/v1/offers/?query=iphone&limit=2",
    "amazon": "https://www.amazon.co.uk/s?k=iphone",
    "ozon": "https://www.ozon.ru/search/?text=iphone",
}

_LINE = re.compile(r"^(?:[a-z0-9]+://)?[^\s:@]+(?::[^\s@]*)?@?[\w.\-]+:\d{2,5}$", re.I)


def _parse_line(line: str) -> str | None:
    item = line.strip()
    if not item or item.startswith("#") or not _LINE.match(item):
        return None
    if "://" not in item:
        item = "http://" + item
    try:
        return _normalize(item)
    except ValueError:
        return None


async def collect(timeout: float = 20.0) -> list[str]:
    found: dict[str, None] = {}
    for url in SOURCES:
        try:
            text = await fetch_text(url, timeout=timeout, ttl=0, source="proxy-list")
        except ExtSearchError:
            continue
        for line in text.splitlines():
            proxy = _parse_line(line)
            if proxy:
                found.setdefault(proxy, None)
    return list(found)


async def _probe(proxy: str, url: str, timeout: float, sem: asyncio.Semaphore,
                 min_bytes: int) -> dict | None:
    async with sem:
        started = time.monotonic()
        try:
            async with cffi.AsyncSession(timeout=timeout, impersonate="chrome") as session:
                resp = await session.get(url, proxies={"http": proxy, "https": proxy})
            body = resp.text or ""
            if resp.status_code >= 400 or len(body) < min_bytes:
                return None
            return {"proxy": proxy,
                    "status": resp.status_code,
                    "latency_ms": int((time.monotonic() - started) * 1000),
                    "bytes": len(body)}
        except Exception:
            return None


async def _geo(proxy: str, timeout: float) -> str | None:
    try:
        async with cffi.AsyncSession(timeout=timeout) as session:
            ip = (await session.get("https://api.ipify.org?format=json",
                                    proxies={"http": proxy, "https": proxy})).json()["ip"]
            data = (await session.get(GEO_URL.format(ip=ip))).json()
            return data.get("countryCode") if data.get("status") == "success" else None
    except Exception:
        return None


async def check(pool: list[str], *, limit: int, concurrency: int, timeout: float,
                target: str | None, geo: bool, country: str | None) -> list[dict]:
    sample = random.sample(pool, min(limit, len(pool)))
    url = TARGETS.get(target or "", PING_URL)
    min_bytes = 0 if url == PING_URL else 20_000
    sem = asyncio.Semaphore(concurrency)
    started = time.monotonic()
    results = [r for r in await asyncio.gather(
        *(_probe(p, url, timeout, sem, min_bytes) for p in sample)) if r]
    elapsed = time.monotonic() - started

    if geo or country:
        for row in results:
            row["country"] = await _geo(row["proxy"], timeout)
    if country:
        results = [r for r in results if r.get("country") == country.upper()]

    results.sort(key=lambda r: r["latency_ms"])
    print(f"checked {len(sample)} against {url.split('/')[2]} in {elapsed:.1f}s")
    print(f"usable: {len(results)} ({len(results) / max(len(sample), 1) * 100:.1f}%)")
    if results:
        lat = [r["latency_ms"] for r in results]
        print(f"latency ms: min {lat[0]} | median {int(statistics.median(lat))} | "
              f"p90 {lat[max(0, int(len(lat) * 0.9) - 1)]}")
        countries: dict[str, int] = {}
        for row in results:
            if row.get("country"):
                countries[row["country"]] = countries.get(row["country"], 0) + 1
        if countries:
            top = sorted(countries.items(), key=lambda kv: -kv[1])[:8]
            print("countries:", ", ".join(f"{c}:{n}" for c, n in top))
    return results


def _save(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(r["proxy"] for r in rows) + "\n", encoding="utf-8")
    path.with_suffix(".json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    print(f"saved: {path}")


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("collect", help="download lists and count unique proxies")

    p = sub.add_parser("check", help="measure proxies")
    p.add_argument("--limit", type=int, default=300)
    p.add_argument("-c", "--concurrency", type=int, default=200)
    p.add_argument("--timeout", type=float, default=6.0)
    p.add_argument("--target", choices=sorted(TARGETS), help="marketplace to test against")
    p.add_argument("--geo", action="store_true")
    p.add_argument("--country")
    p.add_argument("--file", help="your own list instead of public lists")
    p.add_argument("--out", default="logs/proxies_alive.txt")

    a = sub.add_parser("apply", help="load a list into extsearch and run one search")
    a.add_argument("--file", required=True)
    a.add_argument("query")
    a.add_argument("--where", default="ua")
    a.add_argument("--limit", type=int, default=3)

    args = parser.parse_args()

    if args.cmd == "collect":
        pool = await collect()
        print(f"unique proxies: {len(pool)}")
        print("examples:", ", ".join(pool[:5]))
        return 0

    if args.cmd == "check":
        if args.file:
            pool = parse_proxies(Path(args.file).expanduser().read_text(encoding="utf-8"))
        else:
            pool = await collect()
        print(f"pool: {len(pool)}")
        rows = await check(pool, limit=args.limit, concurrency=args.concurrency,
                           timeout=args.timeout, target=args.target, geo=args.geo,
                           country=args.country)
        if rows:
            _save(rows, Path(args.out))
        return 0 if rows else 1

    proxies = parse_proxies(Path(args.file).expanduser().read_text(encoding="utf-8"))
    extsearch.set_proxies(proxies)
    print(f"pool loaded: {len(proxies)}")
    try:
        result = await extsearch.search(args.query, where=args.where, limit=args.limit)
    except ExtSearchError as exc:
        print(json.dumps(exc.as_dict(), ensure_ascii=False, indent=2))
        return 1
    for item in result["items"]:
        print(f'{item["price"]} {item["currency"]}  {item["source"]:<10} {item["title"][:50]}')
    print("sources:", {k: v.get("error", "ok") for k, v in result["sources"].items()})
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
