"""Settings: paths, timeouts, currency rates.

Everything can be overridden with environment variables, so the module works
both as a library and as a service without touching the code.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

__version__ = "2.1.0"

PACKAGE_DIR = Path(__file__).resolve().parent

# Cache and browser profile live outside the package (no repo clutter)
HOME_DIR = Path(os.environ.get("EXTSEARCH_HOME", Path.home() / ".local/share/extsearch"))
CACHE_DIR = Path(os.environ.get("EXTSEARCH_CACHE_DIR", HOME_DIR / "cache"))
PROFILE_DIR = Path(os.environ.get("EXTSEARCH_PROFILE_DIR", HOME_DIR / "browser-profile"))

JUNK_FILE = PACKAGE_DIR / "junk_words.txt"

# Network
TIMEOUT = float(os.environ.get("EXTSEARCH_TIMEOUT", "25"))
MIN_INTERVAL = float(os.environ.get("EXTSEARCH_MIN_INTERVAL", "0.4"))
CACHE_TTL = int(os.environ.get("EXTSEARCH_CACHE_TTL", "300"))
MAX_BODY = int(os.environ.get("EXTSEARCH_MAX_BODY", str(6 * 1024 * 1024)))

# Proxies: "socks5://1.2.3.4:1080,http://user:pass@5.6.7.8:3128"
PROXIES_RAW = os.environ.get("EXTSEARCH_PROXIES", "")

# Rates: how many USD one currency unit is worth. Approximate on purpose:
# they are only used to compare prices across countries, not for accounting.
DEFAULT_FX: dict[str, float] = {
    "USD": 1.0,
    "EUR": 1.08,
    "GBP": 1.27,
    "UAH": 0.024,
    "PLN": 0.25,
    "RON": 0.22,
    "BGN": 0.55,
    "CZK": 0.043,
    "RUB": 0.011,
    "KZT": 0.0020,
    "UZS": 0.000079,
    "JPY": 0.0064,
    "TRY": 0.029,
    "BRL": 0.18,
    "INR": 0.012,
    "IDR": 0.000063,
    "PHP": 0.017,
    "MYR": 0.22,
    "SGD": 0.74,
    "THB": 0.028,
}


def _load_fx() -> dict[str, float]:
    raw = os.environ.get("EXTSEARCH_FX")
    if not raw:
        return dict(DEFAULT_FX)
    try:
        extra = json.loads(raw)
    except ValueError:
        return dict(DEFAULT_FX)
    rates = dict(DEFAULT_FX)
    rates.update({str(k).upper(): float(v) for k, v in extra.items()})
    return rates


FX = _load_fx()


def to_usd(amount: float | None, currency: str | None) -> float | None:
    """Price in USD using the rate table (None when the currency is unknown)."""
    if amount is None:
        return None
    rate = FX.get((currency or "").upper())
    return round(amount * rate, 2) if rate else None
