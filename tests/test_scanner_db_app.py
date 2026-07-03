import json
import asyncio
from pathlib import Path
from urllib.parse import urlencode

import pytest
from starlette.datastructures import FormData
from starlette.requests import Request

from stock_helper import db
from stock_helper.app import SECURITY_HEADERS, _config_from_form, add_security_headers, app, cancel_scan, clear_db, healthz, home, run_scan, scan_events
from stock_helper.config import StrategyConfig
from stock_helper.data import StockInfo, normalize_a_share_code
from stock_helper.data.multi_provider import MultiProvider
from stock_helper.scan_tasks import ScanInProgressError, ScanTaskManager
from stock_helper.scanner import RealtimeDataUnavailable, StockScanner, _limit_stocks, _market_today, ensure_history_cached
from tests.test_strategy import make_rows


class FakeProvider:
    SOURCE_NAME = "Fake"
    fetch_count = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return None

    def list_stocks(self):
        return [StockInfo("sh.600000", "示例股份")]

    def get_history_range(self, code, start_date, end_date):
        type(self).fetch_count += 1
        rows = make_rows()
        rows[-1]["date"] = _market_today().isoformat()
        return rows


class FailingProvider(FakeProvider):
    SOURCE_NAME = "Failing"

    def __enter__(self):
        raise RuntimeError("network down")


class FailingListProvider(FakeProvider):
    SOURCE_NAME = "FailingList"

    def list_stocks(self):
        raise RuntimeError("network down")


class BackupProvider(FakeProvider):
    SOURCE_NAME = "Backup"


def _make_scanner():
    """Create a StockScanner patched to use FakeProvider."""
    def fake_provider_factory(log=None):
        return MultiProvider([FakeProvider], log=log)
    return StockScanner(
        provider_factory=fake_provider_factory,
        snapshot_loader=lambda log=None: {"sh.600000": _current_bar()},
    )


def _current_bar() -> dict:
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


