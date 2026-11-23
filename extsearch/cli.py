"""Command line interface.

    extsearch search "fifine k669" --where ua,pl --limit 5
    extsearch search "iphone 15" --where all --sort cheapest --proxy socks5://1.2.3.4:1080
    extsearch select amazon:B0ABC123
    extsearch sources --country ru
    extsearch web "python dataclass" --fetch-top 2
    extsearch mcp
    extsearch api --port 8000
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

from .config import __version__
from .errors import ExtSearchError
from .media import freelance_search, mod_search, video_search
from .proxies import set_proxies
from .search import search, select, sources_catalog
from .web import web_get, web_search

SORTS = ("relevance", "newest", "cheapest", "expensive",
         "rating", "rating_asc", "reviews", "reviews_asc", "discount")


def _print(data: Any, as_json: bool) -> None:
    if as_json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return
    print(json.dumps(data, ensure_ascii=False, indent=2))



def _progress_printer():
    """One live line: bar, percent, sources done, items found."""
    width = 24

    def callback(event: dict) -> None:
        kind = event.get("event")
        if kind == "start":
            print(f'searching {event["total"]} marketplaces...', file=sys.stderr)
            return
        if kind == "source":
            filled = int(width * event["percent"] / 100)
            bar = "#" * filled + "-" * (width - filled)
            mark = "ok" if event["status"] == "ok" else event.get("error", "error")
            sys.stderr.write(
                f'\r[{bar}] {event["percent"]:>3}%  {event["done"]}/{event["total"]}  '
                f'found {event["found"]:<4} {event["source"]}:{mark}'.ljust(78))
            sys.stderr.flush()
            return
        if kind in ("done", "error"):
            sys.stderr.write("\n")
            if kind == "error":
                sys.stderr.write(f'error: {event.get("error")}\n')
            sys.stderr.flush()

    return callback


def _money(value: Any, currency: Any) -> str:
    if value is None:
        return "—"
    return f"{value} {currency or ''}".strip()


def _print_items(result: dict[str, Any]) -> None:
    items = result.get("items") or []
    if not items:
        print("no items")
        return
    print(f'{result.get("returned", len(items))} of {len(items)} '
          f'for {result.get("query")!r} [{", ".join(result.get("where") or [])}]')
    print()
    for row in items:
        print(f'{_money(row.get("price"), row.get("currency")):>14}  '
              f'{row.get("source", ""):<14} {(row.get("title") or "")[:64]}')
        if row.get("url"):
            url = row["url"]
            print(f'{"":>14}  {url[:110]}{"…" if len(url) > 110 else ""}')
    notes = result.get("notes") or []
    if notes:
        print("\nnotes:")
        for note in notes:
            print("  -", note)


def _fail(exc: ExtSearchError) -> int:
    print(json.dumps(exc.as_dict(), ensure_ascii=False, indent=2), file=sys.stderr)
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="extsearch",
                                     description="Product search across marketplaces")
    parser.add_argument("--version", action="version", version=f"extsearch {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("search", help="search marketplaces")
    p.add_argument("query")
    p.add_argument("-w", "--where", default="all",
                   help='countries/marketplaces: all, ua, ua,ru, olx,ozon, all,-ru')
    p.add_argument("-n", "--limit", type=int, default=10)
    p.add_argument("--page", type=int, default=1)
    p.add_argument("--sort", choices=SORTS, default="relevance")
    p.add_argument("--min-price", type=float)
    p.add_argument("--max-price", type=float)
    p.add_argument("--min-rating", type=float)
    p.add_argument("--per-source", type=int)
    p.add_argument("--details", type=int, default=0)
    p.add_argument("--strict", action="store_true")
    p.add_argument("--client-side-junk", dest="junk", action="store_true", default=None)
    p.add_argument("--no-junk-filter", dest="junk", action="store_false")
    p.add_argument("--free", action="store_true", help="OLX give-away category (UA)")
    p.add_argument("--proxy", action="append", default=[],
                   help="TYPE://IP:PORT, repeatable")
    p.add_argument("--json", action="store_true")
    p.add_argument("--progress", action="store_true",
                   help="live progress bar on stderr")

    p = sub.add_parser("compare", help="same product across sources, best price first")
    p.add_argument("query")
    p.add_argument("-w", "--where", default="all")
    p.add_argument("-n", "--limit", type=int, default=10)
    p.add_argument("--min-price", type=float)
    p.add_argument("--max-price", type=float)
    p.add_argument("--json", action="store_true")
    p.add_argument("--progress", action="store_true")

    p = sub.add_parser("select", help="product card by id")
    p.add_argument("item_id")
    p.add_argument("--chars", type=int, default=600)
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("sources", help="list countries and marketplaces")
    p.add_argument("--country")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("web", help="web search")
    p.add_argument("query")
    p.add_argument("-n", "--limit", type=int, default=8)
    p.add_argument("--fetch-top", type=int, default=0)
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("get", help="fetch a page as text")
    p.add_argument("url")
    p.add_argument("--chars", type=int, default=4000)
    p.add_argument("--offset", type=int, default=0)
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("video", help="YouTube search")
    p.add_argument("query")
    p.add_argument("-n", "--limit", type=int, default=8)
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("mods", help="Minecraft mods")
    p.add_argument("query")
    p.add_argument("-n", "--limit", type=int, default=10)
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("freelance", help="freelance boards")
    p.add_argument("query")
    p.add_argument("--boards", default="all")
    p.add_argument("-n", "--limit", type=int, default=10)
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("mcp", help="run the MCP server")
    p.add_argument("--transport", default=None, help="stdio (default) or http")
    p.add_argument("--host", default=None)
    p.add_argument("--port", type=int, default=None)

    p = sub.add_parser("api", help="run the HTTP API")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--reload", action="store_true")
    return parser


def run(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "mcp":
        import os
        if args.transport:
            os.environ["EXTSEARCH_TRANSPORT"] = args.transport
        if args.host:
            os.environ["EXTSEARCH_HOST"] = args.host
        if args.port:
            os.environ["EXTSEARCH_PORT"] = str(args.port)
        from .mcp_server import main as mcp_main
        mcp_main()
        return 0

    if args.command == "api":
        import uvicorn
        uvicorn.run("extsearch.api:app", host=args.host, port=args.port, reload=args.reload)
        return 0

    if getattr(args, "proxy", None):
        try:
            set_proxies(args.proxy)
        except ValueError as exc:
            return _fail(ExtSearchError("bad_argument", str(exc)))

    try:
        if args.command == "search":
            result = asyncio.run(search(
                args.query, where=args.where, limit=args.limit, page=args.page,
                sort=args.sort, price_min=args.min_price, price_max=args.max_price,
                min_rating=args.min_rating, strict=args.strict, junk_filter=args.junk,
                details=args.details, per_source_limit=args.per_source,
                free_only=args.free, include_urls=True,
                progress=_progress_printer() if args.progress else None,
            ))
            if args.json:
                _print(result, True)
            else:
                _print_items(result)
            return 0
        if args.command == "compare":
            from .compare import compare
            result = asyncio.run(compare(
                args.query, where=args.where, limit=args.limit,
                price_min=args.min_price, price_max=args.max_price,
                progress=_progress_printer() if args.progress else None,
            ))
            if args.json:
                _print(result, True)
            else:
                for group in result["groups"]:
                    best = group["best"]
                    others = " / ".join(f'{o["source"]} {o["price"]}'
                                        for o in group["offers"][1:4])
                    saving = f' (saves {group.get("savings")})' if group.get("savings") else ""
                    print(f'{_money(best.get("price"), best.get("currency")):>14}  '
                          f'{best["title"][:58]}')
                    if others:
                        print(f'{"":>14}  also: {others}{saving}')
            return 0
        if args.command == "select":
            _print(asyncio.run(select(args.item_id, args.chars)), True)
            return 0
        if args.command == "sources":
            data = sources_catalog()
            if args.country:
                code = args.country.lower()
                data = {"country": code,
                        "marketplaces": data["countries"].get(code, {}).get("marketplaces", [])}
            _print(data, True)
            return 0
        if args.command == "web":
            _print(asyncio.run(web_search(args.query, limit=args.limit,
                                          fetch_top=args.fetch_top)), True)
            return 0
        if args.command == "get":
            _print(asyncio.run(web_get(args.url, max_chars=args.chars,
                                       offset=args.offset)), True)
            return 0
        if args.command == "video":
            _print(asyncio.run(video_search(args.query, limit=args.limit)), True)
            return 0
        if args.command == "mods":
            _print(asyncio.run(mod_search(args.query, limit=args.limit)), True)
            return 0
        if args.command == "freelance":
            _print(asyncio.run(freelance_search(args.query, boards=args.boards,
                                                limit=args.limit)), True)
            return 0
    except ExtSearchError as exc:
        return _fail(exc)
    return 2


def main() -> None:  # pragma: no cover - entry point
    raise SystemExit(run())


if __name__ == "__main__":  # pragma: no cover
    main()
