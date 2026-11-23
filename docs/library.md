# Library

Everything is asynchronous: `search`, `select` and the content tools are
coroutines. `sources_catalog`, `resolve`, proxy helpers and `set_proxies` are
plain functions.

```python
import asyncio
from extsearch import search

res = asyncio.run(search("fifine k669", where="ua", limit=5))
```

## `search()`

```python
await search(
    query: str,
    *,
    where: str = "all",
    limit: int = 10,
    page: int = 1,
    price_min: float | None = None,
    price_max: float | None = None,
    min_rating: float | None = None,
    sort: str = "relevance",
    strict: bool = False,
    junk_filter: bool | None = None,
    include_urls: bool = True,
    details: int = 0,
    per_source_limit: int | None = None,
    free_only: bool = False,
    proxies: str | list[str] | None = None,
    timeout: float | None = None,
) -> dict
```

| Parameter | Default | Notes |
|---|---|---|
| `query` | — | search phrase; may be empty only with `free_only=True` |
| `where` | `"all"` | countries and/or marketplace names, `-` excludes: `"ua"`, `"ua,ru"`, `"olx,ozon"`, `"all,-ru"`, `"eu"` |
| `limit` | `10` | items in the response, 1..50 |
| `page` | `1` | result page, forwarded to each marketplace |
| `price_min` / `price_max` | `None` | bounds in the marketplace currency |
| `min_rating` | `None` | drops items without a rating, and whole marketplaces left empty |
| `sort` | `"relevance"` | `relevance`, `newest`, `cheapest`, `expensive`, `rating`, `rating_asc`, `reviews`, `reviews_asc`, `discount` |
| `strict` | `False` | keeps only rows whose title contains a query word |
| `junk_filter` | `None` | `None` = auto (on for price sorts), `True`/`False` force it |
| `include_urls` | `True` | put the product URL into every item |
| `details` | `0` | fetch delivery/payment/reviews for the first N items (one request each) |
| `per_source_limit` | `limit` | items requested from each marketplace; raise it to get more variety |
| `free_only` | `False` | OLX give-away category, Ukraine only |
| `proxies` | `None` | replaces the process proxy pool for this and later calls |
| `timeout` | `None` | per-marketplace timeout in seconds |
| `progress` | `None` | callback receiving progress events while sources finish |

### Progress while a search runs

`progress` is called with a dict per event. Percent is `done / total`, `found` is
how many items have been collected so far — that is what a progress bar needs.

```python
import asyncio
from extsearch import search

def on_progress(event):
    if event["event"] == "source":
        print(f'[{event["percent"]:>3}%] {event["done"]}/{event["total"]} '
              f'{event["source"]}: {event["status"]} +{event["returned"]} '
              f'(total {event["found"]})')

asyncio.run(search("навушники", where="ua", progress=on_progress))
```

Events: `start` (sources list), `source` (one per marketplace, with `status`,
`returned`, `elapsed_ms`, `percent`, `found`), `done` (final `returned`,
`elapsed_ms`), `error` (when nothing was found). A callback that raises is
ignored on purpose: reporting must not break a search.

### How results are ordered

- `relevance` / `newest`: marketplaces are interleaved (`olx`, `prom`,
  `olx_pl`, `olx`, …) so one large catalogue cannot fill the page.
- `cheapest` / `expensive`: one global order by `price_usd`.
- `rating*` / `reviews*` / `discount`: one global order; marketplaces that do
  not expose ratings sink to the bottom.
- `junk_filter` removes accessories (`case`, `film`, `cable`) when sorting by
  price; it is disabled automatically when the query itself is about an
  accessory (`"iphone case"`).

### Example with filters and details

```python
import asyncio
from extsearch import search

async def main():
    res = await search(
        "rtx 4060",
        where="ua,pl",
        limit=10,
        price_min=8000,
        price_max=20000,
        sort="cheapest",
        details=3,
    )
    for item in res["items"]:
        delivery = item.get("delivery") or []
        print(item["price"], item["currency"], item["title"])
        for option in delivery:
            print("   ", option["method"], option["price"], option.get("note") or "")

asyncio.run(main())
```

## `compare()`

```python
await compare(query: str, *, where="all", limit=10, page=1, price_min=None,
              price_max=None, min_rating=None, sort="relevance", strict=False,
              proxies=None, timeout=None, progress=None) -> dict
```

