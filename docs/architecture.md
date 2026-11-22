# Architecture

```text
extsearch/
  search.py          orchestration: resolve → fan-out → merge → rank → details
  olx.py             OLX engine, one codebase for 7 countries
  catalog.py         Ukrainian shops on HTML (prom, foxtrot, rozetka, …)
  catalog_extra.py   Ukrainian shops, newer parsers (bigl, epicentr, makeup, auto)
  marketplaces/
    __init__.py      Marketplace dataclass, registry, resolve(), catalog()
    adapters.py      foreign parsers + the generic CSS adapter
    ua.py ru.py eu.py us.py cis.py asia.py global_.py   wiring per country
  details.py         text-based delivery / payment / review extraction
  net.py             curl_cffi sessions, throttle, cache, retry, proxy rotation
  proxies.py         "TYPE://IP:PORT" parsing and the rotating pool
  web.py             web search (DDG/Bing) and page text extraction
  sources.py         YouTube, Modrinth, CurseForge, freelance meta-search
  media.py           wrappers that merge/namespace the content sources
  mcp_server.py      MCP tools (thin)
  api.py             FastAPI app (thin)
  cli.py             argparse CLI (thin)
```

The last three are thin adapters over the same core: no business logic lives
there, which is why all three behave identically.

## Request flow

```text
search(query, where, …)
   │
   ├── resolve(where) ──► [marketplace names] + excluded + notes
   │
   ├── asyncio.gather( one task per marketplace )
   │      adapter.search(query, limit, page, …)
   │         ├── net.fetch(url, profile=…)  ── throttle ── cache ── proxy ── retry
   │         └── parse HTML/JSON ──► rows {id, title, price, currency, relevance, …}
   │                                └── _finish(): price filter, strict, local sort
   │
   ├── namespace ids: "amazon:B0BZP61K18", attach source + country
   ├── price_usd via config.to_usd()          (approximate FX)
   ├── min_rating filter
   ├── per-source ranking, then merge (interleave or global sort)
   ├── junk_filter
   └── details=N ── select() per item (semaphore = 2)
```

A failing marketplace never aborts the search: the exception is turned into an
entry in `sources` and, if nothing succeeded, re-raised (or wrapped into
`no_results` with `details.sources` when several were requested).

## Adapter contract

A marketplace is a `Marketplace` object:

```python
@dataclass(slots=True)
class Marketplace:
    name: str            # "olx_pl"
    title: str           # "OLX Polska"
    country: str         # "pl"
    currency: str        # "PLN"
    search: SearchFn     # async (query, *, limit, page, …) -> {"items": [...], "total": n}
    get: GetFn | None    # async (id, max_description_chars) -> card dict
    verified: bool       # confirmed by a real run
    needs_proxy: bool    # 403/444 without a regional proxy
    default: bool        # included in "all" and country-wide searches
    note: str
```

Two ways to build `search`:

1. **Raw parser + `rows_search`** — the parser returns a list of row dicts and
   `catalog._finish` applies price bounds, `strict` and local sorting:

   ```python
   rows_search("amazon.de", lambda q, limit, page: amazon_search("de", q, limit, page))
   ```

2. **Custom function** — for sources that need their own pagination or OLX-style
   metadata (`search_offers` returns counts, transport and notes).

A row is a plain dict; only these keys are required:

| Key | Type | Notes |
|---|---|---|
| `id` | int or str | unique inside the marketplace, used in `source:id` |
| `title` | str | also used for relevance and junk filtering |
| `price` | int or None | `None` when the listing has no price |
| `currency` | str | ISO code; drives FX conversion |
| `relevance` | float | 0..1, share of query words found in the title |
| `url` | str | product link |

Optional: `old_price`, `rating`, `reviews_count`, `state`, `city`,
`availability`, `negotiable`, `free`, `business`, and any marketplace-specific
fields (`price_uah`, `mileage_km`, `year`).

`get` returns `title`, `price`, `currency`, `description`, `url`, plus
`delivery[]` / `payment[]` / `reviews` when available.

## Registry and resolution

```python
REGISTRY["olx"] = Marketplace(...)      # one flat dict, name → object
resolve("ua,pl,-allegro")               # (names, excluded, notes)
```

Resolution rules:

