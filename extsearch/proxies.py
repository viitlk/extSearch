"""Proxies: parse ``TYPE://IP:PORT`` strings and rotate them.

Supported schemes: http, https, socks4, socks4a, socks5, socks5h — exactly what
curl (and therefore curl_cffi) understands. Credentials are allowed:
``socks5://user:pass@1.2.3.4:1080``.

Public entry points:

    from extsearch.proxies import set_proxies, get_pool, for_request

    set_proxies("socks5://1.2.3.4:1080,http://5.6.7.8:3128")
    for_request()  # -> {"http": ..., "https": ...} or None
"""

from __future__ import annotations

import os
import re
import time
import urllib.parse
from typing import Iterable, Sequence

SCHEMES = ("http", "https", "socks4", "socks4a", "socks5", "socks5h")
_SPLIT_RE = re.compile(r"[\s,;]+")


def _normalize(raw: str) -> str | None:
    """Validate and normalise a proxy to ``scheme://[user:pass@]host:port``."""
    item = raw.strip()
    if not item or item.startswith("#"):
        return None
    if "://" not in item:
        item = "http://" + item
    parts = urllib.parse.urlsplit(item)
    scheme = parts.scheme.lower()
    if scheme not in SCHEMES:
        raise ValueError(
            f"{raw!r}: unknown scheme {scheme!r}; allowed: {', '.join(SCHEMES)}")
    if not parts.hostname:
        raise ValueError(f"{raw!r}: missing host")
    try:
        port = parts.port
    except ValueError as exc:
        raise ValueError(f"{raw!r}: bad port ({exc})") from None
    if not port:
        raise ValueError(f"{raw!r}: port required, format {scheme}://IP:PORT")
    auth = ""
    if parts.username:
        user = urllib.parse.quote(parts.username, safe="")
        password = urllib.parse.quote(parts.password or "", safe="")
        auth = f"{user}:{password}@"
    return f"{scheme}://{auth}{parts.hostname}:{port}"


def parse_proxies(raw: str | Sequence[str] | None) -> list[str]:
    """String or list -> validated proxy list without duplicates."""
    if raw is None:
        return []
    if isinstance(raw, str):
        chunks: Iterable[str] = _SPLIT_RE.split(raw)
    else:
        chunks = []
        for part in raw:
            chunks.extend(_SPLIT_RE.split(str(part)))
    out: list[str] = []
    for chunk in chunks:
        proxy = _normalize(chunk)
        if proxy and proxy not in out:
            out.append(proxy)
    return out


def mask(proxy: str) -> str:
    """Mask credentials for logs and API responses."""
    return re.sub(r"//[^@/]+@", "//***@", proxy)


class ProxyPool:
    """Round-robin with cooldown: a failing proxy is skipped for N seconds."""

    def __init__(self, proxies: Sequence[str] | None = None, cooldown: float = 120.0):
        self.proxies = list(proxies or [])
        self.cooldown = cooldown
        self._dead: dict[str, float] = {}
        self._i = 0
        self._errors: dict[str, int] = {}

    def __bool__(self) -> bool:
        return bool(self.proxies)

    def __len__(self) -> int:
        return len(self.proxies)

    def next(self) -> str | None:
        if not self.proxies:
            return None
        now = time.monotonic()
        for _ in range(len(self.proxies)):
            proxy = self.proxies[self._i % len(self.proxies)]
            self._i += 1
            if self._dead.get(proxy, 0) <= now:
                return proxy
        # everything is cooling down: take the one expiring first
        return min(self.proxies, key=lambda p: self._dead.get(p, 0))

    def mark_bad(self, proxy: str | None) -> None:
        if not proxy:
            return
        self._errors[proxy] = self._errors.get(proxy, 0) + 1
        self._dead[proxy] = time.monotonic() + self.cooldown

    def mark_ok(self, proxy: str | None) -> None:
        if proxy:
            self._dead.pop(proxy, None)

    def stats(self) -> list[dict]:
        now = time.monotonic()
        return [{
            "proxy": mask(p),
            "errors": self._errors.get(p, 0),
            "cooling_down": self._dead.get(p, 0) > now,
        } for p in self.proxies]

    def update(self, raw: str | Sequence[str] | None) -> None:
        self.proxies = parse_proxies(raw)
        self._dead.clear()
        self._errors.clear()
        self._i = 0


POOL = ProxyPool(parse_proxies(os.environ.get("EXTSEARCH_PROXIES") or ""))


def get_pool() -> ProxyPool:
    return POOL


def set_proxies(raw: str | Sequence[str] | None) -> ProxyPool:
    POOL.update(raw)
    return POOL


def for_request(proxy: str | None = None) -> dict[str, str] | None:
    """Proxy in curl_cffi format for a single request (None = direct)."""
    chosen = proxy or POOL.next()
    if not chosen:
        return None
    return {"http": chosen, "https": chosen}


def enabled() -> bool:
    return bool(POOL)
