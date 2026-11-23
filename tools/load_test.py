"""Load test: run N searches with C in parallel, report latency and errors.

    python tools/load_test.py                     # 30 searches, 10 parallel, OLX
    python tools/load_test.py -n 100 -c 20 -w "comfy,brain,ctrs,foxtrot"
    python tools/load_test.py --query "iphone 15" --timeout 30

Measures what matters: requests per second, p50/p95 latency, error counts by
code. Everything is real requests against real marketplaces.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from extsearch import ExtSearchError, search


async def main() -> int:

    parser = argparse.ArgumentParser()
    parser.add_argument("--query", default="навушники")
    parser.add_argument("-n", "--requests", type=int, default=30)
    parser.add_argument("-c", "--concurrency", type=int, default=10)
    parser.add_argument("-w", "--where", default="olx")
    parser.add_argument("--limit", type=int, default=2)
    parser.add_argument("--timeout", type=float, default=25.0)
    args = parser.parse_args()

    latencies: list[float] = []
    errors: dict[str, int] = {}
    items_total = 0

    async def one(sem: asyncio.Semaphore, index: int) -> None:
        nonlocal items_total
        # Vary the query itself: identical requests hit the response cache and
        # measure nothing. A numeric suffix keeps the search honest.
        query = f"{args.query} {index}"
        async with sem:
            started = time.monotonic()
            try:
                result = await search(query, where=args.where, limit=args.limit,
                                      timeout=args.timeout)
                items_total += len(result.get("items") or [])
                latencies.append(time.monotonic() - started)
            except ExtSearchError as exc:
                errors[exc.code] = errors.get(exc.code, 0) + 1
            except Exception as exc:
                errors[type(exc).__name__] = errors.get(type(exc).__name__, 0) + 1

    sem = asyncio.Semaphore(args.concurrency)
    started = time.monotonic()
    await asyncio.gather(*(one(sem, i) for i in range(args.requests)))
    wall = time.monotonic() - started

    print(f"requests: {args.requests} | concurrency: {args.concurrency} | "
          f"where: {args.where} | limit: {args.limit}")
    print(f"wall: {wall:.2f}s | throughput: {args.requests / wall:.1f} req/s | "
          f"items: {items_total}")

    if latencies:
        latencies.sort()
        p50 = latencies[len(latencies) // 2]
        p95 = latencies[min(len(latencies) - 1, int(len(latencies) * 0.95))]
        print(f"latency s: min {latencies[0]:.2f} | p50 {p50:.2f} | "
              f"p95 {p95:.2f} | max {latencies[-1]:.2f}")
    if errors:
        print("errors:", ", ".join(f"{code}×{n}" for code, n in sorted(errors.items())))
    else:
        print("errors: none")
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
