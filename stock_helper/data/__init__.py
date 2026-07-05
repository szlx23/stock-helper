"""Market data providers."""

from dataclasses import dataclass


@dataclass(slots=True)
class StockInfo:
    code: str
    name: str


def normalize_a_share_code(raw: str) -> str | None:
    value = raw.strip().lower()
    number = value[-6:]
    if len(number) != 6 or not number.isdigit():
        return None
    if value.startswith(("sh.", "sh")):
        market = "sh"
    elif value.startswith(("sz.", "sz")):
        market = "sz"
    elif value.startswith(("bj.", "bj")):
        market = "bj"
    elif number.startswith(("4", "8")):
        market = "bj"
    elif number.startswith(("5", "6", "9")):
        market = "sh"
    else:
        market = "sz"
    return f"{market}.{number}"


# Imported last because the cache provider reuses normalize_a_share_code.
from stock_helper.data.daily_kline_cache import cache_scanner_daily_kline, get_daily_kline, read_cached_daily_kline  # noqa: E402,F401
from stock_helper.data.daily_kline_sources import fetch_daily_kline  # noqa: E402,F401
