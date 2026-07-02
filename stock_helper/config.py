from dataclasses import asdict, dataclass, fields


@dataclass(slots=True)
class StrategyConfig:
    max_price: float = 40.0
    near_ma10_pct: float = 0.03
    max_ma10_ma20_gap: float = 0.12
    min_big_yang_pct: float = 0.045
    big_vol_multiple: float = 1.6
    shrink_vol_ratio: float = 1.00
    burst_vol_ratio: float = 1.25
    limit_up_pct: float = 0.095
    max_recent_rise: float = 0.65
    lookback_days: int = 160
    cache_refresh_days: int = 7
    max_workers: int = 8
    max_scan_count: int = 5000
    exclude_st: bool = True
    exclude_bj: bool = True
    exclude_star: bool = True
    exclude_chinext: bool = True
    exclude_etf: bool = True

    score_yin_line: int = 10
    score_shrink_volume: int = 15
    score_near_ma10: int = 20
    score_ma_bull: int = 15
    score_ma_short_ok: int = 10
    score_ma10_up: int = 10
    score_ma10_ma20_gap_ok: int = 10
    score_above_ma20: int = 10
    score_big_yang: int = 15
    score_limit_up: int = 5
    score_recent_rise_too_high: int = -15

    @classmethod
    def from_mapping(cls, values: dict) -> "StrategyConfig":
        parsed = {}
        for item in fields(cls):
            raw = values.get(item.name, item.default)
            if item.type is bool:
                parsed[item.name] = _parse_bool(raw)
            elif item.type is int:
                parsed[item.name] = int(float(raw))
            elif item.type is float:
                parsed[item.name] = float(raw)
            else:
                parsed[item.name] = raw
        return cls(**parsed)

    def to_dict(self) -> dict:
        return asdict(self)


def _parse_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).lower() in {"1", "true", "yes", "on"}


FILTER_FIELDS = [
    ("max_price", "最高股价", "元"),
    ("near_ma10_pct", "距离10日线最大比例", ""),
    ("burst_vol_ratio", "爆量阴线阈值", ""),
    ("max_ma10_ma20_gap", "10日线和20日线最大距离", ""),
    ("max_recent_rise", "近40日最大涨幅", ""),
    ("min_big_yang_pct", "放量大阳线涨幅阈值", ""),
    ("big_vol_multiple", "放量倍数", ""),
    ("shrink_vol_ratio", "缩量/不放量阈值", ""),
    ("limit_up_pct", "接近涨停阈值", ""),
    ("lookback_days", "历史回看天数", "天"),
    ("cache_refresh_days", "缓存补最近天数", "天"),
    ("max_workers", "并行线程数", ""),
    ("max_scan_count", "分析数量上限", "只"),
]

SCORE_FIELDS = [
    ("score_yin_line", "当前阴线分数"),
    ("score_shrink_volume", "当前缩量分数"),
    ("score_near_ma10", "贴近10日线分数"),
    ("score_ma_bull", "均线多头排列分数"),
    ("score_ma_short_ok", "短中期结构尚可分数"),
    ("score_ma10_up", "10日线向上分数"),
    ("score_ma10_ma20_gap_ok", "10日线与20日线距离合理分数"),
    ("score_above_ma20", "未跌破20日线分数"),
    ("score_big_yang", "前期放量大阳线分数"),
    ("score_limit_up", "前期接近涨停分数"),
    ("score_recent_rise_too_high", "近40日涨幅过大扣分"),
]

EXCLUDE_FIELDS = [
    ("exclude_st", "排除 ST / 退市", "名称含 ST 或退市风险"),
    ("exclude_bj", "排除北交所", "bj / 8 / 4 开头"),
    ("exclude_star", "排除科创板", "688 / 689 开头"),
    ("exclude_chinext", "排除创业板", "300 / 301 开头"),
    ("exclude_etf", "排除ETF/LOF/指数", "510/159/000 等非个股"),
]
