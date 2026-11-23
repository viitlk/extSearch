# extsearch

[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![PyPI](https://img.shields.io/pypi/v/extsearch)](https://pypi.org/project/extsearch/)
[![MCP](https://img.shields.io/badge/MCP-server-blueviolet)](https://modelcontextprotocol.io/)

Product search across marketplaces of many countries. 39 marketplaces in 17
countries, one result format, one error format, no API keys. One engine, three
interfaces:

| Interface | Command | Use it from |
|---|---|---|
| Python library | `import extsearch` | scripts, bots, your own services |
| MCP server | `extsearch-mcp` | LLM agents (OpenCode, Claude, Cline, any MCP client) |
| HTTP API | `extsearch-api` | anything that speaks HTTP, plus a `/docs` UI |

**Documentation:** [docs/](docs/index.md) — [library](docs/library.md) ·
[HTTP API](docs/api.md) · [MCP server](docs/mcp.md) ·
[architecture](docs/architecture.md) ·
[adding a marketplace](docs/adding-a-marketplace.md) ·
[troubleshooting](docs/troubleshooting.md)

```python
import asyncio
from extsearch import search, select

async def main():
    res = await search("fifine k669", where="ua", limit=5, sort="cheapest")
    for item in res["items"]:
        print(item["price"], item["currency"], item["source"], "—", item["title"])

    card = await select(res["items"][0]["id"])   # "olx:940356105"
    print(card["title"], card.get("delivery"))

asyncio.run(main())
```

> [!NOTE]
> Many marketplaces check the TLS fingerprint, so the transport is `curl_cffi`, not
> `requests`/`httpx`. Plain HTTP clients receive `403` even with correct headers.

---

## Why another scraper

Most "marketplace API" projects are one site, one country and one output shape.
This one is built around a small adapter contract:

```text
search(query, where) ──┬── olx (JSON API) ─────┐
                       ├── shops (HTML) ────────┤── merge ── rank ── items[]
                       └── foreign (HTML/JSON) ─┘
```

Each adapter returns the same rows (`id, title, price, currency, relevance, …`),
so filters, sorting, currency conversion and the id → product-card map work
identically for a Ukrainian classified site and a Japanese marketplace.

Adding a marketplace is one function plus one registry line — see
[`extsearch/marketplaces/`](extsearch/marketplaces/).

---

## Countries and marketplaces

`where=` accepts countries, marketplaces, or both: `"ua"`, `"ua,ru"`,
`"olx,ozon"`, `"all"`, `"all,-ru"`, `"eu"`, `"cis"`.

| Code | Country | Marketplaces |
|---|---|---|
| `ua` | Ukraine | olx, prom, foxtrot, rozetka, comfy, allo, moyo, kasta, brain, ctrs, bigl, epicentr, makeup, shafa, auto¹ |
| `pl` | Poland | olx_pl, allegro² |
| `ro` / `bg` / `pt` | Romania / Bulgaria / Portugal | olx_ro, olx_bg, olx_pt |
| `kz` / `uz` | Kazakhstan / Uzbekistan | olx_kz, kaspi², olx_uz |
| `ru` | Russia | yandex_market, wildberries², ozon² |
| `us` | USA | amazon², ebay², walmart² |
| `gb` / `de` / `fr` / `it` / `es` | United Kingdom / Germany / France / Italy / Spain | amazon_gb², amazon_de, amazon_fr², amazon_it², amazon_es² |
| `jp` / `in` / `sg` | Japan / India / Singapore | rakuten, flipkart², shopee_sg² |
| `global` | worldwide | aliexpress, alibaba² |

Group tokens: `eu` (pl, ro, bg, pt, de, gb, fr, it, es), `cis` (kz, uz).

¹ opt-in: excluded from `all` because it is a different vertical (cars).
² needs a proxy in a supported region — the marketplace answers `403`/`444` to
  requests from the "wrong" country. The adapter is written; the status is
  reported as `verified: false` until someone confirms it from a matching IP.

### Verification status

`sources_catalog()` (MCP: `marketplace_catalog`, API: `GET /sources`) reports
per marketplace:

| Field | Meaning |
|---|---|
| `verified` | markup confirmed by a real run, parser returns items |
| `needs_proxy` | answers `403`/`429`/`444` without a regional proxy |
| `default` | included in `all` and in country-wide searches |
| `can_get` | product cards (`select`) supported |

26 of 39 adapters are verified from a normal connection: OLX (ua, pl, ro, bg, pt,
kz, uz), prom, foxtrot, comfy, allo, moyo, kasta, brain, ctrs, bigl, epicentr,
makeup, shafa, auto.ria, yandex_market, rakuten, aliexpress, amazon.com, plus
Rozetka's product API.

The rest answer `403`/`444` or an AWS WAF challenge without a regional proxy and are
reported as `blocked`, not as an empty result set:

- Rozetka **search** needs a warmed browser profile: `python tools/warmup_rozetka.py`
- amazon.co.uk / .fr / .it / .es serve a WAF challenge; amazon.de is rate limited
  after a burst of requests

---

## Install

```bash
pip install extsearch               # library only
pip install "extsearch[mcp]"        # + MCP server (extsearch-mcp)
pip install "extsearch[api]"        # + HTTP API (extsearch-api)
pip install "extsearch[all]"        # everything
```

No access to PyPI? Every [release](https://github.com/viitlk/extSearch/releases) carries
the wheel; install it directly:

```bash
pip install ./extsearch-2.0.2-py3-none-any.whl
```

From source, for development:

```bash
git clone https://github.com/viitlk/extSearch
cd extSearch
python -m venv .venv && source .venv/bin/activate
pip install -e ".[all]"
```

Optional browser engine (Rozetka search only):

```bash
pip install playwright && python -m playwright install chromium
```

Requirements: Python 3.10+, `curl_cffi`, `beautifulsoup4`. Linux and macOS are
the tested platforms; Windows should work but is not covered by CI.

---

## Library

### Search

```python
import asyncio
from extsearch import search

async def main():
    res = await search(
        "iphone 15",
        where="ua,pl",          # countries and/or marketplace names
        limit=10,               # items in the response, 1..50
        page=1,
        price_min=500,
        price_max=1500,
        sort="cheapest",        # relevance|newest|cheapest|expensive|rating|...
        min_rating=4.0,         # drops items without a rating
        strict=True,            # title must contain a query word
        details=2,              # delivery/payment for the first 2 items
    )
    for item in res["items"]:
        print(item["id"], item["price"], item["currency"], item["title"])
    print(res["sources"])       # per marketplace: returned / total / error / hint

asyncio.run(main())
```

<details>
<summary>Response shape (abridged)</summary>

```json
{
  "query": "iphone 15",
  "where": ["olx", "prom", "olx_pl"],
  "countries": ["pl", "ua"],
  "returned": 2,
  "sort": "cheapest",
  "sources": {
    "olx": {"returned": 10, "total": 1843},
    "olx_pl": {"returned": 10, "total": 214},
    "prom": {"returned": 0, "error": "blocked", "hint": "route it through a proxy ..."}
  },
  "items": [
    {
      "id": "olx:940356105",
      "source": "olx",
      "country": "ua",
      "title": "iPhone 15 128GB Black",
      "price": 18900,
      "currency": "UAH",
      "price_usd": 453.6,
      "relevance": 1.0,
      "url": "https://www.olx.ua/d/uk/obyavlenie/..."
    }
  ],
  "notes": ["mixed currencies (PLN, UAH); prices are compared via the approximate price_usd"]
}
```

</details>

### Product card

```python
card = await select("amazon:B0BZP61K18", max_description_chars=400)
card["title"]; card["price"]; card.get("delivery"); card.get("reviews")
```

Cards may include `delivery[]`, `payment[]`, `reviews`, `free_shipping_from`.
A `null` delivery price means the shop does not publish a fixed price and
charges the carrier rate — it is not "unknown".

### Catalog and resolution

```python
from extsearch import resolve, sources_catalog

resolve("ua")               # ['allo', 'bigl', 'brain', ...]
resolve("all,-ru,-auto")    # everything except Russia and AUTO.RIA
resolve("eu")

sources_catalog()["countries"]        # code -> {title, marketplaces}
sources_catalog()["marketplaces"][0]  # name, currency, verified, needs_proxy, ...
```

### Errors

Every failure is one shape, language-neutral, with a machine-readable code:

```python
from extsearch import ExtSearchError

try:
    await search("iphone", where="ozon")
except ExtSearchError as exc:
    exc.code          # "blocked"
    exc.message       # "www.ozon.ru rejected the request (HTTP 403)"
    exc.hint          # 'route it through a proxy (proxies=["TYPE://IP:PORT"])'
    exc.retryable     # True
    exc.as_dict()     # JSON-ready payload
```

Codes: `bad_argument`, `not_found`, `blocked`, `needs_proxy`, `rate_limited`,
`network`, `timeout`, `no_results`, `no_state`, `no_url`, `unavailable`,
`browser_unavailable`, `server_error`, `http_error`, `bad_response`.

A partial failure never kills a search: the response lists per-marketplace
status in `sources` and explanations in `notes`. If exactly one marketplace was
requested, its own error is raised instead of a generic `no_results`.

---

## Proxies

Format `TYPE://IP:PORT`; `TYPE` is `http`, `https`, `socks4` or `socks5`.
Credentials are allowed: `socks5://user:pass@1.2.3.4:1080`.

```bash
# environment
export EXTSEARCH_PROXIES="socks5://1.2.3.4:1080,http://5.6.7.8:3128"

# CLI
extsearch search "iphone" -w us --proxy socks5://1.2.3.4:1080

# library
from extsearch import set_proxies
set_proxies(["socks5://1.2.3.4:1080"])

# per call
await search("iphone", where="us", proxies=["socks5://1.2.3.4:1080"])
```

Proxies rotate round-robin. One that produces a network error or `403`/`429` is
skipped for 120 seconds and the request retries on the next proxy. Credentials
are masked in logs and API responses.

---

## CLI

```bash
extsearch search "fifine k669" -w ua -n 5
extsearch search "iphone 15" -w all --sort cheapest --proxy socks5://1.2.3.4:1080
extsearch search "rtx 4060" -w ru,ua --min-price 8000 --details 2
extsearch select amazon:B0BZP61K18
extsearch sources --country ru
extsearch web "python asyncio gather" --fetch-top 2
extsearch video "fifine k669 review"
extsearch freelance "telegram bot aiogram"
extsearch mcp                      # MCP server over stdio
extsearch api --port 8000          # HTTP API
```

---

## HTTP API

```bash
extsearch-api --host 127.0.0.1 --port 8000
python -m extsearch.api --port 8000      # same thing, module entry point
```

OpenAPI docs and a try-it form: <http://127.0.0.1:8000/docs>

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | status, version, proxy count |
| GET | `/sources` | countries, marketplaces, flags |
| GET | `/search?q=...&where=ua&limit=5` | quick search |
| POST | `/search` | full search (`SearchBody` schema) |
| GET | `/item/{id}` | product card |
| GET / POST | `/proxies` | inspect / replace the pool |
| GET | `/web/search`, `/web/get` | web search, page text |
| GET | `/video`, `/mods`, `/freelance` | content tools |

```bash
curl "127.0.0.1:8000/search?q=fifine%20k669&where=ua&limit=3"
curl "127.0.0.1:8000/item/olx:940356105"

curl -X POST 127.0.0.1:8000/search \
  -H 'content-type: application/json' \
  -d '{"query": "rtx 4060", "where": "ua,pl", "sort": "cheapest", "limit": 5}'
```

Status codes map from error codes: `400` bad_argument, `404` not_found /
no_results, `409` no_url, `429` rate_limited, `501` unavailable, `502` blocked,
`504` timeout. Error bodies always carry `{error, message, hint?, retryable}`.

---

## MCP server

```bash
extsearch-mcp                                        # stdio (default)
python -m extsearch.mcp_server                       # same, module entry point
EXTSEARCH_TRANSPORT=http extsearch-mcp --port 8765   # streamable HTTP
```

Client configuration (OpenCode example; any MCP client works the same):

```json
{
  "mcp": {
    "extsearch": {
      "type": "local",
      "command": ["/path/to/.venv/bin/extsearch-mcp"],
      "enabled": true
    }
  }
}
```

Tools:

| Tool | What it does |
|---|---|
| `marketplace_search` | search across countries and marketplaces |
| `marketplace_select` | product card by `source:id` |
| `marketplace_catalog` | countries, marketplaces, verification flags |
| `configure_proxies` | set or clear the proxy pool |
| `web_search` / `web_get` | web search, clean page text |
| `video_search` | YouTube |
| `mod_search` | Modrinth + CurseForge |
| `freelance_search` | Kwork, Freelancehunt, Freelancer, Upwork, Fiverr |

Example prompts that work well:

```text
find fifine k669 on olx and prom under 2000 UAH, include delivery options
compare iphone 15 across ua, pl and de, cheapest first
show the marketplace catalog, then search ozon through socks5://1.2.3.4:1080
```

---

## Tests

```bash
python tests/smoke_test.py                          # unit + live network
EXTSEARCH_OFFLINE=1 python tests/smoke_test.py      # unit only
pytest tests/ -q
python tools/check_sources.py                       # per-marketplace health check
python tools/test.py                                # one-screen live test (for posts/issues)
```

The live part deliberately hits real marketplaces — that is the only way to
notice a layout change before users do.

---

## Configuration

Everything is environment-driven (see [`.env.example`](.env.example)):

| Variable | Default | Meaning |
|---|---|---|
| `EXTSEARCH_HOME` | `~/.local/share/extsearch` | cache and browser profile |
| `EXTSEARCH_PROXIES` | — | proxy list |
| `EXTSEARCH_TIMEOUT` | `25` | request timeout, seconds |
| `EXTSEARCH_MIN_INTERVAL` | `0.4` | pause between requests per process |
| `EXTSEARCH_CACHE_TTL` | `300` | response cache, seconds |
| `EXTSEARCH_FX` | built-in table | currency rates (USD per unit, JSON) |
| `EXTSEARCH_TRANSPORT` / `_HOST` / `_PORT` | `stdio` / `127.0.0.1` / `8765` | MCP transport |

---

## Layout

```text
extsearch/
  search.py          multi-marketplace search, ranking, FX, details
  olx.py             OLX engine — one codebase for 7 countries
  catalog.py         UA shops: prom, foxtrot, rozetka, comfy, allo, moyo, kasta, brain, citrus
  catalog_extra.py   UA shops: bigl, epicentr, makeup, shafa, auto.ria
  marketplaces/      registry + adapters, split by country
  details.py         delivery / payment / reviews extraction
  net.py             curl_cffi sessions, throttling, cache, proxy rotation
  proxies.py         "TYPE://IP:PORT" parsing, round-robin pool
  web.py             web search and text extraction
  sources.py         YouTube, Modrinth, CurseForge, freelance boards
  media.py           wrappers for the content sources
  mcp_server.py      MCP tools
  api.py             FastAPI app
  cli.py             argparse CLI
docs/
  index.md           documentation map and concepts
  library.md         Python API reference
  api.md             HTTP endpoints and payloads
  mcp.md             MCP tools and client configuration
  architecture.md    request flow, adapter contract, network layer
  adding-a-marketplace.md   step-by-step guide with a real example
  troubleshooting.md symptom → cause → fix
```

---

## Limitations

- Scrapers break on redesigns. `sources` in every response and
  `tools/check_sources.py` tell you which one broke.
- Prices are integers in the marketplace currency; Amazon/EUR cents are rounded.
  Cross-country comparison uses an approximate FX table, not live rates.
- For marketplaces without numeric product ids the id → URL map is in memory,
  so `select()` works for items found in the same process. Re-run the search if
  the process restarted.
- Fourteen marketplaces answer `403`/`444` from the "wrong" country and are
  marked `verified: false` until confirmed with a regional proxy.
- Product cards need a separate request per item: `details=N` costs N requests.

## Legal

This is a personal research tool. It does not bypass authentication, it reads
what a browser would read. Respect the terms of service of each marketplace and
your local law: keep request rates low, do not republish scraped content, use
only proxies you are allowed to use. Marketplace data belongs to the
marketplaces.

## Contributing

Adding a marketplace takes one file plus one registry line:

1. Write a parser that returns rows `{id, title, price, currency, relevance, url}`.
2. Register it in `extsearch/marketplaces/<country>.py` with `verified`/`needs_proxy`.
3. Add the name to the README table and run `python tools/check_sources.py my_source`.

Pull requests with a real example response (minus personal data) are welcome.

### Branching and releases

| Branch | Purpose |
|---|---|
| `main` | released code; tag and release are created here |
| `dev` | integration: small commits, several fixes can pile up before a release |

A release is a decision, not a side effect of pushing a commit:

1. push fixes to `dev`
2. bump `__version__` in `extsearch/config.py` (the only place the number lives)
3. `git checkout main && git merge --no-ff dev && git push`
4. create a GitHub Release with tag `vX.Y.Z` — the workflow builds and publishes to PyPI
5. attach the wheel and sdist to that release

Tagging alone no longer publishes: the workflow listens for a published release.

## Getting help

A marketplace changes its markup every few months, so "the parser broke" is a normal
event, not an exception.

| Situation | Where |
|---|---|
| a parser returns nothing or wrong prices | open an issue, the template asks for the details |
| usage question | [docs/](docs/index.md) first, then issues |
| a site answers `403` | that is regional blocking, see [proxies](docs/library.md#proxies) — a proxy is needed, not a patch |
| you want a new marketplace | [docs/adding-a-marketplace.md](docs/adding-a-marketplace.md), pull requests welcome |

Include this output when reporting a broken parser — it shows what works and what does not,
without guessing:

```bash
.venv/bin/python tools/test.py
```

## Support the project

Optional. Bug reports and pull requests help more than money.

**USDT (TRC20)** — cheapest network, accepted by every Ukrainian exchange

![USDT TRC20 QR](docs/support/qr-usdt-trc20.png)

```text
TDkxURDpAuRAvTy78Tv23Z6DPCx1J1SRCd
```

**Bitcoin**

![Bitcoin QR](docs/support/qr-btc.png)

```text
bc1qj3d8qhnj377elgw5tk0z54zyn6xf7rnnvdx9y6
```

**Ethereum**

![Ethereum QR](docs/support/qr-eth.png)

```text
0x946E80Be2330473ae5f2D09b3e861642D0487040
```

> [!WARNING]
> The network matters. USDT must be sent on **TRC20 (TRON)**. Sending the same
> token on BEP20 or ERC20 to this address loses the money — transfers are
> irreversible.

## License

MIT for the code, see [LICENSE](LICENSE).
