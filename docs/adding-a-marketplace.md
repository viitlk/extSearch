# Adding a marketplace

A marketplace is: one parser, one registration line, one test run. This page
walks through a real addition (Shafa, a Ukrainian second-hand fashion site) and
then shows the short path for a config-only source.

## 0. Decide the shape

| Situation | Use |
|---|---|
| site returns JSON | write a parser with `net.fetch_json` |
| stable attributes / classes in HTML | write a parser, or `css_search` config |
| hashed CSS-module classes | parse the **structure** (ancestors, links), not classes |
| needs a browser (Cloudflare challenge) | see `rozetka` in `catalog.py` |
| search needs JS but the API is public | find the API with DevTools → Network |

## 1. Look at the raw response

Never write a parser against what the browser shows; get the bytes first.

```bash
python - <<'EOF'
import asyncio
from extsearch.net import fetch_text

html = asyncio.run(fetch_text("https://shafa.ua/uk", {"search": "nike"}))
open("page.html", "w").write(html)
print(len(html))
EOF
```

Then inspect `page.html`: search for a price pattern, find the nearest
`<a href>` around it, and check what is unique about it.

```bash
grep -o 'class="[^"]*"' page.html | sort | uniq -c | sort -rn | head
grep -o 'href="[^"]*item[^"]*"' page.html | head
```

Rules of thumb:

- Hashed class names (`shop_1tew61q_by68k9`) change on every deploy — do not
  use them. Attributes (`data-qaid`, `data-testid`), URLs and page structure
  survive.
- If the site embeds a JSON state (`window.__PRERENDERED_STATE__`,
  `ytInitialData`), prefer it: parsing JSON is stable, parsing HTML is not.
- Check the response with and without a browser fingerprint (plain libcurl vs
  `profile="chrome"`); many sites answer differently.

## 2. Write the parser

Real code, `extsearch/catalog_extra.py`:

```python
SHAFA_SEARCH = "https://shafa.ua/uk"
_SHAFA_ID = re.compile(r"/item/(\d+)")
_SHAFA_PRICE = re.compile(r"(\d[\d\s\u00a0]*)\s*грн")


def _shafa_card(link):
    """Walk up until the ancestor that also holds the price.

    Shafa uses CSS modules with hashed class names, so no class selector is
    stable. The structure is: the product link, then a sibling footer with the
    price; their common ancestor is the card.
    """
    node = link
    for _ in range(6):
        node = node.parent
        if node is None:
            return None
        if _SHAFA_PRICE.search(node.get_text(" ", strip=True)):
            return node
    return None


async def shafa_search(query: str, limit: int = 10, page: int = 1) -> list[dict]:
    params: dict = {"search": query}
    if page > 1:
        params["page"] = page
    html = await _fetch(SHAFA_SEARCH, params, chrome=True)
    soup = BeautifulSoup(html, "html.parser")
    rows: list[dict] = []
    seen: set[int] = set()

    for link in soup.select('a[href*="/uk/item/"]'):
        href = link.get("href") or ""
        match = _SHAFA_ID.search(href)
        if match is None:
            continue
        offer_id = int(match.group(1))
        if offer_id in seen:          # the same card is linked twice
            continue
        card = _shafa_card(link)
        if card is None:
            continue
        image = card.select_one("img[alt]")
        title = _squash(image.get("alt") if image else "")
        if not title:
            continue
        seen.add(offer_id)
        url = href if href.startswith("http") else f"https://shafa.ua{href}"
        _remember("shafa", offer_id, url)     # id -> url for select()
        price_match = _SHAFA_PRICE.search(card.get_text(" ", strip=True))
        rows.append({
            "id": offer_id,
            "title": title,
            "price": _int_from(price_match.group(1)) if price_match else None,
            "currency": "UAH",
            "state": "used",
            "availability": None,
            "city": None,
            "business": False,
            "relevance": _relevance(query, title),
            "url": url,
        })

    return rows[: max(limit * 2, 20)]
```

What matters here:

- `_fetch(..., chrome=True)` — the site answers 403 to plain libcurl.
- `_remember(source, id, url)` — required, otherwise `select()` cannot open the
  card later.
- `_relevance(query, title)` and `_int_from(...)` come from `catalog`, reuse
  them instead of writing your own.
- Return more rows than `limit`: `_finish` applies `price_min`/`price_max` and
  `strict` afterwards, so a tight slice can end up empty.
- Dedupe by id: listing pages link the same card from image and title.

### Optional product card

