# extsearch documentation

Start here, then jump to the page you need.

| Page | For |
|---|---|
| [Library](library.md) | Python API: search, product cards, catalog, proxies, errors |
| [HTTP API](api.md) | endpoints, request/response bodies, status codes |
| [MCP server](mcp.md) | tools, client configuration, prompt patterns |
| [Architecture](architecture.md) | request flow, adapter contract, network layer, ranking |
| [Adding a marketplace](adding-a-marketplace.md) | step-by-step with two complete examples |
| [Troubleshooting](troubleshooting.md) | symptom → cause → fix, per-marketplace quirks |

## The 30-second tour

```bash
pip install -e ".[all]"

extsearch search "fifine k669" -w ua -n 3      # CLI
extsearch-mcp                                  # MCP server (stdio)
extsearch-api --port 8000                      # HTTP API + /docs
```

```python
import asyncio
from extsearch import search

res = asyncio.run(search("fifine k669", where="ua", limit=3, sort="cheapest"))
print([(i["source"], i["price"], i["currency"]) for i in res["items"]])
```

```json
// POST /search
{"query": "fifine k669", "where": "ua", "limit": 3, "sort": "cheapest"}
```

The same call, the same result shape. Pick whichever interface fits your code.

## Concepts

| Term | Meaning |
|---|---|
| **marketplace** | one site, identified by `name` (`olx`, `ozon`, `amazon_de`) |
| **country** | ISO code (`ua`, `ru`, `us`) or a group token (`eu`, `cis`, `global`) |
| **item id** | namespaced id from a search: `olx:940356105`, `amazon:B0BZP61K18` |
| **source** | the marketplace an item came from; also the first half of the id |
| **per_source_limit** | how many items to request from each marketplace |
| **price_usd** | approximate conversion used only to compare prices across currencies |
| **verified** | the parser was confirmed by a real run from a normal connection |
| **needs_proxy** | the marketplace refuses requests from unsupported regions |
| **default** | included in `all` and in country-wide searches |

## Result shape

Both search and content tools return plain dictionaries, so they serialise to
JSON unchanged.

```json
{
  "query": "iphone 15",
  "where": ["olx", "olx_pl", "prom"],
  "excluded": [],
  "countries": ["pl", "ua"],
  "returned": 2,
  "per_source_limit": 10,
  "mixed": true,
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

Optional item fields, when the marketplace provides them: `old_price`,
`rating`, `reviews_count`, `state` (new/used), `city`, `availability`,
`negotiable`, `free`, `price_uah` (AUTO.RIA), `mileage_km`, `year`.

## Errors

One shape everywhere — library exception, HTTP detail, MCP tool result:

```json
{
  "error": "blocked",
  "message": "www.ozon.ru rejected the request (HTTP 403)",
  "hint": "this marketplace blocks requests from unsupported regions; route it through a proxy",
  "source": "ozon",
  "country": "ru",
  "retryable": true
}
```

Full code list: [library.md#errors](library.md#errors).
