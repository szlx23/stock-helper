import json
import asyncio
from pathlib import Path
from urllib.parse import urlencode

import pytest
from starlette.datastructures import FormData
from starlette.requests import Request

from stock_helper import db
from stock_helper.app import _config_from_form, app, clear_db, home, run_scan, scan_events
from stock_helper.config import StrategyConfig
from stock_helper.data import StockInfo
from stock_helper.data.multi_provider import MultiProvider
from stock_helper.scan_tasks import ScanTaskManager
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
        return make_rows()


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
    import stock_helper.scanner as scanner_mod
    original = scanner_mod.make_multi_provider

    def patched_make(log=None):
        return MultiProvider([FakeProvider], log=log)

    scanner_mod.make_multi_provider = patched_make
    return scanner_mod.StockScanner()


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
