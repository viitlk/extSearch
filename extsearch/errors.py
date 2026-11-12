"""Errors with machine-readable codes and a language-neutral payload.

Every failure carries a stable ``code`` and an English ``message`` so that
clients (LLM agents, HTTP clients, CLI users) do not depend on the language of
the person who wrote the module. Optional fields add context:

    {
      "error": "blocked",
      "message": "allegro.pl returned HTTP 403",
      "hint": "route the request through a proxy in a supported region",
      "source": "allegro",
      "country": "pl",
      "retryable": true
    }
"""

from __future__ import annotations

from typing import Any

# Codes that usually disappear after a retry or after adding a proxy.
RETRYABLE = frozenset({
    "blocked", "needs_proxy", "rate_limited", "network", "timeout",
    "server_error", "http_error",
})


class ExtSearchError(Exception):
    """Exception with a code, an English message and optional context.

    Codes: bad_argument, not_found, blocked, needs_proxy, rate_limited,
    network, timeout, no_results, no_state, no_url, unavailable,
    browser_unavailable, server_error, http_error, bad_response.
    """

    def __init__(self, code: str, message: str, *, hint: str | None = None,
                 source: str | None = None, country: str | None = None,
                 url: str | None = None, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint
        self.source = source
        self.country = country
        self.url = url
        self.details = details or {}

    @property
    def retryable(self) -> bool:
        return self.code in RETRYABLE

    def as_dict(self) -> dict[str, Any]:
        """Language-neutral payload: code + English message + context."""
        out: dict[str, Any] = {
            "error": self.code,
            "message": self.message,
            "retryable": self.retryable,
        }
        if self.hint:
            out["hint"] = self.hint
        if self.source:
            out["source"] = self.source
        if self.country:
            out["country"] = self.country
        if self.url:
            out["url"] = self.url
        if self.details:
            out["details"] = self.details
        return out

    def __str__(self) -> str:  # pragma: no cover - CLI convenience
        return f"{self.code}: {self.message}"


# Backwards-compatible alias: the original OLX-only code raised OlxError.
OlxError = ExtSearchError


# Universal hints, reused by adapters so wording stays consistent.
HINT_PROXY = ("this marketplace blocks requests from unsupported regions; "
              "route it through a proxy (proxies=[\"TYPE://IP:PORT\"])")
HINT_BROWSER = "install the browser extra: pip install playwright && playwright install chromium"
HINT_SEARCH_FIRST = "run a search first: item URLs are remembered from search results"
