"""Public proxy lists: fetch, normalise, deduplicate.

Free lists are a lottery — most entries are dead, slow, or already blocked by the
marketplaces. This module only collects and normalises them; whether a proxy
actually works is decided by `tools/pool_check.py`, which measures it.

Default sources are public text lists refreshed upstream. Override with
``EXTSEARCH_PROXY_SOURCES`` (comma or newline separated URLs or local paths).
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from .errors import ExtSearchError
from .net import fetch_text
from .proxies import _normalize

DEFAULT_SOURCES: tuple[str, ...] = (
    "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
    "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",
    "https://raw.githubusercontent.com/proxifly/free-proxy-list/main/proxies/protocols/http/data.txt",
    "https://proxyscrape.com/v4/free-proxy-list/get?request=display_proxies&protocol=http&proxy_format=ipport&format=text",
)

_LINE = re.compile(r"^(?:[a-z0-9]+://)?[^\s:@]+(?::[^\s@]*)?@?[\w.\-]+:\d{2,5}$", re.I)


def sources() -> list[str]:
    raw = os.environ.get("EXTSEARCH_PROXY_SOURCES", "").strip()
    if not raw:
        return list(DEFAULT_SOURCES)
    items = [part.strip() for part in re.split(r"[,\n]", raw) if part.strip()]
    return items or list(DEFAULT_SOURCES)


def _parse_line(line: str, default_scheme: str) -> str | None:
    item = line.strip()
    if not item or item.startswith("#") or not _LINE.match(item):
        return None
    if "://" not in item:
        item = f"{default_scheme}://{item}"
    try:
        return _normalize(item)
    except ValueError:
        return None


async def collect(include_socks: bool = False, timeout: float = 20.0) -> list[str]:
    """Download every configured list and return unique, validated proxies."""
    found: dict[str, None] = {}
    schemes = ("http", "socks5") if include_socks else ("http",)

    for url in sources():
        text: str
        try:
            if url.startswith(("http://", "https://")):
                text = await fetch_text(url, timeout=timeout, ttl=0, source="proxy-list")
            else:
                text = Path(url).expanduser().read_text(encoding="utf-8", errors="replace")
        except (OSError, ExtSearchError):
            continue
        for line in text.splitlines():
            for scheme in schemes:
                proxy = _parse_line(line, scheme)
                if proxy:
                    found.setdefault(proxy, None)
                    break
    return list(found)
