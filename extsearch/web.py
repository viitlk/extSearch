"""Web search and page text extraction.

No paid APIs: DuckDuckGo HTML as the primary backend, Bing HTML as a fallback.
Why not Exa/Firecrawl:

  * They provide embedding search and clean markdown, but require a key and
    money, which is an extra dependency for a local agent.
  * What actually improves reranking is not the search itself but the full page
    text. So web_search can fetch the top-N pages, and web_get returns clean
    text in slices.

Text extraction drops script/style/nav/header/footer/aside and prefers
article/main over body. No Readability heuristics: they cut useful content more
often than they save it.
"""

from __future__ import annotations

import asyncio
import base64
import html as html_mod
import re
import time
import urllib.parse
from typing import Any

from bs4 import BeautifulSoup
from curl_cffi import requests as creq
from curl_cffi.requests.exceptions import RequestException

from .errors import ExtSearchError as OlxError
from .proxies import for_request

TIMEOUT = 25.0
MIN_INTERVAL = 0.7
CACHE_TTL = 600

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
_HEADERS = {
    "User-Agent": _UA,
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "uk-UA,uk;q=0.9,en;q=0.8,ru;q=0.7",
}

_client: "creq.AsyncSession | None" = None
_lock: asyncio.Lock | None = None
_last = 0.0
_cache: dict[str, tuple[float, Any]] = {}

_DROP_TAGS = ("script", "style", "noscript", "svg", "template", "iframe",
              "form", "nav", "header", "footer", "aside", "button")
_JUNK_RE = re.compile(r"^\s*(cookie|підписатися|подписаться|реклама|advertisement)\b",
                      re.IGNORECASE)


def _session() -> "creq.AsyncSession":
    global _client
    if _client is None:
        _client = creq.AsyncSession(impersonate="chrome", timeout=TIMEOUT,
                                    headers=_HEADERS)
    return _client


async def _throttle() -> None:
    global _lock, _last
    if _lock is None:
        _lock = asyncio.Lock()
    async with _lock:
        wait = MIN_INTERVAL - (time.monotonic() - _last)
        if wait > 0:
            await asyncio.sleep(wait)
        _last = time.monotonic()


async def _fetch(url: str, params: dict[str, Any] | None = None, *,
                 retries: int = 2) -> str:
    key = f"{url}:{urllib.parse.urlencode(params or {})}"
    hit = _cache.get(key)
    if hit and time.monotonic() < hit[0]:
        return hit[1]

    client = _session()
    last_err: OlxError | None = None
    for attempt in range(retries + 1):
        await _throttle()
        try:
            resp = await client.get(url, params=params, proxies=for_request())
        except RequestException as exc:
            last_err = OlxError("network", f"network request failed: {exc}")
            await asyncio.sleep(1.5 * (attempt + 1))
            continue

        if resp.status_code in (202, 429):
            last_err = OlxError("rate_limited", "search engine asked us to slow down")
            await asyncio.sleep(3 * (attempt + 1))
            continue
        if resp.status_code >= 400:
            last_err = OlxError("http_error", f"HTTP {resp.status_code}")
            await asyncio.sleep(1.5 * (attempt + 1))
            continue

        if len(_cache) > 128:
            _cache.clear()
        _cache[key] = (time.monotonic() + CACHE_TTL, resp.text)
        return resp.text

    raise last_err or OlxError("unknown", "page is unavailable")


# --------------------------------------------------------------------------
# Text extraction
# --------------------------------------------------------------------------

def extract_text(raw_html: str, *, max_chars: int = 0) -> dict[str, Any]:
    soup = BeautifulSoup(raw_html, "html.parser")

    title = ""
    if soup.title and soup.title.string:
        title = soup.title.string.strip()

    desc = ""
    meta = soup.find("meta", attrs={"name": "description"}) or \
        soup.find("meta", attrs={"property": "og:description"})
    if meta and meta.get("content"):
        desc = meta["content"].strip()

    for tag in soup(list(_DROP_TAGS)):
        tag.decompose()

    root = (soup.find("article") or soup.find("main")
            or soup.find(attrs={"role": "main"}) or soup.body or soup)

    lines: list[str] = []
    for chunk in root.get_text("\n").split("\n"):
        line = re.sub(r"[ \t\u00a0]+", " ", chunk).strip()
        if not line or len(line) < 2 or _JUNK_RE.match(line):
            continue
        if lines and line == lines[-1]:
            continue
        lines.append(line)

    text = "\n".join(lines)
    text = html_mod.unescape(text)
    total = len(text)
    if max_chars and total > max_chars:
        text = text[:max_chars].rstrip() + "\n…"

    return {"title": title, "description": desc, "text": text, "chars": total}


async def web_get(url: str, max_chars: int = 4000, offset: int = 0) -> dict[str, Any]:
    """Fetch a page and return clean text.

    offset/max_chars let a long page be read in slices instead of flooding the
    context with the whole text at once.
    """
    if not url.lower().startswith(("http://", "https://")):
        raise OlxError("bad_argument", "url must start with http:// or https://")

    raw = await _fetch(url)
    parsed = extract_text(raw)
    text = parsed["text"]
    start = max(0, int(offset))
    piece = text[start:start + max(1, int(max_chars))] if max_chars else text[start:]
    return {
        "url": url,
        "title": parsed["title"],
        "description": parsed["description"],
        "chars": parsed["chars"],
        "offset": start,
        "returned_chars": len(piece),
        "has_more": start + len(piece) < parsed["chars"],
        "text": piece,
    }


