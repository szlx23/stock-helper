from datetime import date, datetime, time, timedelta
import threading
from zoneinfo import ZoneInfo

from stock_helper import db


_lock = threading.Lock()
_trade_dates: tuple[date, ...] | None = None
_calendar_db_path: str | None = None


def refresh_trade_calendar(log=None) -> tuple[date, ...]:
    """Refresh and persist the SSE/SZSE session calendar through AKShare."""
    global _trade_dates, _calendar_db_path
    try:
        import akshare as ak

        frame = ak.tool_trade_date_hist_sina()
        values = sorted({str(value)[:10] for value in frame["trade_date"].tolist()})
        if not values:
            raise RuntimeError("交易日历为空")
        db.replace_trade_calendar(values)
        parsed = tuple(date.fromisoformat(value) for value in values)
        with _lock:
            _trade_dates = parsed
            _calendar_db_path = str(db.get_db_path().resolve())
        if log:
            log(f"交易日历已更新：{values[0]} 至 {values[-1]}")
        return parsed
    except Exception as exc:
        cached = load_cached_trade_calendar()
        if log:
            if cached:
                log(f"交易日历更新失败，使用本地缓存：{exc}")
            else:
                log(f"交易日历不可用，暂按工作日规则判断：{exc}")
        return cached


def load_cached_trade_calendar() -> tuple[date, ...]:
    global _trade_dates, _calendar_db_path
    current_db_path = str(db.get_db_path().resolve())
    with _lock:
        if _trade_dates is not None and _calendar_db_path == current_db_path:
            return _trade_dates
    try:
        parsed = tuple(date.fromisoformat(value) for value in db.cached_trade_calendar())
    except Exception:
        parsed = ()
    with _lock:
        _trade_dates = parsed
        _calendar_db_path = current_db_path
    return parsed


def latest_session_on_or_before(value: date) -> date | None:
    dates = load_cached_trade_calendar()
    for trade_date in reversed(dates):
        if trade_date <= value:
            return trade_date
    return None


def expected_market_date(now: datetime | None = None) -> date:
    """Latest session whose data is usable at the given Shanghai time."""
    shanghai = ZoneInfo("Asia/Shanghai")
    current = now or datetime.now(shanghai)
    current = current.replace(tzinfo=shanghai) if current.tzinfo is None else current.astimezone(shanghai)
    candidate = current.date()
    if current.weekday() < 5 and current.time() < time(9, 30):
        candidate -= timedelta(days=1)
    calendar_date = latest_session_on_or_before(candidate)
    if calendar_date is not None:
        return calendar_date
    while candidate.weekday() >= 5:
        candidate -= timedelta(days=1)
    return candidate
