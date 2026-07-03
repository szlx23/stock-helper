import threading
import time

import pytest

from stock_helper import db
from stock_helper.config import StrategyConfig
from stock_helper.data import StockInfo
from stock_helper.data.multi_provider import MultiProvider
from stock_helper.scanner import ScanCancelled, StockScanner, _market_today
from tests.test_strategy import make_rows


class DelayedProvider:
    SOURCE_NAME = "Delayed"
    delay = 0.05
    slow_delay = None
    active = 0
    max_active = 0
    completed = 0
    lock = threading.Lock()
    stocks = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return None

    def list_stocks(self):
        return list(type(self).stocks)

    def get_history_range(self, code, start_date, end_date):
        cls = type(self)
        with cls.lock:
            cls.active += 1
            cls.max_active = max(cls.max_active, cls.active)
        delay = cls.delay if cls.slow_delay is None or code.endswith("000") else cls.slow_delay
        try:
            time.sleep(delay)
            rows = make_rows()
            rows[-1]["date"] = _market_today().isoformat()
            return rows
        finally:
            with cls.lock:
                cls.active -= 1
                cls.completed += 1


def _scanner(stock_count: int, *, delay: float, slow_delay: float | None = None) -> StockScanner:
    DelayedProvider.delay = delay
    DelayedProvider.slow_delay = slow_delay
    DelayedProvider.active = 0
    DelayedProvider.max_active = 0
    DelayedProvider.completed = 0
    DelayedProvider.stocks = [StockInfo(f"sh.600{index:03d}", f"示例{index}") for index in range(stock_count)]

    def provider_factory(log=None):
        return MultiProvider([DelayedProvider], log=log)

    return StockScanner(
        provider_factory=provider_factory,
        snapshot_loader=lambda log=None: {
            stock.code: _snapshot_bar() for stock in DelayedProvider.stocks
        },
    )


def _snapshot_bar() -> dict:
    row = make_rows()[-1]
    return {
        "date": _market_today().isoformat(),
        "open": row["open"],
        "high": row["high"],
        "low": row["low"],
        "close": row["close"],
        "volume": row["volume"],
        "turn": row["turn"],
    }


def test_fetch_pool_reduces_first_run_wall_time(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCK_HELPER_DB", str(tmp_path / "parallel.db"))
    db.init_db()
    scanner = _scanner(8, delay=0.08)

    started = time.perf_counter()
    candidates = scanner.run(StrategyConfig(fetch_workers=4, max_workers=4))
    elapsed = time.perf_counter() - started

    sequential_delay = 8 * 0.08
    assert DelayedProvider.max_active >= 3
    assert elapsed < sequential_delay * 0.75
    assert len(candidates) == 8


def test_first_hit_arrives_before_all_fetches_finish(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCK_HELPER_DB", str(tmp_path / "streaming.db"))
    db.init_db()
    scanner = _scanner(8, delay=0.01, slow_delay=0.15)
    completed_when_first_hit = []
    progress_events = []

    def progress(**values):
        progress_events.append(values)
        if values.get("hits_detail") and not completed_when_first_hit:
            completed_when_first_hit.append(DelayedProvider.completed)

    candidates = scanner.run(StrategyConfig(fetch_workers=4, max_workers=2), progress=progress)

    assert completed_when_first_hit
    assert completed_when_first_hit[0] < len(DelayedProvider.stocks)
    assert len(candidates) == 8
    pipeline_events = [event for event in progress_events if event.get("phase") == "pipeline"]
    assert pipeline_events[-1]["fetched"] == 8
    assert pipeline_events[-1]["analyzed"] == 8
    assert pipeline_events[-1]["completed"] == 8


def test_pipeline_cancellation_does_not_drain_full_queue(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCK_HELPER_DB", str(tmp_path / "cancel.db"))
    db.init_db()
    scanner = _scanner(20, delay=0.08)
    stop_event = threading.Event()
    timer = threading.Timer(0.03, stop_event.set)
    timer.start()
    started = time.perf_counter()

    try:
        with pytest.raises(ScanCancelled):
            scanner.run(
                StrategyConfig(fetch_workers=4, max_workers=2),
                stop_event=stop_event,
            )
    finally:
        timer.cancel()

    elapsed = time.perf_counter() - started
    assert elapsed < 0.35
    assert DelayedProvider.completed < len(DelayedProvider.stocks)


def test_fetch_worker_boundary_is_validated():
    StrategyConfig(fetch_workers=16).validate()
    with pytest.raises(ValueError, match="当前为 17"):
        StrategyConfig(fetch_workers=17).validate()


def test_mixed_universe_analyzes_only_current_day_stocks(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCK_HELPER_DB", str(tmp_path / "mixed-realtime.db"))
    db.init_db()

    class MixedRealtimeProvider(DelayedProvider):
        SOURCE_NAME = "MixedRealtime"

        def get_history_range(self, code, start_date, end_date):
            rows = make_rows()
            if int(code[-1]) % 2 == 0:
                rows[-1]["date"] = _market_today().isoformat()
            return rows

    MixedRealtimeProvider.stocks = [StockInfo(f"sh.60000{index}", f"示例{index}") for index in range(4)]

    def provider_factory(log=None):
        return MultiProvider([MixedRealtimeProvider], log=log)

    progress_events = []
    candidates = StockScanner(
        provider_factory=provider_factory,
        snapshot_loader=lambda log=None: {
            stock.code: _snapshot_bar()
            for stock in MixedRealtimeProvider.stocks
            if int(stock.code[-1]) % 2 == 0
        },
    ).run(
        StrategyConfig(fetch_workers=2, max_workers=2),
        progress=lambda **values: progress_events.append(values),
    )

    assert [item["code"] for item in candidates] == ["sh.600000", "sh.600002"]
    final = [event for event in progress_events if event.get("phase") == "pipeline"][-1]
    assert final["realtime_skipped"] == 2
    assert final["analyzed"] == 2