# --------------------------------------------------------------------------
# Search
# --------------------------------------------------------------------------

def _clean_ddg_href(href: str) -> str:
    if not href:
        return href
    if href.startswith("//duckduckgo.com/l/"):
        href = "https:" + href
    if "uddg=" in href:
        q = urllib.parse.urlsplit(href).query
        target = urllib.parse.parse_qs(q).get("uddg")
        if target:
            return urllib.parse.unquote(target[0])
    return href


async def _ddg(query: str, limit: int, region: str | None) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"q": query}
    if region:
        params["kl"] = region
    raw = await _fetch("https://html.duckduckgo.com/html/", params)
    soup = BeautifulSoup(raw, "html.parser")
    out: list[dict[str, Any]] = []

    for block in soup.select("div.result"):
        a = block.select_one("a.result__a")
        if not a:
            continue
        url = _clean_ddg_href(a.get("href") or "")
        if not url.startswith("http"):
            continue
        snippet_el = block.select_one(".result__snippet")
        out.append({
            "title": a.get_text(" ", strip=True),
            "url": url,
            "domain": urllib.parse.urlsplit(url).hostname,
            "snippet": snippet_el.get_text(" ", strip=True) if snippet_el else "",
            "provider": "duckduckgo",
        })
        if len(out) >= limit:
            break

    return out


def _decode_bing_href(href: str) -> str:
    """Bing returns redirects like https://www.bing.com/ck/a?...&u=a1<base64url>."""
    if "bing.com/ck/a" not in href:
        return href
    try:
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(href).query)
        raw = (q.get("u") or [""])[0]
        if not raw.startswith("a1"):
            return href
        payload = raw[2:]
        payload += "=" * (-len(payload) % 4)
        return base64.urlsafe_b64decode(payload).decode("utf-8", "ignore") or href
    except Exception:  # noqa: BLE001
        return href


async def _bing(query: str, limit: int) -> list[dict[str, Any]]:
    raw = await _fetch("https://www.bing.com/search", {"q": query, "setlang": "uk"})
    soup = BeautifulSoup(raw, "html.parser")
    out: list[dict[str, Any]] = []
    for li in soup.select("li.b_algo"):
        a = li.select_one("h2 a")
        if not a:
            continue
        url = _decode_bing_href(a.get("href") or "")
        if not url.startswith("http") or "bing.com" in url:
            cite = li.select_one("cite")
            if cite:
                guess = cite.get_text(strip=True)
                if guess:
                    url = guess if guess.startswith("http") else "https://" + guess
        if not url.startswith("http") or "bing.com" in url:
            continue
        p = li.select_one("p, .b_caption p")
        out.append({
            "title": a.get_text(" ", strip=True),
            "url": url,
            "domain": urllib.parse.urlsplit(url).hostname,
            "snippet": p.get_text(" ", strip=True) if p else "",
            "provider": "bing",
        })
        if len(out) >= limit:
            break
    return out


async def web_search(query: str, limit: int = 8, fetch_top: int = 0,
                     max_page_chars: int = 2500,
                     region: str | None = None,
                     provider: str = "auto") -> dict[str, Any]:
    """Web search with optional full-text download of the top-N pages.

    fetch_top is how many top results are downloaded and attached as text.
    That is what gives reranking real ground: a 150-character snippet is almost
    never enough to tell whether a page answers the question.
    """
    query = (query or "").strip()
    if not query:
        raise OlxError("bad_argument", "query must not be empty")
    limit = max(1, min(int(limit), 30))
    fetch_top = max(0, min(int(fetch_top), 10))

    notes: list[str] = []
    results: list[dict[str, Any]] = []

    if provider in ("auto", "duckduckgo"):
        try:
            results = await _ddg(query, limit, region)
        except OlxError as exc:
            notes.append(f"duckduckgo: {exc.code}")
    if not results and provider in ("auto", "bing"):
        try:
            results = await _bing(query, limit)
        except OlxError as exc:
            notes.append(f"bing: {exc.code}")

    if not results:
        raise OlxError("no_results",
                       f"no backend returned results ({', '.join(notes) or 'empty'})")

    if fetch_top:
        targets = results[:fetch_top]

        async def enrich(item: dict[str, Any]) -> None:
            try:
                page = await web_get(item["url"], max_chars=max_page_chars)
                item["content"] = page["text"]
                item["chars"] = page["chars"]
            except OlxError as exc:
                item["content_error"] = exc.code

        await asyncio.gather(*(enrich(it) for it in targets))

    return {
        "query": query,
        "provider": results[0].get("provider"),
        "returned": len(results),
        "fetched_content": min(fetch_top, len(results)),
        "results": results,
        **({"notes": notes} if notes else {}),
    }
