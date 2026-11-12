"""HTTP layer for the adapters: curl_cffi sessions, throttling, cache, proxies.

Why curl_cffi and not requests/httpx: most marketplaces check the TLS
fingerprint (JA3/JA4). curl_cffi can impersonate Chrome/Safari at the handshake
level, so it passes where httpx gets 403.

Profiles (``profile=``):
    None      — bare libcurl (what OLX likes best)
    "chrome"  — Chrome fingerprint
    "safari"  — fallback when Cloudflare dislikes Chrome
"""

from __future__ import annotations

import asyncio
import json
import time
import urllib.parse
from typing import Any

from curl_cffi import requests as cffi

from .config import CACHE_TTL, MAX_BODY, MIN_INTERVAL, TIMEOUT
from .errors import ExtSearchError
from .proxies import POOL, for_request

_sessions: dict[str | None, "cffi.AsyncSession"] = {}
_lock: asyncio.Lock | None = None
_last = 0.0
_cache: dict[str, tuple[float, Any]] = {}


def session(profile: str | None = None) -> "cffi.AsyncSession":
    if profile not in _sessions:
        kwargs: dict[str, Any] = {"timeout": TIMEOUT}
        if profile:
            kwargs["impersonate"] = profile
        _sessions[profile] = cffi.AsyncSession(**kwargs)
    return _sessions[profile]


async def throttle(min_interval: float = MIN_INTERVAL) -> None:
    """At most one request per min_interval seconds per process."""
    global _lock, _last
    if _lock is None:
        _lock = asyncio.Lock()
    async with _lock:
        wait = min_interval - (time.monotonic() - _last)
        if wait > 0:
            await asyncio.sleep(wait)
        _last = time.monotonic()


def cache_get(key: str) -> Any | None:
    hit = _cache.get(key)
    if hit and time.monotonic() < hit[0]:
        return hit[1]
    return None


def cache_put(key: str, value: Any, ttl: float = CACHE_TTL) -> None:
    if len(_cache) > 512:
        _cache.clear()
    _cache[key] = (time.monotonic() + ttl, value)


async def fetch(
    url: str,
    params: dict[str, Any] | None = None,
    *,
    profile: str | None = None,
    headers: dict[str, str] | None = None,
    as_json: bool = False,
    retries: int = 2,
    ttl: float = CACHE_TTL,
    proxy: str | None = None,
    timeout: float | None = None,
    source: str | None = None,
) -> Any:
    """GET with cache and proxy rotation.

    Returns text or parsed JSON. Raises ExtSearchError with one of the codes
    blocked / needs_proxy / rate_limited / network / not_found.
    """
    host = urllib.parse.urlsplit(url).hostname or ""
    key = f"{profile or 'plain'}:{url}:{json.dumps(params or {}, sort_keys=True)}"
    if ttl:
        hit = cache_get(key)
        if hit is not None:
            return hit

    auth = headers or {}
    last_err: ExtSearchError | None = None
    attempts = retries + 1 if not POOL else max(retries + 1, len(POOL))
    chosen = proxy

    for attempt in range(attempts):
        if chosen is None and POOL:
            chosen = POOL.next()
        proxies = for_request(chosen) if (chosen or POOL) else None
        await throttle()
        sess = session(profile)
        try:
            resp = await sess.get(url, params=params, headers=auth,
                                  proxies=proxies,
                                  timeout=timeout or TIMEOUT)
        except Exception as exc:  # noqa: BLE001 — curl_cffi raises its own errors
            last_err = ExtSearchError(
                "network", f"{host}: network error ({type(exc).__name__}: {exc})",
                source=source)
            if chosen:
                POOL.mark_bad(chosen)
                chosen = None
            await asyncio.sleep(1.0 * (attempt + 1))
            continue

        status = resp.status_code
        if status in (401, 403, 407, 429, 444, 503):
            if chosen:
                POOL.mark_bad(chosen)
                chosen = None
            if status == 429:
                last_err = ExtSearchError("rate_limited", f"{host} rate limited the request (429)",
                                          source=source)
                await asyncio.sleep(2.5 * (attempt + 1))
                continue
            last_err = ExtSearchError(
                "blocked", f"{host} rejected the request (HTTP {status})",
                hint=("this marketplace blocks requests from unsupported regions; "
                      "route it through a proxy (proxies=[\"TYPE://IP:PORT\"] or "
                      "EXTSEARCH_PROXIES)"),
                source=source)
            await asyncio.sleep(0.8 * (attempt + 1))
            continue
        if status == 404:
            raise ExtSearchError("not_found", f"{host}: 404", source=source)
        if status >= 500:
            last_err = ExtSearchError("server_error", f"{host}: HTTP {status}", source=source)
            await asyncio.sleep(1.5 * (attempt + 1))
            continue

        body = resp.text
        if len(body) > MAX_BODY:
            body = body[:MAX_BODY]
        if chosen:
            POOL.mark_ok(chosen)

        value: Any = body
        if as_json:
            try:
                value = json.loads(body)
            except ValueError as exc:
                raise ExtSearchError("bad_response", f"{host}: response is not JSON ({exc})",
                                     source=source) from None
        if ttl:
            cache_put(key, value, ttl)
        return value

    raise last_err or ExtSearchError("blocked", f"{host}: request failed", source=source)


async def fetch_json(url: str, params: dict[str, Any] | None = None, **kw: Any) -> Any:
    return await fetch(url, params, as_json=True, **kw)


async def fetch_text(url: str, params: dict[str, Any] | None = None, **kw: Any) -> str:
    return await fetch(url, params, **kw)
