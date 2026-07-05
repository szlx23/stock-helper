from dataclasses import dataclass

from stock_helper.config import StrategyConfig


@dataclass(slots=True)
class StrategyResult:
    passed: bool
    score: int
    reasons: list[str]
    risks: list[str]


def evaluate_stock(code: str, name: str, rows: list[dict], config: StrategyConfig) -> StrategyResult:
    if not rows:
        return StrategyResult(False, 0, [], ["无行情数据"])
    hard_rejects = hard_filter(code, name, rows, config)
    if hard_rejects:
        return StrategyResult(False, 0, [], hard_rejects)
    score, reasons, risks = score_stock(rows, config)
    return StrategyResult(True, score, reasons, risks)


def hard_filter(code: str, name: str, rows: list[dict], config: StrategyConfig) -> list[str]:
    latest = rows[-1]
    rejects = []
    if config.exclude_st and ("ST" in name.upper() or "退" in name):
        rejects.append("ST或退市风险股")
    if config.exclude_bj and _is_bj_stock(code):
        rejects.append("北交所股票")
    if config.exclude_star and _is_star_stock(code):
        rejects.append("科创板股票")
    if config.exclude_chinext and _is_chinext_stock(code):
        rejects.append("创业板股票")
    if _f(latest.get("close")) > config.max_price:
        rejects.append(f"收盘价超过最高股价{config.max_price:g}")
    if _missing_ma(latest):
        rejects.append("均线数据不足")
        return rejects
    if _f(latest.get("close")) >= _f(latest.get("open")):
        rejects.append("当前不是阴线")
    volume_ratio = _f(latest.get("volume_ratio_5"))
    previous_volume = _f(rows[-2].get("volume")) if len(rows) >= 2 else 0
    current_volume = _f(latest.get("volume"))
    if volume_ratio > config.burst_vol_ratio:
        rejects.append("当前阴线爆量")
    elif not (previous_volume > 0 and current_volume < previous_volume) and not (0 < volume_ratio < 1):
        rejects.append("当前阴线未缩量")
    if abs(_f(latest.get("distance_ma10_pct"))) > config.near_ma10_pct:
        rejects.append("距离10日线过远")
    if _f(latest.get("close")) < _f(latest.get("ma20")):
        rejects.append("收盘价跌破20日线")
    if _f(latest.get("ma10")) < _f(latest.get("ma20")):
        rejects.append("10日线低于20日线")
    if _f(latest.get("ma5")) < _f(latest.get("ma20")) or _f(latest.get("ma10")) < _f(latest.get("ma30")):
        rejects.append("短期均线受20日或30日线压制")
    if _f(latest.get("ma20")) < _f(latest.get("ma30")):
        rejects.append("20日线低于30日线，趋势偏空")
    if not _ma_up(rows, "ma10", config.ma_slope_days, 0):
        rejects.append("10日线未保持向上")
    if not _ma_up(rows, "ma20", config.ma_slope_days, config.ma20_flat_tolerance):
        rejects.append("20日线向下")
    if _ma10_ma20_gap(latest) > config.max_ma10_ma20_gap:
        rejects.append("10日线与20日线距离过大")
    if _ma_gap(latest, "ma10", "ma30") > config.max_ma10_ma30_gap:
        rejects.append("10日线与30日线距离过大，疑似高位退潮")
    if _recent_rise(rows, 40) > config.max_recent_rise:
        rejects.append("近40日涨幅过大")
    if _best_big_yang(rows, config) is None:
        rejects.append(f"近{config.recent_signal_days}日无放量大阳线")
    return rejects


