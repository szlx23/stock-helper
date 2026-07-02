import json
from pathlib import Path

from starlette.requests import Request

from stock_helper import db
from stock_helper.app import app, home
from stock_helper.config import StrategyConfig
from stock_helper.data.baostock_provider import StockInfo
from stock_helper.scanner import StockScanner
from tests.test_strategy import make_rows


class FakeProvider:
    def list_stocks(self):
        return [StockInfo("sh.600000", "示例股份")]

    def get_history(self, code, lookback_days):
        return make_rows()


def test_scanner_returns_candidates():
    candidates = StockScanner(FakeProvider()).run(StrategyConfig())

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
    candidates = StockScanner(FakeProvider()).run(config)
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
    assert "运行筛选" in body
