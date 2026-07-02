"""Market data providers."""

from dataclasses import dataclass


@dataclass(slots=True)
class StockInfo:
    code: str
    name: str
