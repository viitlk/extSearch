# HTTP API

```bash
extsearch-api                          # 127.0.0.1:8000
extsearch-api --host 0.0.0.0 --port 8000
uvicorn extsearch.api:app --reload     # development
```

No authentication. Do not expose it to the internet as-is: put it behind a
token check or a private network (the built-in scrapers would become a public
proxy for the marketplaces).

Interactive docs: `GET /docs`. Raw schema: `GET /openapi.json`.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | status, version, proxy count |
| GET | `/sources` | countries, marketplaces, flags |
| GET | `/search` | search with query-string parameters |
| POST | `/search` | search with a JSON body |
| GET | `/item/{item_id}` | product card |
| GET | `/proxies` | current pool state |
| POST | `/proxies` | replace the pool |
| GET | `/web/search` | web search |
| GET | `/web/get` | page text |
| GET | `/video` | YouTube |
| GET | `/mods` | Minecraft mods |
| GET | `/freelance` | freelance boards |

## `GET /health`

```bash
curl 127.0.0.1:8000/health
```

```json
{"status": "ok", "version": "2.0.0", "proxies": 0}
```

## `GET /sources`

```bash
curl 127.0.0.1:8000/sources
curl 127.0.0.1:8000/sources | jq '.countries | keys'
```

Returns the same payload as `sources_catalog()`:

```json
{
  "countries": {
    "ua": {"title": "Ukraine", "marketplaces": ["olx", "prom", "..."]},
    "ru": {"title": "Russia", "marketplaces": ["ozon", "wildberries", "yandex_market"]}
  },
  "marketplaces": [
    {"name": "ozon", "title": "Ozon", "country": "ru", "currency": "RUB",
     "verified": false, "needs_proxy": true, "default": true, "can_get": false,
     "note": "composer API; widgetStates layout changes, proxy required"}
  ],
  "total": 38
}
```

## `GET /search`

Fast path for curl and browser checks.

```bash
curl "127.0.0.1:8000/search?q=fifine%20k669&where=ua&limit=3"
curl "127.0.0.1:8000/search?q=iphone%2015&where=ua,pl&sort=cheapest&min_rating=4"
```

| Query parameter | Default | Notes |
|---|---|---|
| `q` | required | search phrase |
| `where` | `all` | countries/marketplaces, `-` excludes |
| `limit` | `10` | 1..50 |
| `page` | `1` | |
| `sort` | `relevance` | see [library.md](library.md#search) |
| `price_min`, `price_max` | — | floats |
| `min_rating` | — | float |
| `strict` | `false` | title must contain a query word |

Fixed on this route: `junk_filter` auto, `details=0`, `include_urls=true`.

## `POST /search`

Everything the library supports.

```bash
curl -X POST 127.0.0.1:8000/search \
  -H 'content-type: application/json' \
  -d '{
    "query": "rtx 4060",
    "where": "ua,pl",
    "limit": 5,
    "sort": "cheapest",
    "price_min": 8000,
    "details": 2,
    "per_source_limit": 10,
    "proxies": ["socks5://1.2.3.4:1080"]
  }'
```

Body fields: `query`, `where`, `limit`, `page`, `price_min`, `price_max`,
`min_rating`, `sort`, `strict`, `junk_filter`, `include_urls`, `details`,
`per_source_limit`, `free_only`, `proxies`.

`proxies` in the body **replaces** the process pool, it is not a per-request
override. To scope it, send the pool you want on the first call and reset it
with `{"proxies": null}`.

## `GET /item/{item_id}`

Colons and slashes are allowed in `item_id` (`:path` route).

```bash
curl "127.0.0.1:8000/item/olx:940356105"
curl "127.0.0.1:8000/item/amazon:B0BZP61K18?max_description_chars=300"
```

```json
{
  "source": "olx",
  "country": "ua",
  "id": 940356105,
  "title": "iPhone 15 128GB Black",
  "price": 18900,
  "currency": "UAH",
  "description": "...",
  "url": "https://www.olx.ua/d/uk/obyavlenie/...",
  "delivery": [
    {"method": "OLX Delivery (Nova Poshta / Ukrposhta)", "price": null,
     "note": "carrier rate, payment protected by OLX", "source": "olx_api"}
  ],
  "payment": [{"method": "cash on meetup", "source": "olx_api"}]
}
```

## Proxies

```bash
curl -X POST 127.0.0.1:8000/proxies \
  -H 'content-type: application/json' \
  -d '{"proxies": ["socks5://1.2.3.4:1080", "http://user:pass@5.6.7.8:3128"]}'

curl -X POST 127.0.0.1:8000/proxies -H 'content-type: application/json' -d '{"proxies": null}'
curl 127.0.0.1:8000/proxies
```

```json
{
  "count": 2,
  "proxies": [
    {"proxy": "socks5://1.2.3.4:1080", "errors": 0, "cooling_down": false},
    {"proxy": "http://***@5.6.7.8:3128", "errors": 1, "cooling_down": true}
  ]
}
```

Credentials are always masked. An invalid string returns `400` with
`{"error": "bad_argument", ...}`.

## Content routes

| Route | Parameters |
|---|---|
| `/web/search` | `q`, `limit` (1..30), `fetch_top` (0..10) |
| `/web/get` | `url`, `max_chars` (1..20000), `offset` |
| `/video` | `q`, `limit` (1..30) |
| `/mods` | `q`, `where` = all/modrinth/curseforge, `limit` |
| `/freelance` | `q`, `boards` = all or comma list, `limit` |

## Status codes and errors

| HTTP | Error codes |
|---|---|
| 400 | `bad_argument` |
| 404 | `not_found`, `no_results` |
| 409 | `no_url` |
| 429 | `rate_limited` |
| 500 | unexpected exception |
| 501 | `unavailable`, `browser_unavailable` |
| 502 | `blocked`, `needs_proxy`, `network`, `server_error`, `http_error`, `bad_response` |
| 504 | `timeout` |

Error body (`detail` of `HTTPException`):

```json
{
  "detail": {
    "error": "blocked",
    "message": "www.ozon.ru rejected the request (HTTP 403)",
    "hint": "this marketplace blocks requests from unsupported regions; route it through a proxy",
    "source": "ozon",
    "country": "ru",
    "retryable": true
  }
}
```

## Python client

```python
import requests

BASE = "http://127.0.0.1:8000"

r = requests.post(f"{BASE}/search", json={
    "query": "fifine k669", "where": "ua", "limit": 5, "sort": "cheapest",
}, timeout=120)

if r.status_code != 200:
    err = r.json()["detail"]
    print(err["error"], err["message"], err.get("hint"))
else:
    data = r.json()
    for item in data["items"]:
        print(item["price"], item["currency"], item["title"])

# partial failures are inside the body, not a non-200 status
for name, info in data["sources"].items():
    if info.get("error"):
        print(f'{name}: {info["error"]} — {info.get("hint", "")}')
```

## Deployment notes

```bash
uvicorn extsearch.api:app --host 0.0.0.0 --port 8000 --workers 1
```

One worker is enough and recommended: the cache, throttle and proxy pool are
per process, so several workers multiply traffic to the marketplaces and lose
cache hits. Put a reverse proxy in front, add a token header check, and keep the
instance private.
