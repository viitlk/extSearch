"""Check every marketplace one by one: how many rows and what they contain.

    python tools/check_sources.py              # all
    python tools/check_sources.py olx bigl ozon
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from extsearch import REGISTRY, search

QUERY = "fifine k669"


async def main() -> None:
    names = sys.argv[1:] or list(REGISTRY)
    for name in names:
        if name not in REGISTRY:
            print(f"{name:16} not in registry")
            continue
        try:
            res = await asyncio.wait_for(
                search(QUERY, where=name, limit=3, per_source_limit=6), 90)
            items = res["items"]
            first = items[0] if items else {}
            print(f"{name:16} ok   n={len(items):<2} "
                  f"{(first.get('title') or '')[:38]:40} "
                  f"{first.get('price')} {first.get('currency') or ''}")
        except Exception as exc:
            code = getattr(exc, "code", type(exc).__name__)
            print(f"{name:16} FAIL {code}: {str(exc)[:70]}")


if __name__ == "__main__":
    asyncio.run(main())
