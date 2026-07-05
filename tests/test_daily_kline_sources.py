from datetime import date

import pandas as pd
import pytest

from stock_helper import db
from stock_helper.data.daily_kline_sources import (
    COLUMNS,
    DailyKlineAdapter,
    DailyKlineSourceError,
    EastmoneyAdapter,
    LocalCacheAdapter,
    fetch_daily_kline,
)


def _canonical(source="first"):
    return pd.DataFrame(
        [{
            "code": "sh.600000", "trade_date": "2026-07-03", "open": 10,
            "high": 11, "low": 9, "close": 10.5, "volume": 100,
            "amount": 1000, "pct_chg": 1.2, "turnover": 0.8, "source": source,
        }]
    )


class StubAdapter(DailyKlineAdapter):
    def __init__(self, source, result):
        self.source = source
        self.result = result
        self.called = 0

    def fetch(self, code, start_date, end_date, adjust):
        self.called += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def test_fallback_continues_after_error_empty_and_bad_fields(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCK_HELPER_DB", str(tmp_path / "fallback.db"))
    failed = StubAdapter("eastmoney", RuntimeError("down"))
    empty = StubAdapter("tencent", pd.DataFrame())
    malformed = StubAdapter("sina", pd.DataFrame([{"close": 10}]))
    success = StubAdapter("netease", _canonical("wrong-label"))

    frame = fetch_daily_kline(
        "600000", "20260701", "2026-07-03",
        adapters=[failed, empty, malformed, success],
    )

    assert list(frame.columns) == COLUMNS
    assert frame.iloc[0]["source"] == "netease"
    assert [adapter.called for adapter in (failed, empty, malformed, success)] == [1, 1, 1, 1]


def test_local_cache_is_last_resort_and_marked(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCK_HELPER_DB", str(tmp_path / "local.db"))
    db.init_db()
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO daily_kline
            (code, trade_date, open, high, low, close, volume, amount, pct_chg, source)
            VALUES ('sh.600000', '2026-07-03', 10, 11, 9, 10.5, 100, 1000, 1.2, 'eastmoney')
            """
        )
    failed = StubAdapter("eastmoney", RuntimeError("down"))

    frame = fetch_daily_kline(
        "sh600000", date(2026, 7, 1), date(2026, 7, 3),
        adapters=[failed, LocalCacheAdapter()],
    )

    assert frame.iloc[0]["source"] == "local_cache"


def test_local_qfq_cache_is_not_used_for_other_adjustments(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCK_HELPER_DB", str(tmp_path / "adjust.db"))
    failed = StubAdapter("eastmoney", RuntimeError("down"))
    with pytest.raises(DailyKlineSourceError, match="仅保存 qfq"):
        fetch_daily_kline(
            "600000", "2026-07-01", "2026-07-03", adjust="hfq",
            adapters=[failed, LocalCacheAdapter()],
        )


def test_all_sources_failure_reports_each_source(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCK_HELPER_DB", str(tmp_path / "all-fail.db"))
    adapters = [StubAdapter("eastmoney", RuntimeError("a")), StubAdapter("tencent", RuntimeError("b"))]
    with pytest.raises(DailyKlineSourceError) as exc_info:
        fetch_daily_kline("600000", "2026-07-01", "2026-07-03", adapters=adapters)
    assert "eastmoney" in str(exc_info.value)
    assert "tencent" in str(exc_info.value)


def test_eastmoney_adapter_normalizes_akshare_fields(monkeypatch):
    source_frame = pd.DataFrame(
        [{"日期": "2026-07-03", "开盘": 10, "最高": 11, "最低": 9, "收盘": 10.5,
          "成交量": 100, "成交额": 1000, "涨跌幅": 1.2}]
    )
    calls = []

    def fake_hist(**kwargs):
        calls.append(kwargs)
        return source_frame

    monkeypatch.setattr("akshare.stock_zh_a_hist", fake_hist)
    frame = EastmoneyAdapter().fetch("sh.600000", date(2026, 7, 1), date(2026, 7, 3), "qfq")

    assert calls == [{"symbol": "600000", "period": "daily", "start_date": "20260701", "end_date": "20260703", "adjust": "qfq"}]
    assert list(frame.columns) == COLUMNS
    assert frame.iloc[0]["source"] == "eastmoney"