def score_stock(rows: list[dict], config: StrategyConfig) -> tuple[int, list[str], list[str]]:
    latest = rows[-1]
    score = 0
    reasons: list[str] = []
    risks: list[str] = []

    score += _add(True, config.score_yin_line, "当前阴线", reasons)
    volume_ratio = _f(latest.get("volume_ratio_5"))
    shrink_factor = 1.0 if volume_ratio <= 0.8 else 0.75
    score += round(config.score_shrink_volume * shrink_factor)
    reasons.append("当前缩量阴线" if volume_ratio < 1 else "当前较前一日缩量且未爆量")
    distance_ma10 = abs(_f(latest.get("distance_ma10_pct")))
    near_factor = max(0.55, 1 - distance_ma10 / max(config.near_ma10_pct, 1e-9) * 0.45)
    score += round(config.score_near_ma10 * near_factor)
    reasons.append("阴线回踩10日线")
    reasons.append("贴近10日线")
    if _f(latest.get("low")) <= _f(latest.get("ma10")) * 1.005:
        score += max(1, config.score_near_ma10 // 5)
        reasons.append("盘中触及10日线附近")
    score += _add(_ma_bull(latest), config.score_ma_bull, "均线多头排列", reasons)
    score += _add(_ma_short_ok(latest), config.score_ma_short_ok, "短中期结构尚可", reasons)
    score += _add(True, config.score_ma10_up, "MA10向上", reasons)
    score += _add(
        _ma10_ma20_gap(latest) <= config.max_ma10_ma20_gap,
        config.score_ma10_ma20_gap_ok,
        "10日线与20日线距离合理",
        reasons,
    )
    distance_ma20 = _f(latest.get("close")) / _f(latest.get("ma20")) - 1
    ma20_safety_factor = min(1.0, 0.6 + max(0.0, distance_ma20) / 0.04 * 0.4)
    score += round(config.score_above_ma20 * ma20_safety_factor)
    reasons.append("未跌破20日线")
    signal = _best_big_yang(rows, config)
    signal_strength = min(1.4, max(1.0, signal["volume_multiple"] / config.big_vol_multiple)) if signal else 1
    score += round(config.score_big_yang * signal_strength)
    reasons.append("前期出现放量大阳线")
    near_limit_up = _has_limit_up(rows, config)
    score += _add(near_limit_up, config.score_limit_up, "前期接近涨停", reasons)
    latest["recent_big_yang"] = signal is not None
    latest["recent_near_limit_up"] = near_limit_up
    latest["trend_status"] = "多头排列" if _ma_bull(latest) else "准多头上升"

    recent_rise = _recent_rise(rows, 40)
    if recent_rise > config.max_recent_rise:
        score += config.score_recent_rise_too_high
        risks.append(f"近40日低点到高点涨幅{recent_rise:.1%}")
    if not _ma_bull(latest):
        risks.append("均线为准多头结构，尚未完全多头排列")
    if distance_ma20 < 0.02:
        risks.append("收盘价接近20日线，安全垫较薄")
    return score, reasons, risks


def _add(condition: bool, points: int, label: str, reasons: list[str]) -> int:
    if condition:
        reasons.append(label)
        return points
    return 0


def _f(val) -> float:
    """Safe float conversion, returns 0.0 for None."""
    if val is None:
        return 0.0
    try:
        return float(val)
    except (TypeError, ValueError):
        return 0.0


def _missing_ma(row: dict) -> bool:
    return any(row.get(key) in (None, 0) for key in ("ma5", "ma10", "ma20", "ma30"))


def _is_bj_stock(code: str) -> bool:
    normalized = _code_number(code)
    return code.lower().startswith("bj.") or normalized.startswith("8") or normalized.startswith("4")


def _is_star_stock(code: str) -> bool:
    normalized = _code_number(code)
    return normalized.startswith("688") or normalized.startswith("689")


def _is_chinext_stock(code: str) -> bool:
    normalized = _code_number(code)
    return normalized.startswith("300") or normalized.startswith("301")


def _code_number(code: str) -> str:
    return code.split(".")[-1].lower()


def _ma_bull(row: dict) -> bool:
    return _f(row.get("ma5")) >= _f(row.get("ma10")) >= _f(row.get("ma20")) >= _f(row.get("ma30"))


def _ma_short_ok(row: dict) -> bool:
    return _f(row.get("ma5")) >= _f(row.get("ma10")) and _f(row.get("close")) >= _f(row.get("ma20"))


def _ma_up(rows: list[dict], key: str, days: int, tolerance: float) -> bool:
    if len(rows) <= days or rows[-1].get(key) is None or rows[-1 - days].get(key) is None:
        return False
    previous = _f(rows[-1 - days].get(key))
    return previous > 0 and _f(rows[-1].get(key)) >= previous * (1 - tolerance)


def _ma10_ma20_gap(row: dict) -> float:
    return _ma_gap(row, "ma10", "ma20")


def _ma_gap(row: dict, upper: str, lower: str) -> float:
    base = _f(row.get(lower))
    if base == 0:
        return 999.0
    return abs(_f(row.get(upper)) / base - 1)


def _best_big_yang(rows: list[dict], config: StrategyConfig) -> dict | None:
    start = max(5, len(rows) - config.recent_signal_days - 1)
    best = None
    for idx in range(start, len(rows) - 1):
        row = rows[idx]
        previous_close = _f(rows[idx - 1].get("close"))
        previous_volumes = [_f(item.get("volume")) for item in rows[idx - 5:idx]]
        if previous_close <= 0 or any(volume <= 0 for volume in previous_volumes):
            continue
        pct = _f(row.get("close")) / previous_close - 1
        volume_multiple = _f(row.get("volume")) / (sum(previous_volumes) / len(previous_volumes))
        if pct >= config.min_big_yang_pct and volume_multiple >= config.big_vol_multiple:
            signal = {"pct_chg": pct, "volume_multiple": volume_multiple, "date": row.get("date", "")}
            if best is None or (pct * volume_multiple) > (best["pct_chg"] * best["volume_multiple"]):
                best = signal
    return best


def _has_limit_up(rows: list[dict], config: StrategyConfig) -> bool:
    return any(_f(row.get("pct_chg", 0)) >= config.limit_up_pct for row in rows[-config.recent_signal_days - 1:-1])


def _recent_rise(rows: list[dict], days: int) -> float:
    recent = rows[-days:]
    lows = [_f(row.get("low")) for row in recent if _f(row.get("low")) > 0]
    highs = [_f(row.get("high")) for row in recent if _f(row.get("high")) > 0]
    if not lows or not highs:
        return 0.0
    low = min(lows)
    if low == 0:
        return 0.0
    return max(highs) / low - 1
