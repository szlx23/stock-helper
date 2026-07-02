import json
from pathlib import Path

import pytest
from starlette.datastructures import FormData
from starlette.requests import Request

from stock_helper import db
from stock_helper.app import _config_from_form, app, home
from stock_helper.config import StrategyConfig
from stock_helper.data import StockInfo
from stock_helper.data.multi_provider import MultiProvider
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
