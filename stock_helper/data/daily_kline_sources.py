"""Unified public-web adapters and fallback chain for A-share daily bars."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date

import pandas as pd

from stock_helper import db
from stock_helper.data import normalize_a_share_code


COLUMNS = [
    "code", "trade_date", "open", "high", "low", "close",
    "volume", "amount", "pct_chg", "turnover", "source",
]


class DailyKlineSourceError(RuntimeError):
    pass


class AdapterUnavailable(DailyKlineSourceError):
    pass


class DailyKlineAdapter(ABC):
    source: str

    @abstractmethod
    def fetch(self, code: str, start_date: date, end_date: date, adjust: str) -> pd.DataFrame:
        raise NotImplementedError


class EastmoneyAdapter(DailyKlineAdapter):
    source = "eastmoney"

    def fetch(self, code, start_date, end_date, adjust):
        import akshare as ak

        frame = ak.stock_zh_a_hist(
            symbol=code.split(".")[-1],
            period="daily",
            start_date=start_date.strftime("%Y%m%d"),
            end_date=end_date.strftime("%Y%m%d"),
            adjust=adjust,
        )
        return _from_frame(
            frame,
            code,
            self.source,
            {"trade_date": "日期", "open": "开盘", "high": "最高", "low": "最低", "close": "收盘",
             "volume": "成交量", "amount": "成交额", "pct_chg": "涨跌幅", "turnover": "换手率"},
        )


class TencentAdapter(DailyKlineAdapter):
    source = "tencent"

    def fetch(self, code, start_date, end_date, adjust):
        import akshare as ak

        frame = ak.stock_zh_a_hist_tx(
            symbol=code.replace(".", ""),
            start_date=start_date.strftime("%Y%m%d"),
            end_date=end_date.strftime("%Y%m%d"),
            adjust=adjust,
            timeout=15,
        )
        # Tencent's AKShare wrapper exposes its sixth field as `amount`, but it
        # represents the volume-like series used by that endpoint. Preserve it
        # as volume and leave monetary amount unknown rather than mislabel it.
        if frame is not None and not frame.empty:
            frame = frame.copy()
            frame["volume"] = frame.get("amount", 0)
            frame["monetary_amount"] = 0.0
        return _from_frame(
            frame,
            code,
            self.source,
            {"trade_date": "date", "open": "open", "high": "high", "low": "low", "close": "close",
             "volume": "volume", "amount": "monetary_amount", "turnover": "turnover"},
        )


class SinaAdapter(DailyKlineAdapter):
    source = "sina"

    def fetch(self, code, start_date, end_date, adjust):
        import akshare as ak

        frame = ak.stock_zh_a_daily(
            symbol=code.replace(".", ""),
            start_date=start_date.strftime("%Y%m%d"),
            end_date=end_date.strftime("%Y%m%d"),
            adjust=adjust,
        )
        return _from_frame(
            frame,
            code,
            self.source,
            {"trade_date": "date", "open": "open", "high": "high", "low": "low", "close": "close",
             "volume": "volume", "amount": "amount", "turnover": "turnover"},
        )


class NeteaseAdapter(DailyKlineAdapter):
    source = "netease"

    def fetch(self, code, start_date, end_date, adjust):
        # TODO: add an endpoint only after its availability, adjustment
        # semantics, and anti-bot behavior can be verified by tests.
        raise AdapterUnavailable("网易日K适配器暂未配置")


class SohuAdapter(DailyKlineAdapter):
    source = "sohu"

    def fetch(self, code, start_date, end_date, adjust):
        # TODO: add an endpoint only after its availability and qfq semantics
        # can be verified. Silent unadjusted fallback would corrupt indicators.
        raise AdapterUnavailable("搜狐日K适配器暂未配置")


class LocalCacheAdapter(DailyKlineAdapter):
    source = "local_cache"

    def fetch(self, code, start_date, end_date, adjust):
        if adjust != "qfq":
            raise AdapterUnavailable("本地 daily_kline 缓存仅保存 qfq 数据")
        with db.connect() as conn:
            rows = conn.execute(
                """
                SELECT code, trade_date, open, high, low, close, volume, amount, pct_chg, turnover
                FROM daily_kline
                WHERE code = ? AND trade_date BETWEEN ? AND ?
                ORDER BY trade_date
                """,
                (code, start_date.isoformat(), end_date.isoformat()),
            ).fetchall()
        if not rows:
            raise DailyKlineSourceError(f"{code} 本地日K缓存为空")
        frame = pd.DataFrame([dict(row) for row in rows])
        frame["source"] = self.source
        return frame[COLUMNS]


DEFAULT_ADAPTERS = (
    EastmoneyAdapter,
    TencentAdapter,
    SinaAdapter,
    NeteaseAdapter,
    SohuAdapter,
    LocalCacheAdapter,
)


def fetch_daily_kline(
    code: str,
    start_date,
    end_date,
    adjust: str = "qfq",
    *,
    adapters: list[DailyKlineAdapter] | tuple[DailyKlineAdapter, ...] | None = None,
) -> pd.DataFrame:
    """Fetch normalized daily bars through public sources, then local cache."""
    normalized_code = normalize_a_share_code(code)
    if normalized_code is None:
        raise ValueError(f"无效的 A 股代码：{code}")
    start = _as_date(start_date)
    end = _as_date(end_date)
    if start > end:
        raise ValueError("start_date 不能晚于 end_date")
    if adjust not in ("", "qfq", "hfq"):
        raise ValueError("adjust 仅支持空字符串、qfq 或 hfq")
    db.init_db()

    chain = list(adapters) if adapters is not None else [factory() for factory in DEFAULT_ADAPTERS]
    failures = []
    for adapter in chain:
        try:
            frame = adapter.fetch(normalized_code, start, end, adjust)
            normalized = _validate_canonical(frame, normalized_code, adapter.source)
            normalized = normalized[
                (normalized["trade_date"] >= start.isoformat())
                & (normalized["trade_date"] <= end.isoformat())
            ].reset_index(drop=True)
            if normalized.empty:
                raise DailyKlineSourceError("指定日期范围内没有有效数据")
            return normalized
        except Exception as exc:
            failures.append(f"{adapter.source}: {exc}")
    raise DailyKlineSourceError(f"{normalized_code} 所有日K数据源均失败：" + "；".join(failures))


def _from_frame(frame, code: str, source: str, mapping: dict[str, str]) -> pd.DataFrame:
    if frame is None or frame.empty:
        raise DailyKlineSourceError("返回空数据")
    required = {mapping[name] for name in ("trade_date", "open", "high", "low", "close", "volume")}
    missing = required.difference(frame.columns)
    if missing:
        raise DailyKlineSourceError("关键字段缺失：" + ", ".join(sorted(missing)))
    output = pd.DataFrame()
    for canonical in ("trade_date", "open", "high", "low", "close", "volume", "amount", "pct_chg", "turnover"):
        source_column = mapping.get(canonical)
        output[canonical] = frame[source_column] if source_column in frame.columns else 0.0
    output.insert(0, "code", code)
    output["source"] = source
    return _validate_canonical(output, code, source)


def _validate_canonical(frame, code: str, source: str) -> pd.DataFrame:
    if frame is None or frame.empty:
        raise DailyKlineSourceError("返回空数据")
    missing = set(COLUMNS).difference(frame.columns)
    if missing:
        raise DailyKlineSourceError("统一字段缺失：" + ", ".join(sorted(missing)))
    result = frame[COLUMNS].copy()
    result["code"] = code
    result["trade_date"] = pd.to_datetime(result["trade_date"], errors="coerce").dt.strftime("%Y-%m-%d")
    numeric = ["open", "high", "low", "close", "volume", "amount", "pct_chg", "turnover"]
    for field in numeric:
        result[field] = pd.to_numeric(result[field], errors="coerce")
    result = result.dropna(subset=["trade_date", "open", "high", "low", "close", "volume"])
    result = result[(result[["open", "high", "low", "close"]] > 0).all(axis=1)]
    result = result[(result["volume"] >= 0) & (result["amount"] >= 0)]
    if result.empty:
        raise DailyKlineSourceError("没有通过字段校验的日K记录")
    if not result["pct_chg"].notna().all() or (result["pct_chg"] == 0).all():
        calculated = result["close"].pct_change(fill_method=None).mul(100)
        result["pct_chg"] = result["pct_chg"].where(result["pct_chg"].notna() & (result["pct_chg"] != 0), calculated)
    result["pct_chg"] = result["pct_chg"].fillna(0.0)
    result["source"] = source
    return result.drop_duplicates(["code", "trade_date"], keep="last").sort_values("trade_date").reset_index(drop=True)


def _as_date(value) -> date:
    if isinstance(value, date):
        return value
    text = str(value).strip().replace("/", "-")
    if len(text) == 8 and text.isdigit():
        text = f"{text[:4]}-{text[4:6]}-{text[6:]}"
    try:
        return date.fromisoformat(text[:10])
    except ValueError as exc:
        raise ValueError(f"无效日期：{value}") from exc