| Token | Expands to |
|---|---|
| `all`, `*` | every marketplace with `default=True` |
| country code / alias | that country's default marketplaces |
| `eu`, `cis` | country groups |
| marketplace name | itself, even if `default=False` (e.g. `auto`) |
| `-token`, `!token`, `~token` | removes the expansion from the selection |

Order inside the registry follows `COUNTRY_ORDER`, so merging is deterministic.

## Network layer

`net.fetch(url, params, *, profile, headers, as_json, retries, ttl, proxy, timeout, source)`

| Concern | Implementation |
|---|---|
| TLS fingerprint | `curl_cffi` sessions per profile: `None` (bare libcurl), `chrome`, `safari` |
| Rate | one global lock, `EXTSEARCH_MIN_INTERVAL` (0.4 s) between requests |
| Cache | in-process dict, TTL 300 s, key = profile + URL + sorted params |
| Retries | network errors, `429`, `5xx` with growing backoff |
| Proxies | pool rotation, cooldown 120 s on failure, `safari` retry on `403` in `catalog` |
| Errors | mapped to `ExtSearchError` codes, never raw `curl` exceptions |

Profiles matter because the sites disagree:

| Site | plain libcurl | `impersonate=chrome` |
|---|---|---|
| OLX (all countries) | 200, full results | 403 (browser UA is rejected) |
| Prom.ua | 200 | 200 but an empty placeholder |
| Comfy | 403 | 200 (Safari is the fallback) |
| Rozetka | 403 | product API works, search needs cookies |
| Amazon.de | 200 (sometimes captcha) | 200, full results |
| Ozon / WB / eBay / Walmart | 403 / 444 | 403 / 444 — proxy required |

`olx.py` keeps its own client on purpose: its anti-bot policy is the opposite of
everyone else's and mixing the two clients breaks it.

## ID contract and the URL map

- Search returns `source:id`; `select()` splits on the first colon.
- Marketplaces with numeric ids can rebuild the URL, so an id survives a
  restart (`olx:940356105`).
- Marketplaces with only slugs or alphanumeric ids (Amazon ASINs, AliExpress,
  Yandex Market, Rakuten) keep an in-memory map `(source, id) → url`. After a
  restart, `select()` raises `no_url` and the caller is expected to search again.

## Ranking and merging

1. Each marketplace list is sorted by the requested `sort`.
2. `relevance` and `newest` interleave sources (round-robin) so one large
   catalogue cannot fill the page.
3. `cheapest`, `expensive`, `rating*`, `reviews*`, `discount` produce one global
   order; items without the field go last.
4. `junk_filter` drops accessory titles (`case`, `film`, `cable`, …) from the
   merged list when sorting by price, unless the query itself is about an
   accessory.

`price_usd` is computed with a static FX table (`config.FX`, override with
`EXTSEARCH_FX`) and is used only for ordering. It is never shown as a price.

## Error model

`ExtSearchError(code, message, hint, source, country, url, details)`:

- `message` is English and safe to show to a user.
- `hint` is actionable and language-neutral ("route it through a proxy").
- `retryable` is derived from the code.
- `as_dict()` is the exact payload used by the API (`detail`) and MCP results.

Codes are documented in [library.md#errors](library.md#errors). The rule inside
the code: raise for the caller's mistake and for upstream refusal, return data
for partial failure.

## Details extraction

`details.py` parses delivery, payment and reviews out of free text, because the
shops' markup differs but the vocabulary does not: carriers (Nova Poshta,
Ukrposhta, Meest, Justin, pickup, courier), payment methods, free-shipping
thresholds. Text anchors are Ukrainian and Russian — the data being parsed is
Ukrainian and Russian; the output labels are English.

## Extension points

| Goal | Where |
|---|---|
| new marketplace | `marketplaces/<country>.py`, see [adding-a-marketplace.md](adding-a-marketplace.md) |
| new country | add the code to `COUNTRY_ORDER`, `COUNTRY_TITLES`, `ALIASES`, register sources |
| new content source | `sources.py` + a wrapper in `media.py` |
| new interface | import from `extsearch`, follow `mcp_server.py` as the pattern |
| change request pacing | `EXTSEARCH_MIN_INTERVAL`, or per-call `timeout` |