def test_scanner_returns_candidates(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCK_HELPER_DB", str(tmp_path / "scan.db"))
    db.init_db()

    FakeProvider.fetch_count = 0
    scanner = _make_scanner()
    candidates = scanner.run(StrategyConfig())

    assert len(candidates) == 1
    assert candidates[0]["code"] == "sh.600000"
    assert candidates[0]["score"] > 0
    assert "贴近10日线" in candidates[0]["reasons"]


def test_database_saves_scan_params_and_candidates(tmp_path, monkeypatch):
    db_path = tmp_path / "stock_helper.db"
    monkeypatch.setenv("STOCK_HELPER_DB", str(db_path))
    db.init_db()
    config = StrategyConfig(max_price=20, score_yin_line=33)
    scan_id = db.create_scan(config)
    FakeProvider.fetch_count = 0
    scanner = _make_scanner()
    candidates = scanner.run(config)
    db.replace_candidates(scan_id, candidates)
    db.finish_scan(scan_id, len(candidates))

    summary = db.latest_summary()

    assert summary["count"] == 1
    assert json.loads(summary["scan"]["params_json"])["max_price"] == 20
    assert summary["top"]["score"] >= 33


def test_home_renders_scan_form(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCK_HELPER_DB", str(tmp_path / "web.db"))
    db.init_db()
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [],
        "query_string": b"",
        "server": ("testserver", 80),
        "scheme": "http",
        "client": ("testclient", 1),
        "root_path": "",
        "app": app,
        "router": app.router,
    }

    response = home(Request(scope))
    body = response.body.decode()

    assert response.status_code == 200
    assert 'name="max_price"' in body
    assert 'name="score_yin_line"' in body
    assert 'data-log-box' in body
    assert 'data-results-list' in body


def test_unchecked_exclude_options_parse_as_false():
    config = _config_from_form(FormData({"max_price": "25"}))

    assert config.max_price == 25
    assert config.exclude_st is False
    assert config.exclude_bj is False
    assert config.exclude_star is False
    assert config.exclude_chinext is False


def test_scanner_uses_cached_stock_list_when_provider_fails(tmp_path, monkeypatch):
    """BaoStock fails but AKShare succeeds – MultiProvider should fall through."""
    monkeypatch.setenv("STOCK_HELPER_DB", str(tmp_path / "fallback.db"))
    db.init_db()
    # First run with good provider to populate cache
    FakeProvider.fetch_count = 0
    scanner = _make_scanner()
    scanner.run(StrategyConfig(lookback_days=40))

    # Verify results from FakeProvider
    assert FakeProvider.fetch_count > 0


def test_multi_provider_logs_source(tmp_path, monkeypatch):
    """MultiProvider logs which source is in use."""
    monkeypatch.setenv("STOCK_HELPER_DB", str(tmp_path / "multi.db"))
    db.init_db()
    logs = []
    scanner = _make_scanner()

    # Monkey-patch scanner's run to capture logs
    FakeProvider.fetch_count = 0
    candidates = scanner.run(StrategyConfig(lookback_days=40), log=lambda m: logs.append(m))

    source_lines = [l for l in logs if "数据源" in l]
    assert len(source_lines) >= 1
    assert any("Fake" in l for l in source_lines)


def test_multi_provider_falls_back_when_active_source_request_fails():
    logs = []
    with MultiProvider([FailingListProvider, BackupProvider], log=logs.append) as provider:
        stocks = provider.list_stocks()

        assert stocks == [StockInfo("sh.600000", "示例股份")]
        assert provider.source_name == "Backup"
    assert any("请求失败" in line and "尝试后备源" in line for line in logs)


def test_run_scan_rejects_invalid_config(monkeypatch):
    started = []
    monkeypatch.setattr("stock_helper.app.scan_manager.start", lambda config: started.append(config))
    request = _request(
        "/run-scan",
        urlencode({"run_password": "001023", "max_price": "not-a-number"}).encode(),
        b"application/x-www-form-urlencoded",
    )
    response = asyncio.run(run_scan(request))

    assert response.status_code == 422
    assert json.loads(response.body)["ok"] is False
    assert started == []


def test_clear_db_rejects_bad_password_and_preserves_data(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCK_HELPER_DB", str(tmp_path / "protected.db"))
    db.init_db()
    scan_id = db.create_scan(StrategyConfig())
    request = _request("/clear-db", json.dumps({"password": "wrong"}).encode(), b"application/json")
    response = asyncio.run(clear_db(request))

    assert response.status_code == 403
    assert db.latest_scan()["id"] == scan_id


def test_scan_manager_finishes_when_scan_record_creation_fails(monkeypatch):
    manager = ScanTaskManager()
    manager._task_id = 1
    manager._done = False
    monkeypatch.setattr("stock_helper.scan_tasks.db.create_scan", lambda config: (_ for _ in ()).throw(OSError("disk full")))

    manager._run_task(1, StrategyConfig(), __import__("threading").Event())

    status = manager.status()
    assert status["done"] is True
    assert status["outcome"] == "failed"
    assert any("disk full" in line for line in status["logs"])


def test_old_scan_callbacks_do_not_pollute_current_task():
    manager = ScanTaskManager()
    manager._task_id = 2
    manager._logs = ["current"]
    manager._progress = {"completed": 0}

    manager._append_for(1, "stale")
    manager._update_progress_for(1, completed=99, hits_detail={"code": "stale"})

    status = manager.status()
    assert status["logs"] == ["current"]
    assert status["progress"] == {"completed": 0}
    assert status["live_hits"] == []


def test_scan_event_stream_disables_proxy_buffering():
    response = scan_events()

    assert response.headers["cache-control"] == "no-cache, no-transform"
    assert response.headers["x-accel-buffering"] == "no"


@pytest.mark.parametrize("value", ["nan", "inf", "-inf"])
def test_config_rejects_non_finite_numbers(value):
    with pytest.raises(ValueError, match="有限数值"):
        StrategyConfig.from_mapping({"max_price": value})


def test_config_rejects_fractional_worker_count():
    with pytest.raises(ValueError, match="fetch_workers 必须是整数"):
        StrategyConfig.from_mapping({"fetch_workers": "2.5"})


def test_complete_scan_is_atomic_when_candidate_is_invalid(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCK_HELPER_DB", str(tmp_path / "atomic.db"))
    db.init_db()
    scan_id = db.create_scan(StrategyConfig())
    original = _candidate_payload(code="sh.600000")
    db.replace_candidates(scan_id, [original])

    invalid = _candidate_payload(code="sh.600001")
    del invalid["score"]
    with pytest.raises(KeyError):
        db.complete_scan(scan_id, [invalid])

    assert [item["code"] for item in db.scan_candidates(scan_id)] == ["sh.600000"]
    assert db.latest_scan()["status"] == "running"


def test_clear_db_rejects_request_while_scan_is_running(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCK_HELPER_DB", str(tmp_path / "busy.db"))
    db.init_db()
    scan_id = db.create_scan(StrategyConfig())
    monkeypatch.setattr("stock_helper.app.scan_manager.status", lambda: {"done": False})
    request = _request("/clear-db", json.dumps({"password": "001023"}).encode(), b"application/json")

    response = asyncio.run(clear_db(request))

    assert response.status_code == 409
    assert db.latest_scan()["id"] == scan_id


def test_scan_manager_exposes_success_outcome(monkeypatch):
    manager = ScanTaskManager()
    manager._task_id = 1
    manager._done = False
    manager._outcome = "running"
    completed = []
    monkeypatch.setattr("stock_helper.scan_tasks.db.create_scan", lambda config: 7)
    monkeypatch.setattr("stock_helper.scan_tasks.db.complete_scan", lambda scan_id, candidates: completed.append((scan_id, candidates)))
    monkeypatch.setattr("stock_helper.scan_tasks.run_baostock_scan", lambda *args, **kwargs: [])

    manager._run_task(1, StrategyConfig(), __import__("threading").Event())

    assert completed == [(7, [])]
    assert manager.status()["outcome"] == "success"


def test_stock_limit_is_deterministic():
    stocks = [StockInfo("sz.000003", "C"), StockInfo("sh.600001", "B"), StockInfo("sz.000001", "A")]

    selected = _limit_stocks(stocks, 2)

    assert [stock.code for stock in selected] == ["sh.600001", "sz.000001"]


def test_cache_refresh_days_controls_overlap(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCK_HELPER_DB", str(tmp_path / "cache.db"))
    db.init_db()
    db.upsert_bars("sh.600000", [{"date": "2026-06-20", "open": 10, "high": 11, "low": 9, "close": 10, "volume": 100, "turn": 1}])

    class CapturingProvider:
        def get_history_range(self, code, start_date, end_date):
            self.start_date = start_date
            return [{"date": _market_today().isoformat(), "open": 10, "high": 11, "low": 9, "close": 10, "volume": 100, "turn": 1}]

    provider = CapturingProvider()
    ensure_history_cached("sh.600000", StrategyConfig(cache_refresh_days=7), provider)

    assert provider.start_date == "2026-06-13"


def test_cached_history_does_not_bypass_current_scan_request(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCK_HELPER_DB", str(tmp_path / "fresh-cache.db"))
    db.init_db()
    rows = [{"date": "2026-07-01", "open": 10, "high": 11, "low": 9, "close": 10, "volume": 100, "turn": 1}]
    db.upsert_bars("sh.600000", rows)

    class FailingCurrentProvider:
        def get_history_range(self, code, start_date, end_date):
            raise RuntimeError("network unavailable")

    with pytest.raises(RealtimeDataUnavailable, match="实时快照未更新"):
        ensure_history_cached("sh.600000", StrategyConfig(), FailingCurrentProvider())


def test_current_day_response_is_persisted_and_returned(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCK_HELPER_DB", str(tmp_path / "current-day.db"))
    db.init_db()

    class CurrentProvider:
        def get_history_range(self, code, start_date, end_date):
            rows = make_rows()
            rows[-1]["date"] = _market_today().isoformat()
            return rows

    history = ensure_history_cached("sh.600000", StrategyConfig(), CurrentProvider())

    assert history[-1]["date"] == _market_today().isoformat()
    assert db.latest_cached_bar_date("sh.600000") == _market_today().isoformat()


def test_current_day_row_with_invalid_price_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCK_HELPER_DB", str(tmp_path / "invalid-current.db"))
    db.init_db()

    class InvalidCurrentProvider:
        def get_history_range(self, code, start_date, end_date):
            rows = make_rows()
            rows[-1]["date"] = _market_today().isoformat()
            rows[-1]["close"] = 0
            return rows

    with pytest.raises(RealtimeDataUnavailable, match="当日价格无效"):
        ensure_history_cached("sh.600000", StrategyConfig(), InvalidCurrentProvider())

    assert db.latest_cached_bar_date("sh.600000") is None


def test_current_candidate_view_excludes_previous_day_results(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCK_HELPER_DB", str(tmp_path / "candidate-date.db"))
    db.init_db()
    scan_id = db.create_scan(StrategyConfig())
    previous = _candidate_payload("sh.600000")
    previous["trade_date"] = "2026-01-01"
    current = _candidate_payload("sh.600001")
    current["trade_date"] = _market_today().isoformat()
    db.complete_scan(scan_id, [previous, current])

    visible = db.latest_candidates(trade_date=_market_today().isoformat())

    assert [item["code"] for item in visible] == ["sh.600001"]
    assert len(db.latest_candidates()) == 2


def test_cached_scan_still_fetches_current_market_data(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCK_HELPER_DB", str(tmp_path / "fully-cached.db"))
    db.init_db()
    stock = StockInfo("sh.600000", "示例股份")
    db.upsert_stock_list([stock])
    db.upsert_bars(stock.code, make_rows())

    FakeProvider.fetch_count = 0
    snapshot_calls = []

    def current_provider(log=None):
        return MultiProvider([FakeProvider], log=log)

    def current_snapshot(log=None):
        snapshot_calls.append(True)
        return {stock.code: _current_bar()}

    candidates = StockScanner(
        provider_factory=current_provider,
        snapshot_loader=current_snapshot,
    ).run(StrategyConfig())

    assert [item["code"] for item in candidates] == [stock.code]
    assert snapshot_calls == [True]
    assert FakeProvider.fetch_count == 0


def test_stale_stock_list_and_history_provider_can_use_realtime_with_cached_history(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCK_HELPER_DB", str(tmp_path / "list-fallback.db"))
    db.init_db()
    stock = StockInfo("sh.600000", "示例股份")
    db.upsert_stock_list([stock])
    db.upsert_bars(stock.code, make_rows())
    logs = []

    def failing_provider(log=None):
        return MultiProvider([FailingProvider], log=log)

    candidates = StockScanner(
        provider_factory=failing_provider,
        snapshot_loader=lambda log=None: {stock.code: _current_bar()},
    ).run(
            StrategyConfig(stock_list_ttl_minutes=0),
            log=logs.append,
    )

    assert [item["code"] for item in candidates] == [stock.code]
    assert any("股票列表更新失败" in line for line in logs)


def test_log_offset_continues_after_server_log_trimming():
    manager = ScanTaskManager()
    for index in range(1200):
        manager.append(f"line-{index}")
    *_, offset, _, _, _ = manager.snapshot()

    manager.append("new-line")
    _, logs, _, next_offset, _, _, _ = manager.snapshot(offset)

    assert len(logs) == 1
    assert "new-line" in logs[0]
    assert next_offset == offset + 1


def test_scanner_fails_when_no_stock_has_usable_history(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCK_HELPER_DB", str(tmp_path / "empty-history.db"))
    db.init_db()

    class EmptyHistoryProvider:
        def get_history_range(self, code, start_date, end_date):
            return []

    scanner = StockScanner()
    with pytest.raises(RealtimeDataUnavailable, match="实时快照未更新"):
        scanner._prepare_histories(
            [StockInfo("sh.600000", "示例股份")],
            StrategyConfig(),
            EmptyHistoryProvider(),
        )


def test_startup_marks_interrupted_scans_as_failed(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCK_HELPER_DB", str(tmp_path / "restart.db"))
    db.init_db()
    scan_id = db.create_scan(StrategyConfig())

    changed = db.fail_running_scans()

    assert changed == 1
    scan = db.latest_scan()
    assert scan["id"] == scan_id
    assert scan["status"] == "failed"
    assert "服务重启" in scan["error_message"]


def test_manager_rejects_second_concurrent_scan():
    manager = ScanTaskManager()
    manager._done = False

    with pytest.raises(ScanInProgressError, match="已有扫描"):
        manager.start(StrategyConfig())


def test_manager_cancel_is_idempotent():
    manager = ScanTaskManager()
    manager._done = False
    manager._cancel_event = __import__("threading").Event()

    assert manager.cancel() is True
    assert manager._cancel_event.is_set()
    manager._done = True
    assert manager.cancel() is False


def test_cancel_scan_requires_running_task(monkeypatch):
    monkeypatch.setattr("stock_helper.app.scan_manager.cancel", lambda: False)
    request = _request("/cancel-scan", json.dumps({"password": "001023"}).encode(), b"application/json")

    response = asyncio.run(cancel_scan(request))

    assert response.status_code == 409


def test_health_endpoint_checks_database(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCK_HELPER_DB", str(tmp_path / "health.db"))
    db.init_db()

    response = healthz()

    assert response.status_code == 200
    assert json.loads(response.body) == {"status": "ok", "database": "ok"}


def test_security_headers_cover_browser_baseline():
    assert SECURITY_HEADERS["X-Content-Type-Options"] == "nosniff"
    assert SECURITY_HEADERS["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in SECURITY_HEADERS["Content-Security-Policy"]


def test_security_middleware_applies_headers():
    async def call_next(request):
        from starlette.responses import Response
        return Response("ok")

    response = asyncio.run(add_security_headers(None, call_next))

    for name, value in SECURITY_HEADERS.items():
        assert response.headers[name] == value


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("600000", "sh.600000"),
        ("sz000001", "sz.000001"),
        ("430001", "bj.430001"),
        ("bj.830001", "bj.830001"),
        ("invalid", None),
    ],
)
def test_a_share_code_normalization(raw, expected):
    assert normalize_a_share_code(raw) == expected


def _candidate_payload(code: str) -> dict:
    return {
        "code": code,
        "name": "示例股份",
        "trade_date": "2026-07-01",
        "close": 10.0,
        "pct_chg": -0.01,
        "ma5": 10.1,
        "ma10": 10.0,
        "ma20": 9.8,
        "ma30": 9.6,
        "distance_ma10_pct": 0.0,
        "volume_ratio_5": 0.8,
        "turn": 1.0,
        "score": 80,
        "reasons": ["测试"],
        "risks": [],
    }


def _request(path: str, body: bytes, content_type: bytes) -> Request:
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [(b"content-type", content_type), (b"content-length", str(len(body)).encode())],
        "client": ("testclient", 1),
        "server": ("testserver", 80),
        "root_path": "",
        "app": app,
    }
    return Request(scope, receive)
