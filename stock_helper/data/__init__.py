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
