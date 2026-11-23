"""Measure a proxy list instead of trusting it.

    python tools/pool_check.py                       # 300 random proxies from public lists
    python tools/pool_check.py --limit 2000 -c 300   # more of them, more parallel
    python tools/pool_check.py --file my.txt         # your own list
    python tools/pool_check.py --country RU          # keep only this exit country

Prints how many proxies are alive, the latency distribution, and writes the
working ones to `logs/proxies_alive.txt` (or --out). Use the file with
`EXTSEARCH_PROXIES=@logs/proxies_alive.txt` or `set_proxies(open(...).read())`.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from curl_cffi import requests as cffi

from extsearch.pools import collect

CHECK_URL = "http://cp.cloudflare.com/generate_204"
GEO_URL = "http://ip-api.com/json/{ip}?fields=status,countryCode"


async def _exit_ip(proxy: str, timeout: float) -> str | None:
    try:
        async with cffi.AsyncSession(timeout=timeout) as session:
            r = await session.get("https://api.ipify.org?format=json",
                                  proxies={"http": proxy, "https": proxy})
            if r.status_code == 200:
                return r.json().get("ip")
    except Exception:
        return None
    return None


async def _check(proxy: str, timeout: float, geo: bool, sem: asyncio.Semaphore) -> dict | None:
    async with sem:
        started = time.monotonic()
        try:
            async with cffi.AsyncSession(timeout=timeout) as session:
                r = await session.get(CHECK_URL, proxies={"http": proxy, "https": proxy})
            if r.status_code not in (200, 204):
                return None
            latency = int((time.monotonic() - started) * 1000)
        except Exception:
            return None

    out = {"proxy": proxy, "latency_ms": latency}
    if geo:
        ip = await _exit_ip(proxy, timeout)
        if ip:
            out["ip"] = ip
            try:
                async with cffi.AsyncSession(timeout=timeout) as session:
                    g = await session.get(GEO_URL.format(ip=ip))
                    data = g.json()
                    if data.get("status") == "success":
                        out["country"] = data.get("countryCode")
            except Exception:
                pass
    return out


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=300, help="how many proxies to test")
    parser.add_argument("-c", "--concurrency", type=int, default=200)
    parser.add_argument("--timeout", type=float, default=6.0)
    parser.add_argument("--file", help="read proxies from this file instead of public lists")
    parser.add_argument("--socks", action="store_true", help="include socks5 lists")
    parser.add_argument("--geo", action="store_true", help="resolve exit country (slower)")
    parser.add_argument("--country", help="keep only this exit country, implies --geo")
    parser.add_argument("--out", default="logs/proxies_alive.txt")
    args = parser.parse_args()

    if args.file:
        raw = Path(args.file).expanduser().read_text(encoding="utf-8", errors="replace")
        from extsearch.proxies import parse_proxies
        pool = parse_proxies(raw)
    else:
        print("collecting lists...", flush=True)
        pool = await collect(include_socks=args.socks)
    print(f"collected: {len(pool)} unique proxies")

    sample = random.sample(pool, min(args.limit, len(pool)))
    sem = asyncio.Semaphore(args.concurrency)
    started = time.monotonic()
    results = [r for r in await asyncio.gather(
        *(_check(p, args.timeout, args.geo or bool(args.country), sem) for p in sample)) if r]
    elapsed = time.monotonic() - started

    alive = sorted(results, key=lambda r: r["latency_ms"])
    if args.country:
        code = args.country.upper()
        alive = [r for r in alive if r.get("country") == code]

    print(f"checked {len(sample)} in {elapsed:.1f}s | alive: {len(alive)} "
          f"({len(alive) / max(len(sample), 1) * 100:.1f}%)")
    if alive:
        lat = [r["latency_ms"] for r in alive]
        print(f"latency ms: min {lat[0]} | median {int(statistics.median(lat))} | "
              f"p90 {lat[int(len(lat) * 0.9) - 1]}")
        by_country: dict[str, int] = {}
        for r in alive:
            by_country[r.get("country") or "??"] = by_country.get(r.get("country") or "??", 0) + 1
        if len(by_country) > 1:
            top = sorted(by_country.items(), key=lambda kv: -kv[1])[:8]
            print("countries:", ", ".join(f"{c}:{n}" for c, n in top))

        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("\n".join(r["proxy"] for r in alive) + "\n", encoding="utf-8")
        print(f"saved: {out}")
        out.with_suffix(".json").write_text(json.dumps(alive, indent=2), encoding="utf-8")
    return 0 if alive else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
