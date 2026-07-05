"""Incremental SQLite cache for adjusted A-share daily K-line data."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

from stock_helper import db
from stock_helper.data import normalize_a_share_code


DEFAULT_OVERLAP_DAYS = 3


class DailyKlineError(RuntimeError):
    """Base error for daily K-line cache operations."""


class DailyKlineFetchError(DailyKlineError):
    """Raised when the upstream source cannot provide usable data."""


class DailyKlineProvider(Protocol):
    source_name: str

    def fetch(self, code: str, start_date: date, end_date: date) -> list[dict]: ...


class FallbackDailyKlineProvider:
    source_name = "fallback_chain"

    def fetch(self, code: str, start_date: date, end_date: date) -> list[dict]:
        from stock_helper.data.daily_kline_sources import fetch_daily_kline

        frame = fetch_daily_kline(code, start_date, end_date, adjust="qfq")
        return frame.to_dict("records")


def get_daily_kline(code: str, lookback_days: int = 80) -> list[dict]:
    """Refresh one stock incrementally and return its latest daily bars.

    Existing data always overlaps the latest three cached trading dates, so
    today's/in-progress bar and recent corrections are overwritten on every
    successful call.
    """
    return DailyKlineCache().get(code, lookback_days=lookback_days)


def read_cached_daily_kline(code: str, lookback_days: int = 80) -> list[dict]:
    """Read without network access; used by the decoupled market-data viewer."""
    normalized_code = normalize_a_share_code(code)
    if normalized_code is None:
        raise ValueError(f"无效的 A 股代码：{code}")
    if not isinstance(lookback_days, int) or isinstance(lookback_days, bool) or lookback_days < 1:
        raise ValueError("lookback_days 必须是正整数")
    db.init_db()
    return _read_daily_kline(normalized_code, lookback_days)


def cache_scanner_daily_kline(code: str, rows: list[dict]) -> None:
    """Mirror scanner bars into the shared viewer cache without network I/O."""
    normalized_code = normalize_a_share_code(code)
    if normalized_code is None or not rows:
        return
    normalized_rows = []
    previous_close = None
    for row in rows:
        close = _number(row.get("close"), "close", normalized_code)
        pct_chg = ((close / previous_close) - 1) * 100 if previous_close and previous_close > 0 else 0.0
        normalized_rows.append(
            _normalize_provider_row(
                {**row, "pct_chg": pct_chg, "amount": row.get("amount", 0), "source": "scan_cache"},
                normalized_code,
                "scan_cache",
            )
        )
        previous_close = close
    _upsert_daily_kline(normalized_code, normalized_rows)


class DailyKlineCache:
    def __init__(self, provider: DailyKlineProvider | None = None, overlap_days: int = DEFAULT_OVERLAP_DAYS):
        if overlap_days < 1:
            raise ValueError("overlap_days 必须至少为 1")
        self.provider = provider or FallbackDailyKlineProvider()
        self.overlap_days = overlap_days

    def get(self, code: str, lookback_days: int = 80) -> list[dict]:
        if not isinstance(lookback_days, int) or isinstance(lookback_days, bool) or lookback_days < 1:
            raise ValueError("lookback_days 必须是正整数")
        normalized_code = normalize_a_share_code(code)
        if normalized_code is None:
            raise ValueError(f"无效的 A 股代码：{code}")

        db.init_db()
        end_date = datetime.now(ZoneInfo("Asia/Shanghai")).date()
        overlap_dates = _latest_cached_dates(normalized_code, self.overlap_days)
        if overlap_dates:
            # Use actual cached sessions rather than subtracting calendar days;
            # this remains correct across weekends and long holidays.
            start_date = date.fromisoformat(overlap_dates[-1])
        else:
            # Roughly 1.8 calendar days per session covers weekends, holidays,
            # and a safety margin without downloading the full listing history.
            calendar_span = max(260, lookback_days * 3 + 30)
            start_date = end_date - timedelta(days=calendar_span)

        rows = self.provider.fetch(normalized_code, start_date, end_date)
        if not rows:
            raise DailyKlineFetchError(f"{normalized_code} 数据源未返回可入库的日K数据")
        normalized_rows = [_normalize_provider_row(row, normalized_code, self.provider.source_name) for row in rows]
        # Local fallback is deliberately read-only: writing it back would erase
        # the original online provenance and pretend the cache was refreshed.
        if all(row["source"] == "local_cache" for row in normalized_rows):
            return _read_daily_kline(normalized_code, lookback_days, source_override="local_cache")
        _upsert_daily_kline(normalized_code, normalized_rows)
        return _read_daily_kline(normalized_code, lookback_days)


def _normalize_provider_row(row: dict, code: str, default_source: str) -> dict:
    try:
        trade_date = _normalize_date(row.get("trade_date", row.get("date")))
        values = {name: _number(row.get(name), name, code) for name in ("open", "high", "low", "close")}
        if any(value <= 0 for value in values.values()):
            raise DailyKlineFetchError(f"{trade_date} OHLC 必须大于 0")
        volume = _number(row.get("volume", 0), "volume", code)
        amount = _number(row.get("amount", 0), "amount", code)
        pct_chg = _number(row.get("pct_chg", 0), "pct_chg", code)
        turnover = _number(row.get("turnover", row.get("turn", 0)), "turnover", code)
    except (TypeError, ValueError) as exc:
        raise DailyKlineFetchError(f"{code} 日K字段格式错误：{exc}") from exc
    if volume < 0 or amount < 0:
        raise DailyKlineFetchError(f"{code} {trade_date} 成交量/成交额不能为负数")
    return {
        "code": code,
        "trade_date": trade_date,
        **values,
        "volume": volume,
        "amount": amount,
        "pct_chg": pct_chg,
        "turnover": turnover,
        "source": str(row.get("source") or default_source),
    }


def _normalize_date(value) -> str:
    if value is None or value == "":
        raise ValueError("trade_date 为空")
    if hasattr(value, "strftime"):
        text = value.strftime("%Y-%m-%d")
    else:
        text = str(value).strip()[:10].replace("/", "-")
        if len(text) == 8 and text.isdigit():
            text = f"{text[:4]}-{text[4:6]}-{text[6:]}"
    return date.fromisoformat(text).isoformat()


def _number(value, field: str, code: str) -> float:
    if value is None or value == "":
        return 0.0
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field}={value!r}") from exc
    if number != number or number in (float("inf"), float("-inf")):
        raise ValueError(f"{field} 不是有限数值")
    return number


def _latest_cached_dates(code: str, count: int) -> list[str]:
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT trade_date FROM daily_kline WHERE code = ? ORDER BY trade_date DESC LIMIT ?",
            (code, count),
        ).fetchall()
    return [row["trade_date"] for row in rows]


def _upsert_daily_kline(code: str, rows: list[dict]) -> None:
    with db.connect() as conn:
        conn.executemany(
            """
            INSERT INTO daily_kline (
                code, trade_date, open, high, low, close, volume, amount, pct_chg, turnover, source, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now', 'localtime'))
            ON CONFLICT(code, trade_date) DO UPDATE SET
                open = excluded.open,
                high = excluded.high,
                low = excluded.low,
                close = excluded.close,
                volume = excluded.volume,
                amount = excluded.amount,
                pct_chg = excluded.pct_chg,
                turnover = excluded.turnover,
                source = excluded.source,
                updated_at = datetime('now', 'localtime')
            """,
            [
                (
                    code,
                    row["trade_date"],
                    row["open"],
                    row["high"],
                    row["low"],
                    row["close"],
                    row["volume"],
                    row["amount"],
                    row["pct_chg"],
                    row["turnover"],
                    row["source"],
                )
                for row in rows
            ],
        )


def _read_daily_kline(code: str, limit: int, source_override: str | None = None) -> list[dict]:
    with db.connect() as conn:
        rows = conn.execute(
            """
            SELECT code, trade_date, open, high, low, close, volume, amount, pct_chg, turnover, source, updated_at
            FROM daily_kline
            WHERE code = ?
            ORDER BY trade_date DESC
            LIMIT ?
            """,
            (code, limit),
        ).fetchall()
    result = [dict(row) for row in reversed(rows)]
    if source_override:
        for row in result:
            row["source"] = source_override
    return result
