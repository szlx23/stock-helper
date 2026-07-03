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
    if latest.get("volume_ratio_5") is not None and _f(latest.get("volume_ratio_5")) > config.burst_vol_ratio:
        rejects.append("当前阴线爆量")
    if abs(_f(latest.get("distance_ma10_pct"))) > config.near_ma10_pct:
        rejects.append("距离10日线过远")
    if _f(latest.get("ma10")) < _f(latest.get("ma20")):
        rejects.append("10日线低于20日线")
    if _f(latest.get("close")) < _f(latest.get("ma30")) * (1 - config.near_ma10_pct):
        rejects.append("收盘价明显跌破30日线")
    if _ma10_ma20_gap(latest) > config.max_ma10_ma20_gap:
        rejects.append("10日线与20日线距离过大")
    if _recent_rise(rows, 40) > config.max_recent_rise:
        rejects.append("近40日涨幅过大")
    return rejects


def score_stock(rows: list[dict], config: StrategyConfig) -> tuple[int, list[str], list[str]]:
    latest = rows[-1]
    score = 0
    reasons: list[str] = []
    risks: list[str] = []

    score += _add(_f(latest.get("close")) < _f(latest.get("open")), config.score_yin_line, "当前阴线", reasons)
    score += _add(
        latest.get("volume_ratio_5") is not None and _f(latest.get("volume_ratio_5")) <= config.shrink_vol_ratio,
        config.score_shrink_volume,
        "当前缩量或不放量",
        reasons,
    )
    score += _add(
        abs(_f(latest.get("distance_ma10_pct"))) <= config.near_ma10_pct,
        config.score_near_ma10,
        "贴近10日线",
        reasons,
    )
    score += _add(_ma_bull(latest), config.score_ma_bull, "均线多头排列", reasons)
    score += _add(_ma_short_ok(latest), config.score_ma_short_ok, "短中期结构尚可", reasons)
    score += _add(_ma10_up(rows), config.score_ma10_up, "10日线向上", reasons)
    score += _add(
        _ma10_ma20_gap(latest) <= config.max_ma10_ma20_gap,
        config.score_ma10_ma20_gap_ok,
        "10日线与20日线距离合理",
        reasons,
    )
    score += _add(_f(latest.get("close")) >= _f(latest.get("ma20")), config.score_above_ma20, "未跌破20日线", reasons)
    score += _add(_has_big_yang(rows, config), config.score_big_yang, "前期有放量大阳线", reasons)
    score += _add(_has_limit_up(rows, config), config.score_limit_up, "前期接近涨停", reasons)

    recent_rise = _recent_rise(rows, 40)
    if recent_rise > config.max_recent_rise:
        score += config.score_recent_rise_too_high
        risks.append(f"近40日低点到高点涨幅{recent_rise:.1%}")
    if _f(latest.get("close")) >= _f(latest.get("open")):
        risks.append("当前不是阴线")
    if _f(latest.get("close")) < _f(latest.get("ma20")):
        risks.append("收盘价跌破20日线")
    if _f(latest.get("ma10")) < _f(latest.get("ma20")):
        risks.append("10日线低于20日线")
    if latest.get("volume_ratio_5") and _f(latest.get("volume_ratio_5")) > config.shrink_vol_ratio:
        risks.append("当前未明显缩量")
    if not _ma_bull(latest):
        risks.append("均线尚未完全多头排列")
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


def _ma10_up(rows: list[dict]) -> bool:
    return len(rows) >= 2 and rows[-1].get("ma10") is not None and rows[-2].get("ma10") is not None and _f(rows[-1]["ma10"]) > _f(rows[-2]["ma10"])


def _ma10_ma20_gap(row: dict) -> float:
    ma20 = _f(row.get("ma20"))
    if ma20 == 0:
        return 999.0
    return abs(_f(row.get("ma10")) / ma20 - 1)


def _has_big_yang(rows: list[dict], config: StrategyConfig) -> bool:
    for row in rows[-40:-1]:
        op = _f(row.get("open"))
        if op == 0:
            continue
        pct = _f(row.get("close")) / op - 1
        if pct >= config.min_big_yang_pct and _f(row.get("volume_ratio_5", 0)) >= config.big_vol_multiple:
            return True
    return False


def _has_limit_up(rows: list[dict], config: StrategyConfig) -> bool:
    return any(_f(row.get("pct_chg", 0)) >= config.limit_up_pct for row in rows[-40:-1])


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