Groups equal products across marketplaces and sorts them by the cheapest offer.
One search returns 50+ rows; the same product appears several times, once per
shop. `compare` normalises titles, drops accessories (junk filter is always on
here) and rows that do not mention a distinctive query token, then groups.

```python
res = await compare("iphone 15", where="ua")
for group in res["groups"]:
    print(group["best"]["price"], group["best"]["source"], group["title"])
    for offer in group["offers"][1:]:
        print("   also", offer["price"], offer["source"], "savings", group.get("savings"))
```

```json
{
  "key": "jbl tune 720bt",
  "title": "Навушники JBL Tune 720BT Black",
  "sources": 3,
  "savings": 800,
  "best": {"source": "brain", "price": 2399, "url": "..."},
  "offers": [{"source": "brain", ...}, {"source": "comfy", ...}]
}
```

`groups` is capped at `limit`; `items` tells how many rows went in, `sources` is
the per-marketplace status. The callback and the proxy options are the same as
in `search`.

## `select()`

```python
await select(item_id: str, max_description_chars: int = 600) -> dict
```

Accepts `"source:id"` or a bare number (treated as OLX). Returns `title`,
`price`, `currency`, `description`, `url`, `source`, `country`, and when the
marketplace exposes them: `delivery[]`, `payment[]`, `reviews`,
`free_shipping_from`, `old_price`, `rating`, `reviews_info`.

```python
card = await select("amazon:B0BZP61K18", max_description_chars=300)
card["delivery"]     # [{"method": "Nova Poshta", "price": None, "note": "carrier rate, not fixed", "source": "shop_profile"}]
```

A `null` delivery price means the shop does not publish a fixed price and
charges the carrier tariff. It is recorded honestly instead of guessed.

> [!NOTE]
> For marketplaces without numeric product ids (Amazon, eBay, AliExpress,
> Yandex Market, Rakuten) the id → URL map lives in memory. Items found in a
> previous process cannot be opened after a restart — run the search again.

## Catalog and resolution

```python
from extsearch import resolve, sources_catalog, by_country, REGISTRY

resolve("ua")               # (['allo', 'bigl', ...], [], [])
resolve("olx,ozon")         # (['olx', 'ozon'], [], [])
resolve("all,-ru")          # (everything except ru, ['ozon', 'wildberries', 'yandex_market'], ['excluded: ...'])
resolve("eu")               # group token: pl, ro, bg, pt, de, gb, fr, it, es

sources_catalog()           # {"countries": {...}, "marketplaces": [...], "total": 38}
by_country()                # {"ua": ["olx", ...], "ru": ["yandex_market", ...]}
REGISTRY["olx"].currency    # "UAH"
```

`resolve` raises `ValueError` for an unknown token; `search` converts that into
`ExtSearchError("bad_argument")`.

Each marketplace entry looks like this:

```json
{
  "name": "ozon",
  "title": "Ozon",
  "country": "ru",
  "currency": "RUB",
  "verified": false,
  "needs_proxy": true,
  "default": true,
  "can_get": false,
  "note": "composer API; widgetStates layout changes, proxy required"
}
```

## Proxies

```python
from extsearch import set_proxies, parse_proxies, get_pool

set_proxies("socks5://1.2.3.4:1080,http://user:pass@5.6.7.8:3128")
get_pool().stats()
# [{"proxy": "socks5://1.2.3.4:1080", "errors": 0, "cooling_down": false}, ...]

parse_proxies(["1.2.3.4:8080", "socks5://5.6.7.8:1080"])   # http is assumed without a scheme
```

Format: `TYPE://[user:pass@]IP:PORT`, where `TYPE` is `http`, `https`,
`socks4`, `socks4a`, `socks5` or `socks5h`.

Rotation: round-robin over the pool. A proxy that causes a network error or a
`403`/`429` is skipped for 120 seconds and the request is retried on the next
one. Credentials are masked everywhere they surface (`stats()`, API responses).

Environment: `EXTSEARCH_PROXIES="socks5://...,http://..."` at import time.

## Errors

```python
from extsearch import ExtSearchError

try:
    await search("iphone", where="ozon")
except ExtSearchError as exc:
    exc.code         # "blocked"
    exc.message      # "www.ozon.ru rejected the request (HTTP 403)"
    exc.hint         # 'route it through a proxy (proxies=["TYPE://IP:PORT"])'
    exc.source       # "ozon"
    exc.country      # "ru"
    exc.retryable    # True
    exc.details      # {} — extra context, e.g. {"sources": {...}}
    exc.as_dict()    # JSON-ready dict
```

