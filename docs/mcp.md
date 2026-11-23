# MCP server

The MCP server exposes the same engine to LLM agents. Tools return plain
dictionaries; a failure is returned as data, never as an exception, so the agent
can read the error code and decide what to do next.

## Run

```bash
extsearch-mcp                                         # stdio (default)
EXTSEARCH_TRANSPORT=http extsearch-mcp --port 8765    # streamable HTTP
EXTSEARCH_TRANSPORT=http EXTSEARCH_HOST=0.0.0.0 extsearch-mcp --port 8765
```

| Variable | Default | Meaning |
|---|---|---|
| `EXTSEARCH_TRANSPORT` | `stdio` | `stdio`, `http` (= streamable HTTP) or `sse` |
| `EXTSEARCH_HOST` | `127.0.0.1` | bind address for HTTP transport |
| `EXTSEARCH_PORT` | `8765` | port for HTTP transport |
| `EXTSEARCH_PROXIES` | — | proxy pool at startup |

With the HTTP transport the endpoint is the MCP default path (`/mcp`) — check
the startup log if your client needs the exact URL.

## Client configuration

### OpenCode

`~/.config/opencode/opencode.json`:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "extsearch": {
      "type": "local",
      "command": ["/path/to/.venv/bin/extsearch-mcp"],
      "enabled": true
    }
  }
}
```

Remote variant, when the server runs with `EXTSEARCH_TRANSPORT=http`:

```json
{
  "mcp": {
    "extsearch": {
      "type": "remote",
      "url": "http://127.0.0.1:8765/mcp",
      "enabled": true
    }
  }
}
```

### Claude Desktop

`claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "extsearch": {
      "command": "/path/to/.venv/bin/extsearch-mcp",
      "env": {
        "EXTSEARCH_PROXIES": "socks5://1.2.3.4:1080"
      }
    }
  }
}
```

### Cline / other clients

Any client that accepts a command plus arguments works: command
`/path/to/.venv/bin/extsearch-mcp`, no arguments, optional environment above.
For clients that only speak HTTP, start the server with the HTTP transport and
point the client at the URL.

## Tools

| Tool | Kind | Purpose |
|---|---|---|
| `marketplace_search` | async | search across countries and marketplaces |
| `marketplace_compare` | async | same product across sources, best price first |
| `marketplace_select` | async | product card by `source:id` |
| `marketplace_catalog` | sync | countries, marketplaces, flags |
| `configure_proxies` | sync | set or clear the proxy pool |
| `web_search` | async | DuckDuckGo/Bing, optional page text |
| `web_get` | async | clean page text |
| `video_search` | async | YouTube |
| `mod_search` | async | Modrinth + CurseForge |
| `freelance_search` | async | Kwork, Freelancehunt, Freelancer, Upwork, Fiverr |

### `marketplace_search`

| Argument | Default | Notes |
|---|---|---|
| `query` | required | search phrase |
| `where` | `all` | `ua`, `ua,ru`, `olx,ozon`, `all,-ru`, `eu`, `cis` |
| `limit` | `10` | 1..50 |
| `page` | `1` | |
| `price_min`, `price_max` | — | numbers |
| `min_rating` | — | drops items without a rating |
| `sort` | `relevance` | `cheapest`, `expensive`, `rating`, `reviews`, `discount`, … |
| `strict` | `false` | title must contain a query word |
| `junk_filter` | `null` | `null` = auto for price sorts |
| `include_urls` | `true` | product URLs in items |
| `details` | `0` | fetch delivery/payment for the first N items |
| `per_source_limit` | = `limit` | items per marketplace |
| `free_only` | `false` | OLX give-away category (UA) |
| `proxies` | — | `["socks5://IP:PORT"]`, replaces the pool |

### `marketplace_select`

| Argument | Default | Notes |
|---|---|---|
| `item_id` | required | `olx:940356105`, `amazon:B0ABC123`; a bare number means OLX |
| `max_description_chars` | `600` | truncate the description |

### `marketplace_catalog`

No arguments. Returns countries with their marketplaces and per-marketplace
flags (`verified`, `needs_proxy`, `default`, `can_get`, `note`). Call it before
searching when you are unsure what `where` accepts.

### `configure_proxies`

| Argument | Notes |
|---|---|
| `proxies` | list or comma-separated string, `TYPE://[user:pass@]IP:PORT`; `null` or `""` clears |

Returns masked pool state plus `count`. Proxies are process-wide: the next
`marketplace_search` uses them.

### Content tools

`web_search(query, limit=8, fetch_top=0, max_page_chars=2500, region=None)` —
`fetch_top` downloads the top pages and attaches their text.
`web_get(url, max_chars=4000, offset=0)`, `video_search(query, limit=8)`,
`mod_search(query, where="all", limit=10, project_type=None, sort="relevance")`,
`freelance_search(query, boards="all", limit=10)`.

## Prompt patterns that work

```text
# cheap-first with delivery details
find fifine k669 on olx and prom under 2000 UAH, include delivery options

# cross-country comparison
compare iphone 15 across ua, pl and de, show the cheapest five with links

# catalog first, then a blocked marketplace with a proxy
show the marketplace catalog, then search ozon for "rtx 4060" through socks5://1.2.3.4:1080

# product card for a known id
open the item olx:940356105 and list its payment options

# web verification of a marketplace claim
search the web for "fifine k669 review" and fetch the top two pages
```

Two habits make agents much more reliable with this server:

1. Ask for `marketplace_catalog` first when the country or marketplace name is
   uncertain — the error for a wrong `where` wastes a turn.
2. When `sources` shows `blocked`, do not retry blindly: configure a proxy or
   switch the country.

## Debugging

Check that the server answers and lists tools, without an agent:

```bash
printf '%s\n' \
 '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"t","version":"1"}}}' \
 '{"jsonrpc":"2.0","method":"notifications/initialized"}' \
 '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
 | .venv/bin/extsearch-mcp 2>/dev/null | tail -1 | head -c 400
```

Call a tool by hand:

```bash
printf '%s\n' \
 '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"t","version":"1"}}}' \
 '{"jsonrpc":"2.0","method":"notifications/initialized"}' \
 '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"marketplace_catalog","arguments":{}}}' \
 | .venv/bin/extsearch-mcp 2>/dev/null | tail -1 | head -c 600
```

If a client shows no tools, check that the command path is absolute (GUI clients
often have a different `PATH` than your shell) and that the interpreter can
import the package: `/path/to/.venv/bin/python -c "import extsearch"`.
