from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from stock_helper.data.realtime_provider import (
    RealtimeSnapshotError,
    _quote_is_current,
    load_realtime_snapshot,
)


SHANGHAI = ZoneInfo("Asia/Shanghai")


def test_quote_freshness_during_trading_session():
    now = datetime(2026, 7, 3, 10, 30, tzinfo=SHANGHAI)

    assert _quote_is_current(now - timedelta(minutes=2), now)
    assert not _quote_is_current(now - timedelta(minutes=6), now)
    assert not _quote_is_current(now - timedelta(days=1), now)


def test_lunch_break_accepts_latest_same_day_quote():
    now = datetime(2026, 7, 3, 12, 30, tzinfo=SHANGHAI)
    quote_time = datetime(2026, 7, 3, 11, 30, tzinfo=SHANGHAI)

    assert _quote_is_current(quote_time, now)


def test_bulk_snapshot_maps_realtime_ohlcv(monkeypatch):
    now = datetime.now(SHANGHAI)

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "data": {
                    "total": 1,
                    "diff": [{
                        "f2": 10.2,
                        "f5": 1000,
                        "f6": 123456,
                        "f8": 1.2,
                        "f12": "600000",
                        "f13": 1,
                        "f14": "示例股份",
                        "f15": 10.5,
                        "f16": 9.9,
                        "f17": 10.0,
                        "f18": 9.95,
                        "f124": int(now.timestamp()),
                    }],
                }
            }

    monkeypatch.setattr("stock_helper.data.realtime_provider.requests.get", lambda *args, **kwargs: Response())

    snapshot = load_realtime_snapshot()

    assert snapshot["sh.600000"]["date"] == now.date().isoformat()
    assert snapshot["sh.600000"]["close"] == 10.2
    assert snapshot["sh.600000"]["volume"] == 123456
    assert snapshot["sh.600000"]["quote_time"]


def test_bulk_snapshot_rejects_previous_day_data(monkeypatch):
    old = datetime.now(SHANGHAI) - timedelta(days=1)

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": {"total": 1, "diff": [{"f12": "600000", "f124": int(old.timestamp())}]}}

    monkeypatch.setattr("stock_helper.data.realtime_provider.requests.get", lambda *args, **kwargs: Response())

    with pytest.raises(RealtimeSnapshotError, match="没有可验证"):
        load_realtime_snapshot()


def test_bulk_snapshot_fetches_remaining_pages_concurrently(monkeypatch):
    now = datetime.now(SHANGHAI)
    requested_pages = []

    class Response:
        def __init__(self, page):
            self.page = page

        def raise_for_status(self):
            return None

        def json(self):
            start = (self.page - 1) * 100
            end = min(start + 100, 250)
            rows = []
            for index in range(start, end):
                rows.append({
                    "f2": 10.2,
                    "f6": 1000,
                    "f8": 1,
                    "f12": f"{600000 + index:06d}",
                    "f15": 10.5,
                    "f16": 9.9,
                    "f17": 10.0,
                    "f124": int(now.timestamp()),
                })
            return {"data": {"total": 250, "diff": rows}}

    def fake_get(url, params, timeout):
        page = int(params["pn"])
        requested_pages.append(page)
        return Response(page)

    monkeypatch.setattr("stock_helper.data.realtime_provider.requests.get", fake_get)

    snapshot = load_realtime_snapshot()

    assert len(snapshot) == 250
    assert sorted(requested_pages) == [1, 2, 3]