| Code | Meaning | Retryable |
|---|---|---|
| `bad_argument` | invalid parameter, unknown country or marketplace | no |
| `not_found` | the page or product does not exist | no |
| `blocked` | marketplace refused the request (403/401/444) | yes, with a proxy |
| `needs_proxy` | known proxy-only source | yes, with a proxy |
| `rate_limited` | 429 | yes, later |
| `network` | DNS/TCP/TLS failure | yes |
| `timeout` | no answer within the budget | yes |
| `no_results` | nothing found across all requested marketplaces | no |
| `no_url` | product URL unknown, run the search first | no |
| `no_state` | page layout changed, parser cannot find its data | no |
| `unavailable` | marketplace has no card parser | no |
| `browser_unavailable` | Playwright not installed | no |
| `server_error` / `http_error` / `bad_response` | upstream misbehaviour | yes |

Partial failure never raises: a per-marketplace summary is in `sources`, and a
single requested marketplace re-raises its own error.

## Content tools

```python
from extsearch import web_search, web_get, video_search, mod_search, freelance_search
```

### `web_search(query, limit=8, fetch_top=0, max_page_chars=2500, region=None, provider="auto")`

DuckDuckGo HTML first, Bing HTML as a fallback. `fetch_top=N` downloads the top
N pages and attaches their text — that is what makes reranking possible.

```python
res = await web_search("python asyncio gather", limit=5, fetch_top=2)
res["results"][0]     # {title, url, domain, snippet, provider, content?, chars?}
res["provider"]       # "duckduckgo" | "bing"
```

`region` is a DuckDuckGo code such as `"ua-uk"` or `"de-de"`. `provider` can be
pinned to `"duckduckgo"` or `"bing"`.

### `web_get(url, max_chars=4000, offset=0)`

```python
page = await web_get("https://example.com/article", max_chars=3000, offset=3000)
page["text"]; page["chars"]; page["has_more"]
```

Strips scripts, navigation, headers and footers; prefers `article`/`main`.

### `video_search(query, limit=8)`

YouTube search without an API key (`ytInitialData`). Items carry `id`, `title`,
`channel`, `url`, `duration`, `duration_seconds`, `published`, `views`.

### `mod_search(query, where="all", limit=10, project_type=None, sort="relevance")`

Modrinth (official API) plus CurseForge (HTML). `project_type` is one of
`mod`, `modpack`, `resourcepack`, `shader`, `datapack`. `sort` is one of
`relevance`, `downloads`, `newest`, `updated`, `follows`. Results are
interleaved and namespaced as `modrinth:…` / `curseforge:…`.

### `freelance_search(query, boards="all", limit=10)`

Boards: `kwork`, `freelancehunt`, `freelancer`, `upwork`, `fiverr`. This is
meta-search through a search engine with `site:` operators (boards render with
JS), and the response says so in the `mode` field.

## Concurrency and rate limits

- One global pause between requests (`EXTSEARCH_MIN_INTERVAL`, default 0.4 s),
  so parallel searches do not turn into a burst.
- Responses are cached in memory for `EXTSEARCH_CACHE_TTL` (default 300 s).
- `timeout=` bounds each marketplace; a slow one is reported as `timeout`
  instead of holding the whole search.

Calling `asyncio.run()` per call is fine for scripts. In a service, keep one
event loop and await the coroutines directly.

## Environment

| Variable | Default | Meaning |
|---|---|---|
| `EXTSEARCH_HOME` | `~/.local/share/extsearch` | cache directory and browser profile |
| `EXTSEARCH_CACHE_DIR` | `$EXTSEARCH_HOME/cache` | response cache |
| `EXTSEARCH_PROFILE_DIR` | `$EXTSEARCH_HOME/browser-profile` | persistent Chromium profile for Rozetka |
| `EXTSEARCH_PROXIES` | — | proxy list, comma/space separated |
| `EXTSEARCH_TIMEOUT` | `25` | request timeout |
| `EXTSEARCH_MIN_INTERVAL` | `0.4` | pause between requests |
| `EXTSEARCH_CACHE_TTL` | `300` | response cache TTL |
| `EXTSEARCH_FX` | built-in table | `{"UAH": 0.024, ...}` — USD per one unit |

`config.to_usd(amount, currency)` converts a single value; unknown currencies
return `None` and simply sort last.
