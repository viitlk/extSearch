"""HTTP API on FastAPI: search, product cards, catalog, content tools.

Run:

    extsearch-api                       # 127.0.0.1:8000
    extsearch-api --host 0.0.0.0 --port 8000

Interactive docs: /docs (OpenAPI). Every error keeps the same shape:

    {"error": "blocked", "message": "...", "hint": "...", "retryable": true}
"""

from __future__ import annotations

from typing import Any, Literal

try:
    from fastapi import FastAPI, HTTPException, Query
    from pydantic import BaseModel, Field
except ImportError as exc:  # pragma: no cover - depends on the install extras
    raise SystemExit(
        "extsearch-api needs the API extras, which are not installed.\n"
        "Install them with:  pip install 'extsearch[api]'\n"
        f"(missing: {exc.name})"
    ) from None

from .config import __version__
from .errors import ExtSearchError
from .media import freelance_search, mod_search, video_search
from .proxies import get_pool, set_proxies
from .search import search, select, sources_catalog
from .web import web_get, web_search

app = FastAPI(
    title="extsearch",
    version=__version__,
    summary="Product search across marketplaces of many countries",
)

STATUS_BY_CODE = {
    "bad_argument": 400,
    "not_found": 404,
    "no_url": 409,
    "unavailable": 501,
    "browser_unavailable": 501,
    "blocked": 502,
    "needs_proxy": 502,
    "rate_limited": 429,
    "timeout": 504,
    "network": 502,
    "server_error": 502,
    "http_error": 502,
    "bad_response": 502,
    "no_results": 404,
}


def _fail(exc: ExtSearchError) -> HTTPException:
    return HTTPException(status_code=STATUS_BY_CODE.get(exc.code, 500),
                         detail=exc.as_dict())


class SearchBody(BaseModel):
    query: str = Field(..., description="search phrase")
    where: str = Field("all", description='countries/marketplaces: "ua", "ua,ru", "olx,ozon", "all,-ru"')
    limit: int = Field(10, ge=1, le=50)
    page: int = Field(1, ge=1)
    price_min: float | None = None
    price_max: float | None = None
    min_rating: float | None = None
    sort: Literal["relevance", "newest", "cheapest", "expensive", "rating",
                  "rating_asc", "reviews", "reviews_asc", "discount"] = "relevance"
    strict: bool = False
    junk_filter: bool | None = None
    include_urls: bool = True
    details: int = Field(0, ge=0, le=20)
    per_source_limit: int | None = Field(None, ge=1, le=50)
    free_only: bool = False
    proxies: list[str] | None = Field(None, description='["socks5://IP:PORT"]')


class ProxyBody(BaseModel):
    proxies: list[str] | str | None = None


@app.get("/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "version": __version__, "proxies": len(get_pool())}


@app.get("/sources")
def sources() -> dict[str, Any]:
    return sources_catalog()


@app.get("/search")
async def search_get(
    q: str = Query(..., min_length=1),
    where: str = "all",
    limit: int = Query(10, ge=1, le=50),
    page: int = Query(1, ge=1),
    sort: str = "relevance",
    price_min: float | None = None,
    price_max: float | None = None,
    min_rating: float | None = None,
    strict: bool = False,
) -> dict[str, Any]:
    """Convenience GET for quick checks and curl."""
    try:
        return await search(q, where=where, limit=limit, page=page, sort=sort,
                            price_min=price_min, price_max=price_max,
                            min_rating=min_rating, strict=strict)
    except ExtSearchError as exc:
        raise _fail(exc) from None


@app.post("/search")
async def search_post(body: SearchBody) -> dict[str, Any]:
    try:
        return await search(
            body.query, where=body.where, limit=body.limit, page=body.page,
            price_min=body.price_min, price_max=body.price_max,
            min_rating=body.min_rating, sort=body.sort, strict=body.strict,
            junk_filter=body.junk_filter, include_urls=body.include_urls,
            details=body.details, per_source_limit=body.per_source_limit,
            free_only=body.free_only, proxies=body.proxies,
        )
    except ExtSearchError as exc:
        raise _fail(exc) from None


@app.get("/item/{item_id:path}")
async def item(item_id: str, max_description_chars: int = Query(600, ge=0, le=20000)) -> dict[str, Any]:
    try:
        return await select(item_id, max_description_chars)
    except ExtSearchError as exc:
        raise _fail(exc) from None


@app.post("/proxies")
def proxies_set(body: ProxyBody) -> dict[str, Any]:
    try:
        pool = set_proxies(body.proxies)
    except ValueError as exc:
        raise HTTPException(status_code=400,
                            detail=ExtSearchError("bad_argument", str(exc)).as_dict()) from None
    return {"count": len(pool), "proxies": pool.stats()}


@app.get("/proxies")
def proxies_get() -> dict[str, Any]:
    pool = get_pool()
    return {"count": len(pool), "proxies": pool.stats()}


@app.get("/web/search")
async def web_search_ep(q: str, limit: int = Query(8, ge=1, le=30),
                        fetch_top: int = Query(0, ge=0, le=10)) -> dict[str, Any]:
    try:
        return await web_search(q, limit=limit, fetch_top=fetch_top)
    except ExtSearchError as exc:
        raise _fail(exc) from None


@app.get("/web/get")
async def web_get_ep(url: str, max_chars: int = Query(4000, ge=1, le=20000),
                     offset: int = Query(0, ge=0)) -> dict[str, Any]:
    try:
        return await web_get(url, max_chars=max_chars, offset=offset)
    except ExtSearchError as exc:
        raise _fail(exc) from None


@app.get("/video")
async def video_ep(q: str, limit: int = Query(8, ge=1, le=30)) -> dict[str, Any]:
    try:
        return await video_search(q, limit=limit)
    except ExtSearchError as exc:
        raise _fail(exc) from None


@app.get("/mods")
async def mods_ep(q: str, where: str = "all", limit: int = Query(10, ge=1, le=30)) -> dict[str, Any]:
    try:
        return await mod_search(q, where=where, limit=limit)
    except ExtSearchError as exc:
        raise _fail(exc) from None


@app.get("/freelance")
async def freelance_ep(q: str, boards: str = "all",
                       limit: int = Query(10, ge=1, le=30)) -> dict[str, Any]:
    try:
        return await freelance_search(q, boards=boards, limit=limit)
    except ExtSearchError as exc:
        raise _fail(exc) from None


def main() -> None:  # pragma: no cover - entry point
    import argparse

    import uvicorn

    parser = argparse.ArgumentParser(description="extsearch HTTP API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()
    uvicorn.run("extsearch.api:app", host=args.host, port=args.port, reload=args.reload)