```python
async def shafa_get(offer_id: int, max_description_chars: int = 600) -> dict:
    return await _product_page(
        offer_id, "shafa", max_description_chars,
        title_sel="h1", price_sel="[itemprop='price'], .product-price",
    )
```

`_product_page` fetches the remembered URL, extracts title/price/description and
attaches delivery/payment when the text contains them. Skip `get` entirely if
the card is behind a login or a challenge — `can_get: false` is a valid state.

## 3. Register it

Same file, two dicts:

```python
SEARCHERS = {
    "shafa": shafa_search,
    # …
}

GETTERS = {
    "shafa": shafa_get,
    # …
}
```

And in `extsearch/marketplaces/ua.py`:

```python
_UA_STORES = [
    # …
    ("shafa", "Shafa", "second-hand fashion"),
]
```

For a marketplace in another country, use the matching module (`ru.py`, `us.py`,
`eu.py`, `asia.py`, `cis.py`, `global_.py`) with the adapter helpers:

```python
register(Marketplace(
    name="my_shop",
    title="My Shop",
    country="pl",
    currency="PLN",
    search=rows_search("my_shop", lambda q, limit, page: my_shop_search(q, limit=limit, page=page)),
    get=simple_get(my_shop_get),
    verified=True,        # confirmed by a real run
    needs_proxy=False,    # answers without a proxy
    default=True,         # included in "all" and in country-wide searches
    note="short human-readable description",
))
```

Flags are a promise, not decoration:

- `verified=True` only if you saw real items from a normal connection.
- `needs_proxy=True` if it answers `403`/`444` from the wrong region.
- `default=False` for verticals that pollute general searches (cars, real
  estate, dating) — they stay reachable by name.

## 4. Config-only source (no Python)

If the markup is stable, `css_search` needs no parser at all:

```python
from .adapters import css_search, rows_search

register(Marketplace(
    name="example_shop",
    title="Example Shop",
    country="us",
    currency="USD",
    search=rows_search(
        "example_shop",
        lambda q, limit, page: css_search(
            "example_shop",
            "https://example.com/search?q={q}",
            query=q, limit=limit, page=page, currency="USD",
            item_sel="div.product-card",
            title_sel="a.product-name",
            link_sel="a.product-name",
            price_sel="span.price",
            id_regex=r"/product/(\d+)",
        ),
    ),
    verified=False,       # markup not confirmed
    needs_proxy=True,
))
```

`{q}` is URL-encoded, `{qdash}` is the query with dashes — that covers the two
common URL schemes. When `price_sel` is omitted, the price is found in the card
text with a currency regex, which survives class renames.

## 5. Test it

```bash
python tools/check_sources.py shafa
# shafa            ok   n=3  Догляд для шкіри … 50 UAH
```

Then a real query, a product card, and the country-wide run:

```bash
python - <<'EOF'
import asyncio
from extsearch import search, select

async def main():
    res = await search("nike", where="shafa", limit=3)
    print(res["sources"])
    print([(i["price"], i["title"][:40]) for i in res["items"]])
    card = await select(res["items"][0]["id"])
    print(card.get("title"), card.get("price"))

asyncio.run(main())
EOF
```

Finally run the suite (`python tests/smoke_test.py`) and add the name to the
README table.

## 6. Checklist

- [ ] Parser returns `id`, `title`, `price`, `currency`, `relevance`, `url`.
- [ ] `_remember()` is called for every id that `select()` should open.
- [ ] Rows are deduplicated by id.
- [ ] More rows returned than `limit` (filters run afterwards).
- [ ] Correct TLS profile: plain, `chrome`, or `safari`.
- [ ] Registered in `SEARCHERS` / `GETTERS` and in a country module.
- [ ] Flags set honestly (`verified`, `needs_proxy`, `default`).
- [ ] `tools/check_sources.py <name>` passes.
- [ ] README table updated.

## 7. Pitfalls

| Symptom | Cause |
|---|---|
| `price: null` for everything | price is rendered by JS or lives in another card |
| `relevance: 0` | title came from a brand line, not the product name (Amazon has two `h2` per card) |
| duplicates in results | the same card is linked from image and title |
| `select()` says `no_url` | `_remember()` was not called, or the process restarted |
| 403 in production but not locally | the site fingerprint-checks; try another profile or a proxy |
| empty page, HTTP 200 | anti-bot placeholder; compare response size between profiles |
| prices 100× smaller | the API returns kopecks/cents (`price / 100`), as with Wildberries |
