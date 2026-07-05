from datetime import date, timedelta

from stock_helper.config import StrategyConfig
from stock_helper.indicators import enrich_history
from stock_helper.strategy import evaluate_stock


def make_rows(last_close=10.4, last_open=10.6, last_volume=850):
    rows = []
    start = date(2026, 1, 1)
    signal_close = None
    for idx in range(45):
        close = 8.5 + idx * 0.03 if signal_close is None else signal_close + (idx - 32) * 0.035
        open_price = close - 0.02
        high = close + 0.08
        low = close - 0.08
        volume = 1000
        if idx == 32:
            previous_close = rows[-1]["close"]
            close = previous_close * 1.06
            signal_close = close
            open_price = previous_close * 1.01
            high = close * 1.01
            low = open_price * 0.995
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


def test_current_yang_line_is_hard_rejected():
    result = evaluate_stock("sh.600000", "示例股份", make_rows(last_open=10.2, last_close=10.4), StrategyConfig())
    assert not result.passed
    assert "当前不是阴线" in result.risks


def test_burst_volume_yin_line_is_hard_rejected():
    rows = make_rows(last_volume=3000)
    result = evaluate_stock("sh.600000", "示例股份", rows, StrategyConfig())
    assert not result.passed
    assert "当前阴线爆量" in result.risks


def test_close_below_ma20_and_bearish_ma_are_hard_rejected():
    rows = make_rows()
    rows[-1]["close"] = rows[-1]["ma20"] * 0.99
    rows[-1]["distance_ma10_pct"] = rows[-1]["close"] / rows[-1]["ma10"] - 1
    rows[-1]["ma10"] = rows[-1]["ma20"] * 0.99
    result = evaluate_stock("sh.600000", "示例股份", rows, StrategyConfig())
    assert not result.passed
    assert "收盘价跌破20日线" in result.risks
    assert "10日线低于20日线" in result.risks


def test_missing_recent_volume_signal_is_hard_rejected():
    rows = make_rows()
    for row in rows:
        row["volume"] = 1000
    rows[-1]["volume"] = 800
    rows[-1]["volume_ratio_5"] = 0.8
    result = evaluate_stock("sh.600000", "示例股份", rows, StrategyConfig())
    assert not result.passed
    assert any("无放量大阳线" in risk for risk in result.risks)


def test_ma10_turning_down_is_hard_rejected():
    rows = make_rows()
    rows[-4]["ma10"] = rows[-1]["ma10"] * 1.01
    result = evaluate_stock("sh.600000", "示例股份", rows, StrategyConfig())
    assert not result.passed
    assert "10日线未保持向上" in result.risks


def test_candidate_reasons_describe_pullback_setup():
    result = evaluate_stock("sh.600000", "示例股份", make_rows(), StrategyConfig())
    assert result.passed
    assert "当前缩量阴线" in result.reasons
    assert "阴线回踩10日线" in result.reasons
    assert "前期出现放量大阳线" in result.reasons
