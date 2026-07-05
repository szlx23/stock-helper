from datetime import date, timedelta

import pytest

from stock_helper import db
from stock_helper.data.daily_kline_cache import (
    DailyKlineCache,
    DailyKlineFetchError,
)


def _bars(count: int, end: date = date(2026, 7, 3), close: float = 10.0) -> list[dict]:
    dates = []
    current = end
    while len(dates) < count:
        if current.weekday() < 5:
            dates.append(current)
        current -= timedelta(days=1)
    return [
        {
            "trade_date": value.isoformat(),
            "open": close - 0.1,
            "high": close + 0.2,
            "low": close - 0.2,
            "close": close,
            "volume": 1000,
            "amount": 10000,
            "pct_chg": 1.2,
        }
        for value in reversed(dates)
    ]


class FakeProvider:
    source_name = "fake"

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def fetch(self, code, start_date, end_date):
        self.calls.append((code, start_date, end_date))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def test_initial_load_returns_latest_80_in_ascending_order(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCK_HELPER_DB", str(tmp_path / "daily.db"))
    provider = FakeProvider([_bars(100)])

    rows = DailyKlineCache(provider).get("600000", lookback_days=80)

    assert len(rows) == 80
    assert [row["trade_date"] for row in rows] == sorted(row["trade_date"] for row in rows)
    assert provider.calls[0][0] == "sh.600000"
    assert (provider.calls[0][2] - provider.calls[0][1]).days >= 260


def test_increment_starts_at_third_latest_session_and_overwrites_latest(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCK_HELPER_DB", str(tmp_path / "increment.db"))
    initial = _bars(6)
    updated = [*initial[-3:-1], {**initial[-1], "close": 12.5, "amount": 99999}]
    provider = FakeProvider([initial, updated])
    cache = DailyKlineCache(provider, overlap_days=3)

    cache.get("sh.600000", lookback_days=80)
    rows = cache.get("sh.600000", lookback_days=80)

    assert provider.calls[1][1].isoformat() == initial[-3]["trade_date"]
    assert rows[-1]["close"] == 12.5
    assert rows[-1]["amount"] == 99999
    with db.connect() as conn:
        count = conn.execute("SELECT COUNT(*) AS n FROM daily_kline WHERE code = 'sh.600000'").fetchone()["n"]
    assert count == 6


def test_fetch_failure_is_explicit_and_does_not_erase_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCK_HELPER_DB", str(tmp_path / "failure.db"))
    provider = FakeProvider([_bars(4), DailyKlineFetchError("network down")])
    cache = DailyKlineCache(provider)
    cache.get("600000")

    with pytest.raises(DailyKlineFetchError, match="network down"):
        cache.get("600000")

    with db.connect() as conn:
        count = conn.execute("SELECT COUNT(*) AS n FROM daily_kline").fetchone()["n"]
    assert count == 4


def test_invalid_arguments_are_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCK_HELPER_DB", str(tmp_path / "invalid.db"))
    cache = DailyKlineCache(FakeProvider([[]]))
    with pytest.raises(ValueError, match="无效"):
        cache.get("abc")
    with pytest.raises(ValueError, match="正整数"):
        cache.get("600000", lookback_days=0)
