# Troubleshooting

## Quick triage

| Symptom | Most likely cause | Fix |
|---|---|---|
| `blocked` on many marketplaces | no proxy, or the proxy is dead | set `EXTSEARCH_PROXIES`, check `/proxies` |
| `blocked` everywhere, even on OLX | the IP is throttled or a proxy is rejected | wait, rotate the pool, test without proxies |
| `no_results` with `where="all"` | everything blocked or the query is too narrow | try one country, drop `strict`, check `sources` |
| `no_results` on a single marketplace | items exist but filters cut them | remove `min_rating`, `price_min`, `strict` |
| `timeout` on one marketplace | slow site or dead proxy | raise `timeout`, lower `per_source_limit` |
| `no_url` from `select()` | the process restarted, the id → URL map is in memory | run the search again, then select |
| `no_state` | the site changed its layout | open an issue with the URL; this is a parser bug |
| `browser_unavailable` | Playwright is not installed | `pip install playwright && playwright install chromium` |
| `needs_session` on Rozetka | Cloudflare challenge not solved | `python tools/warmup_rozetka.py` |
| prices are 100× off | the API returns minor units | divide by 100 in the parser |
| `price: null` everywhere on a shop | price rendered by JS | take the price from the search card or enable the browser path |
| results full of accessories | sorting by price without the junk filter | `junk_filter=true` or set `price_min` |

## Error codes

| Code | Meaning | What to do |
|---|---|---|
| `bad_argument` | wrong parameter, unknown country/marketplace | call `marketplace_catalog` / `sources_catalog` |
| `not_found` | product or page does not exist | check the id |
| `blocked` | 401/403/444 from the marketplace | add a proxy for that region |
| `needs_proxy` | source is known to be proxy-only | same |
| `rate_limited` | 429 | wait, lower concurrency |
| `network` | DNS/TCP/TLS error | retry, check the proxy |
| `timeout` | no answer in time | raise `timeout`, retry |
| `no_results` | nothing across all requested sources | see `sources` and `notes` |
| `no_url` | product URL unknown | search first |
| `no_state` | layout changed | parser needs an update |
| `unavailable` | marketplace has no card parser | search-only source |
| `browser_unavailable` | Playwright missing | install it |
| `server_error`, `http_error`, `bad_response` | upstream misbehaviour | retry later |

## Debug recipes

### One marketplace, verbose

```bash
python tools/check_sources.py ozon
```

It prints either the first rows or the error code, without running the whole
search. Fastest way to see what broke.

### Raw response, no parsing

```python
import asyncio
from extsearch.net import fetch_text

html = asyncio.run(fetch_text(
    "https://www.ozon.ru/search/?text=iphone",
    profile="chrome", source="debug",
))
print(len(html))
open("page.html", "w").write(html)
```

Compare profiles to spot fingerprint blocking:

```python
plain = asyncio.run(fetch_text(url))                     # bare libcurl
chrome = asyncio.run(fetch_text(url, profile="chrome"))
print(len(plain), len(chrome))
```

A site that returns a few hundred bytes for one profile and hundreds of
kilobytes for the other is fingerprint-checking; a site that returns the same
size but no prices renders prices with JS.

### Is the proxy actually used?

```python
import asyncio
from extsearch.net import fetch_text

async def main():
    print(await fetch_text("https://api.ipify.org", source="debug"))

asyncio.run(main())          # run again after set_proxies() to compare the IP
```

If the IP does not change, the proxy string was rejected or the proxy itself
ignores the `https` CONNECT tunnel.

### Proxy checklist

```python
from extsearch import parse_proxies, get_pool, set_proxies

set_proxies("socks5://1.2.3.4:1080")
get_pool().stats()
```

- Format must be `TYPE://IP:PORT`; `TYPE` is one of `http`, `https`, `socks4`,
  `socks4a`, `socks5`, `socks5h`.
- `http://IP:PORT` means "HTTP proxy", not "proxy for http URLs" — it is used
  for both schemes.
- A proxy that returns `407` or a connection error is marked bad for 120 s.
- Credentials with special characters must be URL-encoded
  (`p@ss` → `p%40ss`).

### MCP client shows no tools

1. The command path must be absolute — GUI clients often have a minimal `PATH`.
2. Check the interpreter can import the package:
   `/path/to/.venv/bin/python -c "import extsearch; print(extsearch.__version__)"`.
3. Check the server answers:
   ```bash
   printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"t","version":"1"}}}' \
     | /path/to/extsearch-mcp 2>/dev/null
   ```
4. Look at the client's MCP log; most of them log subprocess stderr.

### API returns 502

`502` covers `blocked`, `network`, `server_error` and friends — read
`detail.error`. A search across many marketplaces normally returns `200` with
per-source errors inside the body; a `502` means **every** requested source
failed, or exactly one source was requested and it failed.

### Everything is slow

- `where="all"` is 37 marketplaces; use one country for interactive work.
- `details=N` costs N extra requests; keep it at 0–3.
- One global pause of 0.4 s applies per process (`EXTSEARCH_MIN_INTERVAL`).
  Lowering it makes `429` more likely, not less.

## Per-marketplace quirks

| Marketplace | Quirk |
|---|---|
| OLX (all countries) | rejects browser User-Agents; the client must stay on the plain profile. `free_only` works only on `olx.ua`. |
| Rozetka | search needs a warmed Chromium profile; product API works directly; prices may be missing in search rows. |
| Prom.ua | `impersonate=chrome` returns a 200 with an empty placeholder — use plain. |
| Comfy | the opposite: plain gets 403, chrome 403, Safari passes. |
| Allo | renders the price with JS on the card page; the search row price is authoritative. |
| Makeup / Kasta | different verticals: an electronics query returns cosmetics or clothes, which is the site's own search result. |
| AUTO.RIA | price is in USD with a UAH equivalent in `price_uah`; `default=false`, excluded from `all`. |
| Yandex Market | HTML result page, prices in RUB; search works without a proxy. |
| Wildberries | JSON API; prices are kopecks (`/100`); 403 from outside the region. |
| Ozon | composer API; `widgetStates` layout changes; proxy required. |
| Amazon | two headings per card (brand and name) — the longest is the product name; prices come with the symbol first (`€47.48`). |
| AliExpress | price and currency live in the link parameters (`pdp_npi`), not in text. |
| Rakuten | prices in JPY from `data-track-price`. |
| eBay / Walmart / Allegro / Flipkart / Shopee | adapters exist, markup unverified, regional proxy required. |

## FAQ

**Why not `requests`/`httpx`?**
Most of these sites check the TLS fingerprint (JA3/JA4). `requests` receives
`403` even with identical headers. `curl_cffi` performs the handshake like
Chrome or Safari.

**Why does an id stop working after a restart?**
Marketplaces without numeric ids (Amazon, eBay, AliExpress, Yandex Market,
Rakuten) are mapped `id → url` in memory. Search again to repopulate the map.

**Why does `min_rating` empty the results?**
Only some sources expose ratings in search results (comfy, kasta, brain, ctrs,
makeup, Amazon). Others are dropped entirely, so a high threshold can leave one
source or none.

**Why is the price different from the website?**
Prices are integers in the marketplace currency; cents are rounded. Delivery
costs are not included. For cross-country sorting an approximate FX table is
used (`price_usd`), it is never shown as a price.

**Can I use several workers?**
The cache, throttle and proxy pool are per process. One worker is recommended;
several workers multiply requests to the marketplaces.

**Something changed and a parser broke — what now?**
Run `tools/check_sources.py`, open the failing URL with the raw-response recipe,
compare the markup with the parser, patch the selectors, run the smoke test.
