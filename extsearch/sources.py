"""Extra sources: YouTube, Modrinth, CurseForge, freelance boards.

Why each one works this way:

  youtube    — the search HTML page carries the data in var ytInitialData.
               The official Data API needs a key and quota; everything is here.
  modrinth   — open /v2/search API, no key required. Main source for Minecraft
               mods and plugins.
  curseforge — HTML, .project-card items. Cloudflare only passes with
               impersonate=chrome (verified: plain -> 403).
  freelance  — kwork/freelancer/upwork/fiverr load listings via JS and
               freelancehunt has no RSS, so freelance uses a search engine with
               site: operators. It is honest meta-search, not native parsing,
               and the response says so explicitly.
"""

from __future__ import annotations

import json
import re
import urllib.parse
from typing import Any

from bs4 import BeautifulSoup
from curl_cffi import requests as ccreq

from .errors import ExtSearchError as OlxError
from .proxies import for_request

TIMEOUT = 30.0

FREELANCE_BOARDS = {
    "kwork": "kwork.ru",
    "freelancehunt": "freelancehunt.com",
    "freelancer": "freelancer.com",
    "upwork": "upwork.com",
    "fiverr": "fiverr.com",
}

_plain: ccreq.AsyncSession | None = None
_chrome: ccreq.AsyncSession | None = None


def _client(chrome: bool) -> ccreq.AsyncSession:
    global _plain, _chrome
    if chrome:
        if _chrome is None:
            _chrome = ccreq.AsyncSession(impersonate="chrome", timeout=TIMEOUT)
        return _chrome
    if _plain is None:
        _plain = ccreq.AsyncSession(timeout=TIMEOUT)
    return _plain


async def _get(url: str, params: dict[str, Any] | None = None, *,
               chrome: bool = False, as_json: bool = False) -> Any:
    client = _client(chrome)
    try:
        resp = await client.get(url, params=params, proxies=for_request())
    except Exception as exc:
        raise OlxError("network", f"network request failed: {exc}") from None
    if resp.status_code in (401, 403):
        raise OlxError("blocked", f"{urllib.parse.urlsplit(url).hostname}: 403")
    if resp.status_code == 404:
        raise OlxError("not_found", "page not found")
    if resp.status_code >= 400:
        raise OlxError("http_error", f"HTTP {resp.status_code}")
    if as_json:
        try:
            return resp.json()
        except ValueError:
            raise OlxError("bad_response", "response is not JSON") from None
    return resp.text


# --------------------------------------------------------------------------
# YouTube
# --------------------------------------------------------------------------

_YT_MARKER = "var ytInitialData = "


def _walk_video_renderers(node: Any, out: list[dict[str, Any]], limit: int) -> None:
    if len(out) >= limit:
        return
    if isinstance(node, dict):
        vr = node.get("videoRenderer")
        if isinstance(vr, dict):
            title = ""
            runs = (vr.get("title") or {}).get("runs") or []
            if runs:
                title = "".join(r.get("text", "") for r in runs)
            owner = ""
            owner_runs = (vr.get("ownerText") or {}).get("runs") or []
            if owner_runs:
                owner = owner_runs[0].get("text", "")
            vid = vr.get("videoId")
            if vid and title:
                seconds = None
                length = (vr.get("lengthText") or {}).get("simpleText") or ""
                m = re.match(r"(\d+):(\d+)(?::(\d+))?$", length.strip())
                if m:
                    parts = [int(x) for x in m.groups() if x is not None]
                    seconds = (parts[0] * 60 + parts[1]) if len(parts) == 2 \
                        else (parts[0] * 3600 + parts[1] * 60 + parts[2])
                out.append({
                    "id": vid,
                    "title": title,
                    "channel": owner,
                    "url": f"https://www.youtube.com/watch?v={vid}",
                    "duration": length.strip() or None,
                    "duration_seconds": seconds,
                    "published": (vr.get("publishedTimeText") or {}).get("simpleText"),
                    "views": (vr.get("viewCountText") or {}).get("simpleText"),
                })
                return
        for value in node.values():
            _walk_video_renderers(value, out, limit)
    elif isinstance(node, list):
        for item in node:
            _walk_video_renderers(item, out, limit)


async def youtube_search(query: str, limit: int = 8) -> dict[str, Any]:
    raw = await _get("https://www.youtube.com/results", {"search_query": query})
    idx = raw.find(_YT_MARKER)
    if idx < 0:
        raise OlxError("no_state", "YouTube did not provide ytInitialData — layout changed")
    # A .*? regex matched the first ";</script>" inside the string, so a real
    # JSON decoder is used starting at the marker position.
    try:
        data, _ = json.JSONDecoder().raw_decode(raw, idx + len(_YT_MARKER))
    except ValueError as exc:
        raise OlxError("no_state", f"could not parse ytInitialData: {exc}") from None

    items: list[dict[str, Any]] = []
    _walk_video_renderers(data, items, limit)
    return {"query": query, "returned": len(items), "items": items}


