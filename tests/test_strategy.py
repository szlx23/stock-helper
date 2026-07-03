from datetime import date, timedelta

from stock_helper.config import StrategyConfig
from stock_helper.indicators import enrich_history
from stock_helper.strategy import evaluate_stock


def make_rows(last_close=10.4, last_open=10.6, last_volume=850):
    rows = []
    start = date(2026, 1, 1)
    for idx in range(45):
        close = 9.0 + idx * 0.035
        open_price = close - 0.02
        high = close + 0.08
        low = close - 0.08
        volume = 1000
        if idx == 20:
            open_price = close * 0.95
            high = close * 1.06
            volume = 2200
        rows.append(
            {
                "date": (start + timedelta(days=idx)).isoformat(),
                "open": open_price,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
                "turn": 1.2,
            }
        )
    rows[-1]["open"] = last_open
    rows[-1]["close"] = last_close
    rows[-1]["high"] = max(last_open, last_close) + 0.05
    rows[-1]["low"] = min(last_open, last_close) - 0.05
    rows[-1]["volume"] = last_volume
    return enrich_history(rows)


def test_price_above_max_price_is_hard_rejected():
    result = evaluate_stock("sh.600000", "示例股份", make_rows(last_close=10.4), StrategyConfig(max_price=10))

    assert not result.passed
    assert any("最高股价" in risk for risk in result.risks)


def test_score_uses_custom_weights():
    rows = make_rows()
    base = evaluate_stock("sh.600000", "示例股份", rows, StrategyConfig())
    custom = evaluate_stock("sh.600000", "示例股份", rows, StrategyConfig(score_yin_line=99))

    assert base.passed
    assert custom.passed
    assert custom.score - base.score == 89


def test_bj_stock_can_be_excluded():
    result = evaluate_stock("bj.430001", "示例股份", make_rows(), StrategyConfig(exclude_bj=True))

    assert not result.passed
    assert any("北交所" in risk for risk in result.risks)


def test_star_market_stock_can_be_excluded():
    result = evaluate_stock("sh.688001", "示例股份", make_rows(), StrategyConfig(exclude_star=True))

    assert not result.passed
    assert any("科创板" in risk for risk in result.risks)


def test_chinext_stock_can_be_excluded():
    result = evaluate_stock("sz.300001", "示例股份", make_rows(), StrategyConfig(exclude_chinext=True))

    assert not result.passed
    assert any("创业板" in risk for risk in result.risks)


def test_default_scan_limit_covers_full_a_share_market():
    assert StrategyConfig().max_scan_count == 10000