# --------------------------------------------------------------------------
# Modrinth
# --------------------------------------------------------------------------

_MODRINTH_SORTS = {
    "relevance": "relevance",
    "downloads": "downloads",
    "newest": "newest",
    "updated": "updated",
    "follows": "follows",
}


async def modrinth_search(query: str, limit: int = 10,
                          project_type: str | None = None,
                          sort: str = "relevance") -> dict[str, Any]:
    params: dict[str, Any] = {
        "query": query,
        "limit": max(1, min(int(limit), 50)),
        "index": _MODRINTH_SORTS.get(sort, "relevance"),
    }
    if project_type:
        params["facets"] = json.dumps([[f"project_type:{project_type}"]])
    data = await _get("https://api.modrinth.com/v2/search", params, as_json=True)

    items = []
    for hit in data.get("hits") or []:
        ptype = hit.get("project_type") or "mod"
        items.append({
            "id": hit.get("project_id") or hit.get("slug"),
            "slug": hit.get("slug"),
            "title": hit.get("title"),
            "description": hit.get("description"),
            "author": hit.get("author"),
            "downloads": hit.get("downloads"),
            "follows": hit.get("follows"),
            "type": ptype,
            "categories": hit.get("categories") or [],
            "versions": hit.get("versions") or [],
            "url": f"https://modrinth.com/{ptype}/{hit.get('slug')}",
        })
    return {
        "query": query,
        "total": data.get("total_hits"),
        "returned": len(items),
        "items": items,
    }


# --------------------------------------------------------------------------
# CurseForge
# --------------------------------------------------------------------------

async def curseforge_search(query: str, limit: int = 10) -> dict[str, Any]:
    raw = await _get("https://www.curseforge.com/minecraft/search",
                     {"search": query}, chrome=True)
    soup = BeautifulSoup(raw, "html.parser")
    items: list[dict[str, Any]] = []

    for card in soup.select(".project-card"):
        link = card.select_one("a.name") or card.select_one("a.overlay-link")
        if not link:
            continue
        href = link.get("href") or ""
        title_el = card.select_one("a.name .ellipsis") or card.select_one("a.name")
        author_el = card.select_one(".author-name .ellipsis")
        kind_el = card.select_one(".class-tag-overlay")
        items.append({
            "id": href.strip("/").split("/")[-1] if href else None,
            "title": title_el.get_text(strip=True) if title_el else None,
            "author": author_el.get_text(strip=True) if author_el else None,
            "type": kind_el.get_text(strip=True) if kind_el else None,
            "url": f"https://www.curseforge.com{href}" if href.startswith("/") else href,
        })
        if len(items) >= limit:
            break

    if not items:
        raise OlxError("no_state", "CurseForge: no project cards found — layout changed")
    return {"query": query, "returned": len(items), "items": items}


# --------------------------------------------------------------------------
# Freelance — meta-search across boards via a search engine
# --------------------------------------------------------------------------

async def freelance_search(query: str, boards: list[str] | None = None,
                           limit: int = 10, region: str | None = None) -> dict[str, Any]:
    from . import web  # local import to avoid cycles

    if boards is None:
        boards = list(FREELANCE_BOARDS)
    unknown = [b for b in boards if b not in FREELANCE_BOARDS]
    if unknown:
        raise OlxError(
            "bad_argument",
            f"unknown board: {', '.join(unknown)}. "
            f"Allowed: {', '.join(FREELANCE_BOARDS)}",
        )

    # One request per board. Engines treat "site:a OR site:b" differently:
    # DDG often returns nothing, Bing ignores operators entirely and returns a
    # generic result page.
    per_board: dict[str, int] = {}
    results: list[dict[str, Any]] = []
    notes: list[str] = []
    per = max(2, limit // max(1, len(boards)) + 1)

    for board in boards:
        domain = FREELANCE_BOARDS[board]
        matched = 0
        # The site: operator is tried first, but Bing ignores it and DDG
        # throttles frequent queries. So the second attempt is a plain query with
        # the board name, and results are filtered by domain anyway.
        for text in (f"{query} site:{domain}", f"{query} {board}"):
            try:
                found = await web.web_search(text, limit=per, region=region)
            except OlxError as exc:
                notes.append(f"{board}/{text[:12]}: {exc.code}")
                continue
            for item in found["results"]:
                host = (item.get("domain") or "").lower()
                if domain not in host:
                    continue
                results.append({**item, "board": board})
                matched += 1
            if matched:
                break
        per_board[board] = matched

    if not results:
        raise OlxError(
            "no_results",
            "The search engine returned nothing for any board. "
            + ("; ".join(notes) if notes else "Try another query."),
        )

    return {
        "query": query,
        "mode": "meta-search via a search engine (site:), not the native board APIs",
        "boards": boards,
        "per_board": per_board,
        "returned": len(results[:limit]),
        "results": results[:limit],
        **({"notes": notes} if notes else {}),
    }
